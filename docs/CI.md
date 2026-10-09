# CI and releases

Two workflows, both in [`.github/workflows/`](../.github/workflows), and one
procedure: **pushing a tag publishes a release**. This file is the human-readable
summary; the YAML is the source of truth.

## On every push and pull request — `ci.yml`

| Job | Runner | What it proves |
| :--- | :--- | :--- |
| `tests` (matrix) | ubuntu-latest + Python 3.11, ubuntu-latest + 3.13, windows-latest + 3.13 | the updater + version gate, then the **whole suite**, on both platforms and on the oldest supported interpreter |
| `payload` | ubuntu-latest | `python tools/build-payload-zip.py` produces an archive that **unpacks into a usable node** |

Dependencies are installed explicitly (`pytest`, `pytest-asyncio`, `pydantic`,
`uvicorn`, `starlette`, `websockets`, `requests`, `httpx`, `mcp`, and `pywin32` on
Windows). Google's SDK is installed on the Windows runner only — on Linux its
harness wants a writable `/root/agy-gdrive-runner`, so the tests that need it are
documented skips there rather than failures.

## On a tag `v*` — `release.yml`

The tag push is the whole procedure. The workflow:

1. **`verify`** — the tag, `core/version.py` and `package.json` must agree (and
   `VERSION_PATTERN` must match), then `tests/test_updater.py` and
   `tests/test_version_single_source.py` run, then the payload archive is built and
   uploaded as an artifact.
2. **`windows`** — `build-installer-exe.ps1` builds
   `dist/AntigravityMesh-Setup-<version>.exe`, and the executable is **self-tested**:
   `--version` must print the version and `--unpack` must succeed.
3. **`release`** — creates (or updates) the release, takes the notes from
   `docs/releases/<tag>.md` when that file exists, and attaches exactly four assets:

| Asset | Who reads it |
| :--- | :--- |
| `mesh-payload-<version>.zip` | every node's updater (`core/updater.py`) — the Linux/updater artifact |
| `mesh-payload-<version>.zip.sha256` | the checksum the updater verifies before installing |
| `AntigravityMesh-Setup-<version>.exe` | Windows users (`irm … \| iex` is not needed for them) |
| `AntigravityMesh-Setup-<version>.exe.sha256` | anyone verifying the download by hand |

Never upload an asset by hand: the node refuses a payload whose hash does not match
the published one, and only this workflow computes both from the same tree.

## Release checklist (what a version bump touches)

1. `core/version.py` — the **single** version literal; `MAJOR.MINOR.PATCH`, an
   optional `-rc.N` suffix is allowed.
2. `package.json` — same version (the npm package and the Python node must never
   ship as two versions), and its `description` mentions the current tool count.
3. `README.md` — the copy/paste examples naming `AntigravityMesh-Setup-<version>.exe`
   (a test fails if they still advertise the previous version).
4. `docs/releases/v<version>.md` — the release notes (a test requires the file).
5. Run the gates locally: `python -m pytest -q -p no:cacheprovider tests/test_updater.py
   tests/test_version_single_source.py`.
6. **A new core module must also be added to the bootstrap list in `install.sh`** —
   a node that installs itself fetches that list, not a tree, and a module missing
   from it ships a tool whose implementation is absent (`tests/test_installer_bootstrap.py`
   fails on exactly that, after a phone install advertised `device_info` and answered
   "the device layer (core/device.py) is missing from this checkout").
7. Optional pre-flight of the artifacts: `python tools/build-payload-zip.py` and
   `./build-installer-exe.ps1` (both write to `dist/`, which is not committed).
8. Commit, then `git tag -a v<version> -m "Antigravity Mesh <version>"`, then
   `git push origin main refs/tags/v<version>` — CI does the rest.

> [!NOTE]
> **Reproducibility, measured.** The payload archive is built from sorted entries
> with fixed timestamps, so the same tree on the same host family produces the same
> bytes. Two differences were measured between a locally rebuilt archive and the
> published one, and neither is a defect:
>
> * **Line endings.** With `core.autocrlf=true` (the Windows default) the working
>   tree holds CRLF while the committed blobs — and therefore the CI checkout — hold
>   LF, so a payload built from a Windows checkout is a different archive. Commit
>   and release from CI, or set `core.autocrlf=input`.
> * **Host byte.** A zip built on Windows and one built on Linux differ in exactly
>   **one byte per entry** — the "version made by / host OS" byte of each
>   central-directory record (`0` = MS-DOS, `3` = Unix); every entry's content and
>   CRC are identical.
>
> That is why the checksum a node verifies is always the one computed **from the
> published archive**, never from a locally rebuilt copy.

## After the release: the served installers

`install.sh` / `install.ps1` (the `curl … | bash` and `npx` one-liners) and the
bootstrap payload are served by the gateway, not by GitHub. Publishing them is a
separate, verified step:

```bash
# what it does, without touching the host
MESH_GATEWAY_SSH=root@<gateway> MESH_PUBLIC_URL=https://<domain> ./deploy_gateway.sh --dry-run

# app dir  : gateway.py            (the tool surface the client sees)
# www dir  : install.sh install.ps1 (the served installers)
# plus the node code the bootstrap download serves (core/*.py, skills/*.md,
# ops/windows/agent-watchdog.ps1, ops/doctor.ps1, ops/update.ps1)
MESH_GATEWAY_SSH=root@<gateway> MESH_PUBLIC_URL=https://<domain> ./deploy_gateway.sh
```

It uploads to a staging directory, verifies an sha256 manifest **on the host**,
backups everything it replaces, installs atomically with the right owner, restarts
the service and finishes with a public health check. A node that installs from the
domain after this step gets the released code; before it, it gets the previous one.
