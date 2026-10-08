"""Windows ops: the watchdog and the doctor must be shippable and safe to run.

``install.ps1`` pins a full interpreter path for a reason: the Microsoft Store
"python.exe" App Execution Alias can sit on PATH, exist as a file and still run no
Python at all. After a Windows Update reboot that alias left a truncated
``Python `` line in ``agent.log`` (no trailing newline) and the node stayed
offline for about 29 hours. ``ops/windows/agent-watchdog.ps1`` recovers such a
node, ``ops/doctor.ps1`` explains it, and the properties that make both usable are
pinned here:

* they exist and are ASCII-only - Windows PowerShell 5.1 decodes a BOM-less script
  with the ANSI code page, so a single non-ASCII byte can break the whole file;
* both reject ``\\WindowsApps\\``;
* neither can leak the token: it is masked as ``****``, and no log/print statement
  interpolates it;
* the documented parameters stay in place;
* ``bin/cli.js`` exposes the two maintenance subcommands;
* and, on Windows only, the real PowerShell parser accepts both files.

The parser check reuses the probe technique from
``tests/test_domain_single_source.py::test_install_ps1_is_parseable_by_windows_powershell``
and is skipped everywhere else. Everything above runs offline on any platform.
"""

import os
import re
import subprocess
import tempfile

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

WATCHDOG = os.path.join("ops", "windows", "agent-watchdog.ps1")
DOCTOR = os.path.join("ops", "doctor.ps1")
SCRIPTS = (WATCHDOG, DOCTOR)
CLI = os.path.join("bin", "cli.js")

#: The broken Microsoft Store alias directory. An interpreter under it must never
#: be pinned, trusted or started again, however real the file looks.
STORE_ALIAS = "\\WindowsApps\\"

#: A statement that could put a value in front of a human or into a file. The
#: token may be read, exported and masked - never printed or logged.
LEAKY_STATEMENT = re.compile(
    r"(?i)(write-host|write-output|add-content|appendalltext)\b[^\n]*\$token\b")


def _path(relative):
    return os.path.join(REPO, relative)


def _read_bytes(relative):
    with open(_path(relative), "rb") as handle:
        return handle.read()


def _read_text(relative):
    return _read_bytes(relative).decode("utf-8")


# ---------------------------------------------------------------------------
# Shippable: present and 7-bit clean
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("relative", SCRIPTS)
def test_script_exists_and_is_not_empty(relative):
    assert os.path.isfile(_path(relative)), "%s is missing" % relative
    assert os.path.getsize(_path(relative)) > 0, "%s is empty" % relative


@pytest.mark.parametrize("relative", SCRIPTS)
def test_script_is_ascii_only(relative):
    raw = _read_bytes(relative)
    offenders = sorted({byte for byte in raw if byte > 0x7F})
    assert offenders == [], (
        "%s holds non-ASCII bytes (%s); Windows PowerShell 5.1 reads a BOM-less "
        "script with the ANSI code page and may fail to parse it"
        % (relative, ", ".join("0x%02X" % byte for byte in offenders)))


# ---------------------------------------------------------------------------
# The incident: the Store alias and the token
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("relative", SCRIPTS)
def test_scripts_refuse_the_store_alias(relative):
    assert STORE_ALIAS in _read_text(relative), (
        "%s must reject the Microsoft Store App Execution Alias, the interpreter "
        "that silently did nothing after the Windows Update reboot" % relative)


def test_watchdog_masks_the_token():
    text = _read_text(WATCHDOG)
    assert "****" in text, "the watchdog must mask the token as ****"
    # The token can never be printed or written to a log.
    assert LEAKY_STATEMENT.findall(text) == [], (
        "a print/log statement in the watchdog interpolates $Token")


def test_doctor_masks_the_token():
    text = _read_text(DOCTOR)
    assert "****" in text, "the doctor must mask the token as ****"
    assert "function Hide-Secret" in text
    assert LEAKY_STATEMENT.findall(text) == [], (
        "a print/log statement in the doctor interpolates $Token")


@pytest.mark.parametrize("relative", SCRIPTS)
def test_scripts_never_read_the_token_without_masking_it(relative):
    """The token variable may be assigned, exported and masked - nothing else."""
    text = _read_text(relative)
    allowed = ("$token =", "= $token", "-secret $token", "$env:mesh_token",
               "mesh_token'", 'mesh_token"')
    for line in text.splitlines():
        if line.strip().startswith("#"):
            continue
        if "$token" not in line.lower():
            continue
        assert any(marker in line.lower() for marker in allowed), (
            "unexpected use of $Token in %s: %s" % (relative, line.strip()))


# ---------------------------------------------------------------------------
# The published CLI contract
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("relative, parameters", (
    (WATCHDOG, ("-ConfigDir", "-LogFile", "-GraceSeconds", "-Quiet")),
    (DOCTOR, ("-ConfigDir", "-Json", "-TailLines")),
))
def test_documented_parameters_stay_in_place(relative, parameters):
    text = _read_text(relative)
    for parameter in parameters:
        assert parameter in text, "%s no longer accepts %s" % (relative, parameter)


def test_watchdog_documents_grace_seconds_and_its_default():
    """-GraceSeconds is the heartbeat threshold the docs and install.ps1 rely on."""
    text = _read_text(WATCHDOG)
    assert re.search(r"\[int\]\$GraceSeconds\s*=\s*60", text), (
        "-GraceSeconds must stay declared with its default of 60 seconds")
    header = text.split("[CmdletBinding()]")[0]
    assert "-GraceSeconds" in header, (
        "-GraceSeconds must be documented in the script header, not only in param()")
    assert text.count("-GraceSeconds") >= 3, (
        "-GraceSeconds needs a header entry (description, usage and parameters)")


def test_watchdog_publishes_its_exit_codes():
    text = _read_text(WATCHDOG)
    for code in ("exit 0", "exit 2", "exit 3"):
        assert code in text, "the watchdog lost '%s'" % code


def test_doctor_publishes_its_verdicts():
    text = _read_text(DOCTOR)
    for verdict in ("interpreter_ok", "autostart_ok", "heartbeat_fresh", "node_online"):
        assert verdict in text, "the doctor no longer reports %s" % verdict
    assert "torn-line" in text, "the doctor must flag a torn agent.log line"


def test_cli_exposes_the_maintenance_subcommands():
    text = _read_text(CLI)
    assert "doctor" in text, "bin/cli.js must expose the doctor subcommand"
    assert "restart" in text, "bin/cli.js must expose the restart subcommand"
    assert "agent-watchdog.ps1" in text
    assert "doctor.ps1" in text


# ---------------------------------------------------------------------------
# "Is an agent alive?" must not be answered by a substring
# ---------------------------------------------------------------------------
# The live defect: the process check matched any command line that merely MENTIONED
# core.agent - the operator's own PowerShell session, for example. If the heartbeat
# and the gateway are both unavailable, that false positive makes the watchdog skip
# the restart the node needs.

#: The argument pattern both scripts use: "-m core.agent" must be a real argument,
#: so it opens the command line or follows a space or a quote.
MODULE_ARGUMENT = r'(?i)(^|[\s"])-m[\s"]+core\.agent\b'

SHELL_NAMES = ("powershell", "pwsh", "wscript", "cscript")


def _function_body(text, name):
    match = re.search(r"(?ms)^function %s \{.*?^\}" % re.escape(name), text)
    assert match, "function %s not found" % name
    return match.group(0)


def _agent_process_matches(name, command_line):
    """The rule both scripts implement, mirrored here so it can be checked offline.

    Kept literal on purpose: the marker assertions below fail if the shipped rule
    changes, which is what keeps this mirror honest.
    """
    name = (name or "").strip().lower()
    if not command_line:
        return False
    if name.startswith(SHELL_NAMES):
        return False
    if not re.search(MODULE_ARGUMENT, command_line):
        return False
    if name == "py.exe" or name.startswith("python"):
        return True
    if name in ("cmd.exe", "cmd"):
        return "agent.log" in command_line.lower()
    return False


@pytest.mark.parametrize("relative", SCRIPTS)
def test_process_check_requires_a_real_agent(relative):
    text = _read_text(relative)
    assert "function Test-AgentProcess" in text
    assert MODULE_ARGUMENT in text, (
        "%s must require '-m core.agent' as an argument, not as a substring" % relative)
    assert "StartsWith('python')" in text, (
        "%s must require a Python process name (python.exe/pythonw.exe/...)" % relative)
    for shell in SHELL_NAMES:
        assert shell in text, (
            "%s must never mistake %s for the agent" % (relative, shell))


def test_both_scripts_apply_the_same_process_rule():
    bodies = {_function_body(_read_text(relative), "Test-AgentProcess")
              for relative in SCRIPTS}
    assert len(bodies) == 1, (
        "the watchdog's liveness rule and the doctor's listing must not diverge")


def test_the_process_rule_rejects_a_command_line_that_only_mentions_the_module():
    # The operator's own PowerShell session, and anything else that only talks
    # about the agent, is not an agent.
    assert not _agent_process_matches(
        "powershell.exe", "powershell.exe -NoProfile -File C:\\ops\\doctor.ps1")
    assert not _agent_process_matches(
        "powershell.exe", 'powershell.exe -Command "Select-String core.agent agent.log"')
    assert not _agent_process_matches(
        "powershell.exe", 'powershell.exe -Command "Start-Process python -m core.agent"')
    assert not _agent_process_matches("pwsh.exe", 'pwsh.exe -c "python -m core.agent"')
    assert not _agent_process_matches("notepad.exe", "notepad.exe core.agent")
    assert not _agent_process_matches("python.exe", 'python.exe -c "print(1)"')
    assert not _agent_process_matches("cmd.exe", "cmd.exe /c python -m core.agent")


def test_the_process_rule_accepts_every_real_way_the_agent_starts():
    assert _agent_process_matches(
        "python.exe", '"C:\\Python313\\python.exe" -u -m core.agent')
    assert _agent_process_matches("python.exe", "python.exe -m core.agent")
    assert _agent_process_matches("pythonw.exe", "pythonw.exe -m core.agent")
    assert _agent_process_matches("py.exe", "py.exe -3 -m core.agent")
    # The logon launcher's wrapper, which carries the agent.log append redirect.
    assert _agent_process_matches(
        "cmd.exe",
        'cmd.exe /c ""C:\\Python313\\python.exe" -u -m core.agent >> "C:\\cfg\\agent.log" 2>&1"')


# ---------------------------------------------------------------------------
# Parsed by the real Windows PowerShell 5.1
# ---------------------------------------------------------------------------

@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell is the nodes' runtime")
@pytest.mark.parametrize("relative", SCRIPTS)
def test_scripts_are_parseable_by_windows_powershell(relative):
    """The probe is the one used for install.ps1 in test_domain_single_source.py."""
    probe = os.path.join(tempfile.gettempdir(), "dsh-parse-ops-probe.ps1")
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
             "-File", probe, "-Path", _path(relative)],
            capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stderr or result.stdout
    finally:
        try:
            os.unlink(probe)
        except OSError:
            pass
