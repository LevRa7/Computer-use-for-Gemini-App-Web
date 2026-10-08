# Automatic updates

A node installs itself from a GitHub release, and it keeps itself current the same
way: it asks the GitHub Releases API what the newest published version is,
compares it with the version declared in `core/version.py`, downloads the matching
payload, verifies its SHA-256, swaps `core/`, `skills/` and `ops/` with a backup it
can roll back to, and restarts the agent.

Nothing has to be re-installed by hand, and nothing is installed that does not
match a published checksum.

---

## 1. The moving parts

| Piece | What it does |
| :--- | :--- |
| `core/updater.py` | The whole mechanism: check, download, verify, swap, roll back, restart. Standard library only, so it runs on a Windows box with nothing but python.org's Python, on a VPS, and on macOS. |
| `core/agent.py` | Starts a background thread (`mesh-updater`) that checks every `MESH_UPDATE_CHECK_INTERVAL`, records the answer in `agent.heartbeat`, and stops the process when a new version is installed so the helper can start it. |
| `mesh_update` (MCP tool) | Lets Gemini report state (`action=status`), look for a release (`action=check`) or install one (`action=apply`). |
| `ops/update.ps1` | The Windows wrapper: finds the interpreter the node is pinned to and the payload directory from the Startup launcher, then runs the updater. Refuses the Microsoft Store alias. |
| `bin/cli.js update` | `gemini-computer-use update [-Check] [-Force] [-Json] …` — the same thing from the CLI. |
| `AntigravityMeshUpdater` (Scheduled Task) | Runs `ops/update.ps1 -Quiet` once a day at 03:30, so a machine nobody logs into still moves forward. Registered by `install.ps1`. |
| `ops/windows/run-hidden.vbs` | Starts a node script with no console window at all; both scheduled tasks go through it. A task has no console of its own, so `powershell.exe` would be given a new one and Windows draws it before honouring `-WindowStyle Hidden` — a black window every five minutes. `wscript.exe` is a GUI host, and `WshShell.Run …, 0` hands the child `SW_HIDE`, so nothing is ever drawn. |
| `.github/workflows/release.yml` | Turns a pushed tag into a release with the assets a node needs. |

On Linux and macOS the agent runs continuously under systemd/launchd, so its own
background thread is the update check; no extra timer is registered.

---

## 2. What a release must contain

Pushing a tag is the whole procedure:

```bash
# 1. the version is declared once, in core/version.py
# 2. package.json must name the same version (the build refuses otherwise)
git tag v0.3.1 && git push origin v0.3.1
```

`.github/workflows/release.yml` then:

1. **verifies** that the tag, `core/version.py` and `package.json` agree, and runs
   the updater and version test suites;
2. **builds** `AntigravityMesh-Setup-<version>.exe` with the in-box C# compiler and
   self-tests it (`--version`, `--unpack`);
3. **builds** `mesh-payload-<version>.zip` — the archive the updater installs —
   with `tools/build-payload-zip.py`, plus its `.sha256`;
4. **publishes** all four assets on the release, with the notes from
   `docs/releases/<tag>.md` when that file exists.

`mesh-payload-<version>.zip` is built by the project's own tool rather than by
`Compress-Archive`: PowerShell writes backslashes into entry names, and Python's
`zipfile` treats a backslash as an ordinary character on POSIX — such an archive
would unpack into files literally named `core\agent.py` and the node would never
find its own code again. The tool also excludes `__pycache__`, and fixes entry
timestamps so the same tree always produces the same hash.

---

## 3. How a node picks the payload

For a release, the updater chooses the first asset that exists:

1. `mesh-payload-<version>.zip` — verified against `mesh-payload-<version>.zip.sha256`
   (or the asset's `digest`). **This is the normal path, on every platform.**
2. `AntigravityMesh-Setup-<version>.exe` — Windows only, verified against
   `<name>.exe.sha256`, then unpacked with `--unpack` and installed from there.
3. the release source archive (`tarball_url`) — **only** when
   `MESH_UPDATE_ALLOW_UNVERIFIED=1`, because GitHub publishes no checksum for it.

If the chosen asset has no published checksum and unverified payloads are not
allowed, the update is refused and the node stays on its current version. That is
deliberate: a mesh that installs whatever it downloads is a mesh that can be
poisoned once and stay poisoned.

---

## 4. How an install works, safely

```
check ──▶ download to <tmp>/….part ──▶ SHA-256 match?
                                        │ no  ──▶ delete, keep the old version, back off 1 h
                                        └ yes ──▶ extract to a staging directory
                                                   │
                            verify_payload: core/agent.py, core/mcp_tools.py,
                            core/updater.py, core/version.py, core/__init__.py, …
                                                   │
                     journal {"state":"applying"} ──▶ backup core/ skills/ ops/ ──▶ swap
                                                   │
                     journal {"state":"swapped"} ──▶ journal {"state":"applied"}
```

* **Backup.** Everything replaced is first moved to
  `~/.config/antigravity-mesh/backups/<old-version>-<timestamp>/`. The newest three
  are kept (`MESH_UPDATE_KEEP_BACKUPS`).
* **Journal.** `~/.config/antigravity-mesh/update.journal.json` is written *before*
  the swap. If the process is killed mid-swap, the next agent start calls
  `recover_pending()`: a journal left at `applying` restores the backup, one at
  `swapped` only finishes the bookkeeping. The worst case is "still on the old
  version", never "half of two versions".
* **Bytecode.** `__pycache__` directories are removed after the swap: a payload
  built from a working tree can carry bytecode whose timestamps match the sources
  it was compiled from, and Python would then run the old code under the new names.
* **Files the updater does not own** (an operator's `install-gui.strings.json`, a
  checkout's `docs/`) are left exactly as they are.

---

## 5. Restarting the node

An updated payload only matters once the *process* is new, and the old agent holds
the instance lock and the tunnel. So the updater starts a small detached helper
**before** the swap:

| Situation | What the helper does |
| :--- | :--- |
| The agent applied the update itself (background thread, `mesh_update`) | Waits for the agent's pid to exit, then restarts it through its supervisor. |
| A CLI or the scheduled task applied it while an agent was running | Takes the pid from `agent.heartbeat`, stops that agent (graceful signal, then force), then restarts. Without this the new agent would wait for the lock the old one never releases. |
| No agent was running | Starts one. |

The restart itself goes through whatever already exists:

* **Windows** — the watchdog task's script, `ops/windows/agent-watchdog.ps1`, with a
  short grace period; verified through the heartbeat, with a direct
  `python -m core.agent` start as the last resort.
* **Linux (systemd)** — `systemctl --user restart agy-agent.service` when that unit
  exists (`Restart=always` covers it either way).
* **macOS (launchd)** — `launchctl kickstart -k gui/<uid>/com.antigravity.mesh.agent`.
* Everything else — `python -u -m core.agent`, detached, output appended to
  `agent.log`.

The result of every step lands in `~/.config/antigravity-mesh/update.log` and, in
short form, in `agent.heartbeat` under `update` — which is what `ops/doctor.ps1`
prints.

---

## 6. Configuration

Every key is read from the environment, and therefore also from `agent.env`:

| Variable | Default | Meaning |
| :--- | :--- | :--- |
| `MESH_UPDATE_CHECK` | `1` | Check for releases at all. `0` disables the background thread. |
| `MESH_UPDATE_AUTO` | `1` | Install a found update automatically. `0` only reports it. |
| `MESH_UPDATE_CHECK_INTERVAL` | `21600` | Seconds between checks (minimum 300). |
| `MESH_UPDATE_CHANNEL` | `stable` | `prerelease` also considers pre-releases. |
| `MESH_UPDATE_TOKEN` | – | GitHub token. Raises the API limit from 60 to 5000 requests/hour. `GITHUB_TOKEN` and `GH_TOKEN` are honoured too. |
| `MESH_UPDATE_REPO` | `LevRa7/Computer-use-for-Gemini-App-Web` | Where releases come from. |
| `MESH_UPDATE_DIR` | the directory holding `core/` | Install directory. |
| `MESH_UPDATE_RESTART` | `1` | Restart the agent after installing. |
| `MESH_UPDATE_ALLOW_UNVERIFIED` | `0` | Accept a payload with no published checksum. Leave it off. |
| `MESH_UPDATE_KEEP_BACKUPS` | `3` | How many old versions to keep. |
| `MESH_UPDATE_DRY_RUN` | `0` | Download and verify, install nothing. |
| `MESH_UPDATE_INITIAL_DELAY` | `90` | Seconds before the agent's first check after start. |

A second instance waiting for the instance lock never updates the node: only the
agent that owns it does.

---

## 7. Doing it by hand

```powershell
# Windows: is a newer release published?
gemini-computer-use update -Check
gemini-computer-use update -Check -Json          # machine-readable

# install it now (downloads, verifies, swaps, restarts)
gemini-computer-use update
gemini-computer-use update -Force                # ignore the check interval
gemini-computer-use update -NoRestart            # swap without restarting
```

```bash
# any platform, from the node directory
python -m core.updater --status                  # local state, no network
python -m core.updater --check                   # ask GitHub
python -m core.updater --apply                   # install and restart
python -m core.updater --check --json
```

Exit codes: `0` up to date or applied, `2` an update is available and was not
installed (`--check`), `3` the action failed, `4` applied and the node is
restarting.

From Gemini, ask the node itself:

> check whether a newer version of the node is available (`mesh_update status`, then `check`)

`mesh_update` with `action=apply` downloads, verifies, installs and restarts — the
tunnel drops for a few seconds, and the tool's own answer is still delivered first.

---

## 8. Rolling back

Every applied update leaves a backup:

```powershell
# Windows
dir "$env:USERPROFILE\.config\antigravity-mesh\backups"
# the payload of the previous version is <backup>\core, <backup>\skills, <backup>\ops
```

To go back, stop the agent, copy those three directories over the install
directory, and start it again:

```powershell
powershell -File .\ops\windows\agent-watchdog.ps1 -GraceSeconds 0
```

If a swap was interrupted, do nothing: the next agent start rolls the tree back
from the journal automatically.

---

## 9. Troubleshooting

| Symptom | Cause and fix |
| :--- | :--- |
| `check failed: GitHub rate limit reached` | The unauthenticated limit is 60 requests/hour per IP. Set `MESH_UPDATE_TOKEN` (a read-only fine-grained token is enough, no scopes needed for public repos). |
| `no published SHA-256 for …; refusing` | The release has no `mesh-payload-*.zip.sha256`. Re-run the release workflow, or attach the checksum by hand. |
| `no releases found in … (HTTP 404)` | Nothing is published yet, or `MESH_UPDATE_REPO` points at the wrong repository. |
| `the payload is incomplete: missing …` | The asset is not a node payload. Check that it was built by `tools/build-payload-zip.py`. |
| The node updates but runs the old version | The agent was not restarted: look for "restart helper" lines in `update.log`, and check that the scheduled task `AntigravityMeshUpdater` exists (`schtasks /Query /TN AntigravityMeshUpdater`). |
| A node never updates although a release exists | `core/version.py` is the compared number. A release whose tag is older than the installed version is reported as "up to date" — that is correct, not a bug. |
| An update is found but not installed | `MESH_UPDATE_AUTO=0`. |
| Everything else | `ops/doctor.ps1` prints the node version and the last update answer from the heartbeat; `~/.config/antigravity-mesh/update.log` has the full history. |

---

## 10. Tests

`tests/test_updater.py` covers the mechanism without a network: real archives over
`file://` URLs (so download, SHA-256 verification and extraction run for real),
backup and rollback, recovery from an interrupted swap, the CLI, the MCP tool, and
the restart logic — including the case that matters most, a foreign agent that has
to be stopped or it will hold the instance lock forever.

```bash
python -m pytest -q tests/test_updater.py tests/test_version_single_source.py
```

`tests/test_version_single_source.py` keeps the tag, `package.json` and
`core/version.py` in agreement, which is what the release workflow verifies before
it publishes anything.
