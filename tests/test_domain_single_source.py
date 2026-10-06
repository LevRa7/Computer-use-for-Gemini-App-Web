"""The domain must come from ONE place - for the shell side too.

These tests pin the property that makes the domain switchable by editing a single
value: no shipped script, template or config carries a domain literal, and the
shell resolver (ops/mesh-domain.sh, used by deploy_gateway.sh and the nginx
renderer) agrees with core/domain.py.
"""

import os
import shutil
import subprocess

import pytest

from core import domain

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: The project's default authority, derived from the single declaration so this
#: test keeps working if that default ever changes.
DEFAULT_HOST = domain.DEFAULT_PUBLIC_BASE_URL.split("//", 1)[-1].rstrip("/")

#: Files that must never name a domain: only the placeholder, or nothing at all.
NO_LITERAL_FILES = (
    "install.sh",
    "install.ps1",
    "install-gui.ps1",
    "install-gui.strings.json",
    "install-gui.cmd",
    "build-installer-exe.ps1",
    "tools/installer-exe/Launcher.cs",
    "deploy_gateway.sh",
    "ops/mesh-domain.sh",
    "ops/nginx/render-domain.sh",
    "ops/nginx/mesh-domain.vhost.template",
    "ops/nginx/antigravity-mesh-mcp.conf",
)

PLACEHOLDER_FILES = (
    "install.sh",
    "install.ps1",
    "ops/nginx/mesh-domain.vhost.template",
)


def _read(relative: str) -> str:
    with open(os.path.join(REPO, relative), "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()


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
# The audit
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("relative", NO_LITERAL_FILES)
def test_shipped_files_hold_no_domain_literal(relative):
    text = _read(relative)
    assert DEFAULT_HOST not in text, (
        "%s names the domain directly; it must come from MESH_PUBLIC_URL, the "
        "domain file, or core/domain.py" % relative
    )


@pytest.mark.parametrize("relative", PLACEHOLDER_FILES)
def test_published_files_keep_the_placeholder(relative):
    """deploy_gateway.sh anchors the rewrite on these, so they must stay."""
    assert "__MESH_DOMAIN__" in _read(relative)


def test_the_default_is_declared_exactly_once_in_python():
    """Only core/domain.py may name the default; everything else asks it."""
    offenders = []
    for root, dirs, files in os.walk(REPO):
        # Every dot-directory is a local or build artifact (.git, .win-test-deps,
        # .pytest-tmp, an unpacked installer payload) and may hold a copy of
        # core/domain.py; only tracked source is audited.
        dirs[:] = [d for d in dirs if d not in
                   {"__pycache__", "node_modules", "live_logs", "docs", "tests", "dist"}
                   and not d.startswith(".")]
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(root, name)
            if os.path.relpath(path, REPO) == os.path.join("core", "domain.py"):
                continue
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as handle:
                    if DEFAULT_HOST in handle.read():
                        offenders.append(os.path.relpath(path, REPO))
            except OSError:
                continue
    assert offenders == [], "domain literal found in: %s" % ", ".join(offenders)


# ---------------------------------------------------------------------------
# The shell resolver agrees with the Python one
# ---------------------------------------------------------------------------

def _resolve(env_extra: dict) -> str:
    """Run ops/mesh-domain.sh in bash and return 'url|host|source'."""
    script = (
        '. "%s/ops/mesh-domain.sh" && mesh_resolve_domain "%s" '
        '&& printf "%%s|%%s|%%s" "$MESH_DOMAIN_URL" "$MESH_DOMAIN_HOST" "$MESH_DOMAIN_SOURCE"'
        % (REPO.replace("\\", "/"), REPO.replace("\\", "/"))
    )
    environment = dict(os.environ)
    environment.pop("MESH_PUBLIC_URL", None)
    environment.pop("AGY_PUBLIC_BASE_URL", None)
    environment.pop("MESH_DOMAIN_FILE", None)
    environment.update(env_extra)
    result = subprocess.run([BASH, "-c", script], capture_output=True, text=True,
                            env=environment, timeout=60)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@needs_bash
def test_shell_resolver_prefers_the_environment():
    assert _resolve({"MESH_PUBLIC_URL": "https://env.example.com/"}) == \
        "https://env.example.com|env.example.com|MESH_PUBLIC_URL environment variable"


@needs_bash
def test_shell_resolver_reads_the_domain_file(tmp_path):
    domain_file = tmp_path / "domain.env"
    domain_file.write_text("MESH_PUBLIC_URL=https://file.example.com\n", encoding="utf-8")
    url, host, source = _resolve({"MESH_DOMAIN_FILE": str(domain_file)}).split("|")
    assert (url, host) == ("https://file.example.com", "file.example.com")
    assert source.endswith("domain.env")


@needs_bash
def test_shell_resolver_tolerates_a_bom_and_a_bare_host(tmp_path):
    domain_file = tmp_path / "domain.env"
    domain_file.write_bytes(b"\xef\xbb\xbfMESH_PUBLIC_URL=bare.example.com\r\n")
    url, host, _source = _resolve({"MESH_DOMAIN_FILE": str(domain_file)}).split("|")
    assert (url, host) == ("https://bare.example.com", "bare.example.com")


@needs_bash
def test_shell_resolver_falls_back_to_the_python_default(tmp_path):
    url, host, source = _resolve({"MESH_DOMAIN_FILE": str(tmp_path / "absent.env")}).split("|")
    assert url == domain.DEFAULT_PUBLIC_BASE_URL
    assert host == DEFAULT_HOST
    assert "domain.py" in source


@needs_bash
def test_shell_resolver_fails_when_nothing_declares_a_domain(tmp_path):
    """A checkout is a valid source, so removal must be simulated by hiding it."""
    script = (
        'cd /tmp && . "%s/ops/mesh-domain.sh" && ! mesh_resolve_domain /nonexistent '
        '&& echo REFUSED' % REPO.replace("\\", "/")
    )
    environment = dict(os.environ)
    for key in ("MESH_PUBLIC_URL", "AGY_PUBLIC_BASE_URL", "MESH_DOMAIN_FILE"):
        environment.pop(key, None)
    environment["MESH_DOMAIN_FILE"] = str(tmp_path / "absent.env")
    result = subprocess.run([BASH, "-c", script], capture_output=True, text=True,
                            env=environment, timeout=60)
    assert "REFUSED" in result.stdout


# ---------------------------------------------------------------------------
# Syntax of everything shipped as a script
# ---------------------------------------------------------------------------

@needs_bash
@pytest.mark.parametrize("relative", ("install.sh", "deploy_gateway.sh",
                                      "ops/mesh-domain.sh", "ops/nginx/render-domain.sh"))
def test_shell_scripts_parse(relative):
    result = subprocess.run([BASH, "-n", os.path.join(REPO, relative)],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(os.name != "nt", reason="PowerShell is the Windows installer's runtime")
def test_install_ps1_is_parseable_by_windows_powershell():
    import tempfile

    probe = os.path.join(tempfile.gettempdir(), "dsh-parse-install-probe.ps1")
    with open(probe, "w", encoding="ascii") as handle:
        handle.write(
            "param([string]$Path)\n"
            "$b=[IO.File]::ReadAllBytes($Path)\n"
            "$s=[Text.Encoding]::UTF8.GetString($b)\n"
            "if([int][char]$s[0] -eq 0xFEFF){$s=$s.Substring(1)}\n"
            "$e=@()\n"
            "$null=[System.Management.Automation.Language.Parser]::ParseInput($s,[ref]$null,[ref]$e)\n"
            "if($e.Count -gt 0){ $e | ForEach-Object { Write-Error $_.Message }; exit 1 }\n"
            "exit 0\n"
        )
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", probe, "-Path", os.path.join(REPO, "install.ps1")],
            capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stderr or result.stdout
    finally:
        try:
            os.unlink(probe)
        except OSError:
            pass


@needs_bash
def test_renderer_substitutes_every_placeholder(tmp_path):
    environment = dict(os.environ)
    environment["MESH_PUBLIC_URL"] = "https://render.example.com"
    environment.pop("MESH_DOMAIN_FILE", None)
    result = subprocess.run(
        [BASH, os.path.join(REPO, "ops/nginx/render-domain.sh"), "--out", str(tmp_path)],
        capture_output=True, text=True, env=environment, timeout=60)
    assert result.returncode == 0, result.stderr
    rendered = (tmp_path / "render.example.com").read_text(encoding="utf-8")
    assert "server_name render.example.com;" in rendered
    assert "/etc/letsencrypt/live/render.example.com/fullchain.pem" in rendered
    assert "__MESH_" not in rendered
