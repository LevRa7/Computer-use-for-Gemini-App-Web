"""Both installers must record the domain they resolved, not only use it once.

The incident this covers: a Termux node installed with only ``agent.env``
(``MESH_GATEWAY`` / ``MESH_USER`` / ``MESH_TOKEN``) dialled the right gateway -
``gateway_host()`` does honour the legacy ``MESH_GATEWAY`` - while minting its
share links on ``core/domain.py``'s built-in default. That resolver reads
``MESH_PUBLIC_URL`` from the environment, then the domain file, then the built-in
default, and it never reads ``MESH_GATEWAY``: the node tunnelled to the gateway it
was registered on and published links on the default relay.

Both installers resolve the domain anyway, so both write it into the domain file
as well: the place that decides the domain is then also the place that records it,
and the node's links, its tunnel and the gateway agree by construction.

``tests/test_installer_windows.py`` already exercises ``Write-DomainFile`` out of
install.ps1's own AST, so this module does not repeat that behaviour. What it adds
is the source-level contract for both installers - the call sites and their
position relative to the file whose contents the domain must outlive - and a real
run of ``install.sh``'s writer under bash.
"""

import os
import shutil
import subprocess

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTALL_SH = os.path.join(REPO, "install.sh")
INSTALL_PS1 = os.path.join(REPO, "install.ps1")

#: The one way install.sh hands its resolved domain to the writer. Counting it is
#: how "both call sites exist and no third one was added" is pinned.
SH_CALL = 'write_domain_file "$GATEWAY"'

#: The same statement in install.ps1.
PS1_CALL = "Write-DomainFile -Domain $Gateway -Path $DomainFilePath"

#: Example hosts, deliberately not the project default declared in core/domain.py.
SAMPLE_HOST = "mesh.example.test"
OTHER_HOST = "tunnel.example.test"

#: The four functions install.sh's writer is built from, in dependency order.
WRITER_FUNCTIONS = ("normalise_host", "strip_bom", "domain_file_value",
                    "module_default_domain", "write_domain_file")


def _read_sh() -> str:
    with open(INSTALL_SH, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()


def _read_ps1() -> str:
    # install.ps1 carries a UTF-8 BOM (Windows PowerShell 5.1 needs it to decode
    # the Russian strings), and every match below is on text without the mark.
    with open(INSTALL_PS1, "r", encoding="utf-8-sig") as handle:
        return handle.read()


def _line_numbers(lines, needle: str):
    """Numbers of the lines whose stripped text is exactly *needle*."""
    return [number for number, line in enumerate(lines) if line.strip() == needle]


def _only_line(lines, needle: str, where: str) -> int:
    """Index of the single line whose stripped text is exactly *needle*."""
    hits = _line_numbers(lines, needle)
    assert len(hits) == 1, (
        "%s: expected exactly one %r line, found %d" % (where, needle, len(hits)))
    return hits[0]


def _offset(text: str, needle: str, where: str) -> int:
    """Offset of *needle* in *text*, with a failure message that names the file."""
    position = text.find(needle)
    assert position != -1, "%s no longer contains %r" % (where, needle)
    return position


def _bash() -> str:
    """A usable bash: PATH on POSIX, Git for Windows' bash on Windows."""
    if os.name != "nt":
        return shutil.which("bash") or ""
    try:
        from core import mcp_tools

        return mcp_tools._git_bash_windows() or ""
    except Exception:
        return ""


BASH = _bash()
needs_bash = pytest.mark.skipif(not BASH, reason="bash is required for the shell-side checks")


# ---------------------------------------------------------------------------
# Source-level: install.sh writes the domain where it decided it
# ---------------------------------------------------------------------------

def test_install_sh_records_the_domain_at_both_call_sites():
    """Two call sites: the standalone branch and the tunnel branch after agent.env.

    A node that never writes the domain keeps publishing on the built-in default,
    so both branches that build public links have to persist it - and the tunnel
    one has to do it after agent.env exists, because that file is what carries the
    legacy MESH_GATEWAY the writer is compensating for.
    """
    lines = _read_sh().splitlines()

    definition = _only_line(lines, "write_domain_file() {", "install.sh")
    calls = _line_numbers(lines, SH_CALL)
    assert len(calls) == 2, (
        "install.sh must call %s exactly twice (standalone and tunnel), found %d"
        % (SH_CALL, len(calls)))
    standalone, tunnel = calls
    assert definition < standalone, "the writer is called before it is defined"

    # Standalone: the branch creates $CONFIG_DIR before it writes anything there.
    branches = _line_numbers(lines, 'if [ "$MODE" = "standalone" ]; then')
    assert branches, "install.sh no longer opens a standalone branch"
    branch = min(branches)
    created = _line_numbers(lines, 'mkdir -p "$CONFIG_DIR"')
    assert branch < standalone and any(branch < number < standalone
                                       for number in created), (
        "the standalone call site must come after the branch's mkdir -p \"$CONFIG_DIR\"")

    # Tunnel: agent.env is written, locked down, and only then is the domain
    # recorded next to it.
    heredoc = _line_numbers(lines, 'cat << EOF > "$CONFIG_FILE"')
    chmod = _line_numbers(lines, 'chmod 600 "$CONFIG_FILE"')
    assert heredoc, "install.sh no longer writes agent.env"
    assert chmod, "agent.env must be chmod 600"
    assert min(heredoc) < max(chmod) < tunnel, (
        "the tunnel call site must come after agent.env is written and chmod 600")


# ---------------------------------------------------------------------------
# Source-level: install.ps1 writes the domain that resolved the tunnel
# ---------------------------------------------------------------------------

def test_install_ps1_records_the_domain_after_agent_env():
    """The Windows writer runs between agent.env and the agent that reads it."""
    text = _read_ps1()

    assert "function Write-DomainFile {" in text, "install.ps1 must define the writer"
    assert "$Domain = Get-NormalisedHost $Domain" in text, (
        "the writer must normalise its argument: a scheme or a trailing slash "
        "would be written verbatim as MESH_PUBLIC_URL=https://https://host/")
    assert text.count(PS1_CALL) == 1, (
        "install.ps1 must call %r exactly once, found %d" % (PS1_CALL, text.count(PS1_CALL)))

    heredoc = _offset(text, '"@ | Out-File -FilePath $envFile -Encoding utf8', "install.ps1")
    call = _offset(text, PS1_CALL, "install.ps1")
    started = _offset(text, "# Start agent in background", "install.ps1")
    assert heredoc < call < started, (
        "the domain file must be written after agent.env and before the agent is "
        "started from it")


# ---------------------------------------------------------------------------
# Behaviour: install.sh's own writer, run under bash
# ---------------------------------------------------------------------------

def _function_source(text: str, name: str) -> str:
    """The exact text of ``name() {`` up to the first following line that is ``}``."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line == "%s() {" % name:
            for end in range(index + 1, len(lines)):
                if lines[end] == "}":
                    return "\n".join(lines[index:end + 1])
            raise AssertionError("install.sh: %s() { is never closed" % name)
    raise AssertionError("install.sh no longer defines %s() {" % name)


def _writer_source() -> str:
    """The four writer functions plus a no-op privilege helper, then the call.

    The host arrives as ``$1`` and every path through the environment, so no path
    is ever interpolated into the script text.
    """
    parts = [_function_source(_read_sh(), name) for name in WRITER_FUNCTIONS]
    # Root is not needed to prove the contract, and sudo does not exist in Git for
    # Windows' bash: running the command directly is what the function does as root.
    parts.append('run_privileged() { "$@"; }')
    parts.append('write_domain_file "$1"')
    return "\n".join(parts)


def _posix(path) -> str:
    """A path bash can use: Git for Windows' MSYS tools read ``C:/...``, not ``C:\\...``."""
    return str(path).replace("\\", "/")


def _run_writer(directory, host: str) -> subprocess.CompletedProcess:
    """Run install.sh's writer with the domain file at ``<directory>/domain.env``."""
    environment = dict(os.environ)
    environment.update({
        "DEFAULT_DOMAIN_DIR": _posix(directory),
        "DEFAULT_DOMAIN_FILE": _posix(directory) + "/domain.env",
        "DOMAIN_PLACEHOLDER": "__MESH_DOMAIN__",
        "LANG_CHOICE": "en",
        # module_default_domain() reads core/domain.py from the checkout, which is
        # how the writer recognises the built-in default it must refuse to record.
        "SCRIPT_DIR": _posix(REPO),
        "YELLOW": "", "GREEN": "", "CYAN": "", "RESET": "",
    })
    return subprocess.run(
        [BASH, "-c", _writer_source(), "domain-writer", host],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=environment, timeout=60)


def _domain_bytes(directory) -> bytes:
    path = directory / "domain.env"
    assert path.exists(), "install.sh's writer did not create %s" % path
    return path.read_bytes()


def _expected(host: str) -> bytes:
    return ("MESH_PUBLIC_URL=https://%s\n" % host).encode("utf-8")


@needs_bash
def test_bash_write_domain_file_contract(tmp_path):
    """Created, normalised, idempotent, overwritten, and silent for a non-domain."""
    # 1. Created: a bare host lands as exactly one MESH_PUBLIC_URL line, LF
    #    terminated and BOM-less, so every reader (core/domain.py, sed, grep)
    #    sees the same value.
    created = tmp_path / "created"
    result = _run_writer(created, SAMPLE_HOST)
    assert result.returncode == 0, result.stderr
    assert _domain_bytes(created) == _expected(SAMPLE_HOST), (
        "the file must hold exactly the resolved domain: %r" % _domain_bytes(created))

    # 2. Normalised: the same host spelled with a scheme and a trailing slash is
    #    the same file. Verbatim it would read "https://https://host/".
    normalised = tmp_path / "normalised"
    result = _run_writer(normalised, "https://%s/" % SAMPLE_HOST)
    assert result.returncode == 0, result.stderr
    assert _domain_bytes(normalised) == _expected(SAMPLE_HOST), (
        "the argument must be normalised before it reaches the file: %r"
        % _domain_bytes(normalised))

    # 3. Idempotent: a line appended by hand survives, which is what proves the
    #    writer did not rewrite a file that already resolves to the same host.
    marker = _expected(SAMPLE_HOST) + b"# keep\n"
    (created / "domain.env").write_bytes(marker)
    result = _run_writer(created, SAMPLE_HOST)
    assert result.returncode == 0, result.stderr
    assert _domain_bytes(created) == marker, (
        "a file that already resolves to the same host must be left untouched: %r"
        % _domain_bytes(created))

    # 4. Overwritten: an explicit --domain=/MESH_PUBLIC_URL outranks the recorded
    #    value, marker and all.
    result = _run_writer(created, OTHER_HOST)
    assert result.returncode == 0, result.stderr
    assert _domain_bytes(created) == _expected(OTHER_HOST), (
        "a different host must replace the recorded one: %r" % _domain_bytes(created))

    # 5. Nothing at all for the placeholder or an empty value: those mean "no
    #    domain was resolved", and a file holding one would be worse than none.
    for label, value, name in (("the placeholder", "__MESH_DOMAIN__", "placeholder"),
                               ("an empty value", "", "empty")):
        skipped = tmp_path / ("skipped-" + name)
        result = _run_writer(skipped, value)
        assert result.returncode == 0, result.stderr
        assert not (skipped / "domain.env").exists(), (
            "%s must not create a domain file" % label)

    # 6. ...and neither of them touches a file that already exists: a file that
    #    holds a real domain is not emptied by a run that resolved none.
    (created / "domain.env").write_bytes(marker)
    for value in ("__MESH_DOMAIN__", ""):
        result = _run_writer(created, value)
        assert result.returncode == 0, result.stderr
    assert _domain_bytes(created) == marker, (
        "the placeholder and an empty value must not modify an existing domain file")


@needs_bash
def test_bash_write_domain_file_never_fails_an_install(tmp_path):
    """An unwritable directory only prints a hint; the install goes on.

    A node whose domain file cannot be written still works - the legacy
    MESH_GATEWAY keeps the tunnel where it was - so a failed write must return
    success and tell the operator how to record the domain by hand.
    """
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory\n", encoding="ascii")

    result = _run_writer(blocker, SAMPLE_HOST)

    assert result.returncode == 0, (
        "a failed write must not abort the installer: %s" % result.stderr)
    assert "Could not write the domain" in result.stdout, result.stdout
    assert not (blocker / "domain.env").exists()


@needs_bash
def test_bash_write_domain_file_refuses_the_built_in_default(tmp_path):
    """The project default is a fallback, not a configuration.

    Recording it would turn "nothing is configured" into a pinned domain file, and
    from then on core/domain.py stops consulting the legacy MESH_GATEWAY at all -
    an operator who re-points agent.env would be silently ignored. Skipping the
    write costs nothing: with no file the resolver's last fallback IS that value,
    so the node's links and its tunnel still name the same host.
    """
    from core import domain

    default_host = domain.DEFAULT_PUBLIC_BASE_URL.split("//", 1)[-1].rstrip("/")
    directory = tmp_path / "default"

    result = _run_writer(directory, default_host)
    assert result.returncode == 0, result.stderr
    assert not (directory / "domain.env").exists(), (
        "the built-in default must never be recorded in the domain file")

    # A real domain still lands, in the very same directory.
    result = _run_writer(directory, SAMPLE_HOST)
    assert result.returncode == 0, result.stderr
    assert _domain_bytes(directory) == _expected(SAMPLE_HOST)


def test_install_sh_hands_the_domain_to_an_ssh_target():
    """The remote branch resolves the domain here and installs there.

    It must pass the value on: without ``--domain`` the remote copy re-resolves
    from its own environment and, on a repository copy whose ``PUBLISHED_DOMAIN``
    is still the placeholder, falls back to the built-in default - which the target
    would then record as its own domain.
    """
    remote = [line for line in _read_sh().splitlines()
              if "bash install.sh --quick --lang=$LANG_CHOICE" in line]
    assert len(remote) == 1, remote
    assert "$REMOTE_DOMAIN_ARG" in remote[0], (
        "the SSH branch must hand the resolved domain to the remote installer")
    assert '--domain=$GATEWAY' in _read_sh()
