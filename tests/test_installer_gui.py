"""The visual Windows installer must stay parseable, bilingual and self-checking.

``install-gui.ps1`` is deliberately pure ASCII and keeps every user-visible string
in ``install-gui.strings.json``. Windows PowerShell 5.1 decodes a script without a
UTF-8 BOM using the ANSI code page, and Russian text then turns into characters the
parser reads as string delimiters - the same trap that forces ``install.ps1`` to
carry a BOM (see docs/WINDOWS_TESTING.md). These tests pin that property, the
parity of the two languages, and the wizard's own ``-SelfTest`` report.

They are Windows-only because the wizard is: it is a WinForms application and it
drives ``install.ps1``.
"""

import json
import os
import re
import subprocess
import tempfile

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUI_SCRIPT = os.path.join(REPO, "install-gui.ps1")
GUI_STRINGS = os.path.join(REPO, "install-gui.strings.json")
GUI_LAUNCHER = os.path.join(REPO, "install-gui.cmd")

needs_windows = pytest.mark.skipif(
    os.name != "nt", reason="the visual installer is a Windows-only WinForms wizard"
)


def _powershell() -> str:
    """Windows PowerShell 5.1 - the interpreter the wizard and install.ps1 target."""
    candidate = os.path.join(
        os.environ.get("SystemRoot", r"C:\Windows"),
        "System32", "WindowsPowerShell", "v1.0", "powershell.exe",
    )
    return candidate if os.path.exists(candidate) else "powershell.exe"


def _read_bytes(path: str) -> bytes:
    with open(path, "rb") as handle:
        return handle.read()


def _read_text(path: str) -> str:
    return _read_bytes(path).decode("utf-8")


# ---------------------------------------------------------------------------
# Source-level properties that do not need PowerShell to run
# ---------------------------------------------------------------------------

@needs_windows
def test_gui_script_is_pure_ascii():
    """A non-ASCII byte here would break the script on a BOM-less copy.

    Windows PowerShell 5.1 reads such a file with the ANSI code page; Russian text
    then produces characters the parser treats as quote delimiters and the whole
    file fails to parse. Every user-visible string therefore lives in the JSON
    resource, which is read explicitly as UTF-8.
    """
    data = _read_bytes(GUI_SCRIPT)
    offenders = [(index, byte) for index, byte in enumerate(data) if byte > 127]
    assert offenders == [], (
        "install-gui.ps1 must stay pure ASCII; first non-ASCII byte at offset %d "
        "(0x%02x)" % offenders[0] if offenders else ""
    )


@needs_windows
def test_launcher_is_pure_ascii_and_points_at_the_script():
    text = _read_text(GUI_LAUNCHER)
    assert all(ord(character) < 128 for character in text), "install-gui.cmd must stay ASCII"
    assert "install-gui.ps1" in text, "the launcher must run install-gui.ps1"
    assert "-Sta" in text, "the clipboard needs a single-threaded apartment (-Sta)"


@needs_windows
def test_strings_resource_is_valid_utf8_json_with_matching_languages():
    document = json.loads(_read_text(GUI_STRINGS))
    assert set(document) >= {"en", "ru"}, "the resource must carry both languages"

    english = set(document["en"])
    russian = set(document["ru"])
    assert english == russian, (
        "language keys differ: missing in ru=%s, extra in ru=%s"
        % (sorted(english - russian), sorted(russian - english))
    )
    assert english, "the string table must not be empty"

    # A key that maps to itself means a typo: Get-UiText falls back to the key.
    unresolved = [key for key in english if document["en"][key] == key]
    assert unresolved == [], "keys resolving to themselves: %s" % ", ".join(sorted(unresolved))


@needs_windows
def test_russian_strings_survive_a_utf8_round_trip():
    """Guards the file against being re-saved in the ANSI code page."""
    document = json.loads(_read_text(GUI_STRINGS))
    russian_values = [value for value in document["ru"].values() if isinstance(value, str)]
    cyrillic = [value for value in russian_values if any("\u0400" <= ch <= "\u04ff" for ch in value)]
    assert len(cyrillic) > 20, "the Russian table should be genuinely Russian"


@needs_windows
def test_gui_files_do_not_name_the_project_domain():
    """The domain has one source of truth (core/domain.py), not a literal here."""
    from core import domain

    default_host = domain.DEFAULT_PUBLIC_BASE_URL.split("//", 1)[-1].rstrip("/")
    for path in (GUI_SCRIPT, GUI_STRINGS, GUI_LAUNCHER):
        assert default_host not in _read_text(path), (
            "%s names the domain directly; it must be resolved at run time" % os.path.basename(path)
        )


# ---------------------------------------------------------------------------
# Windows PowerShell 5.1 must actually parse it
# ---------------------------------------------------------------------------

@needs_windows
def test_gui_script_parses_under_windows_powershell():
    probe = os.path.join(tempfile.gettempdir(), "dsh-parse-install-gui-probe.ps1")
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
            [_powershell(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", probe, "-Path", GUI_SCRIPT],
            capture_output=True, text=True, timeout=120,
        )
        assert result.returncode == 0, result.stderr or result.stdout
    finally:
        try:
            os.unlink(probe)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# The wizard's own headless self-check
# ---------------------------------------------------------------------------

def _run_self_test(*extra_args):
    """Run the wizard's -SelfTest and return (returncode, parsed report)."""
    result = subprocess.run(
        [_powershell(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-Sta",
         "-File", GUI_SCRIPT, "-SelfTest"] + list(extra_args),
        capture_output=True, text=True, timeout=600,
    )
    output = result.stdout or ""
    start = output.find("{")
    assert start >= 0, "the self-test printed no JSON report:\n%s\n%s" % (output, result.stderr)
    return result.returncode, json.loads(output[start:])


def _detail(report, name):
    for item in report["results"]:
        if item["name"] == name:
            return item["detail"]
    raise AssertionError("the self-test has no %r check: %s" % (name, sorted(
        item["name"] for item in report["results"])))


def _field(detail, name):
    match = re.search(r"\b%s=([^\s\]]+)" % re.escape(name), detail or "")
    return match.group(1) if match else ""


@needs_windows
def test_gui_self_test_passes():
    """Every page in both languages, the process pipeline, the URL parser and the
    preflight parser, all inside the wizard's own headless check."""
    returncode, report = _run_self_test()

    failures = [item for item in report["results"] if not item["pass"]]
    assert report["ok"] is True, "self-test failures: %s" % json.dumps(failures, ensure_ascii=False)
    assert returncode == 0, "the self-test must exit 0 when it passes"

    names = {item["name"] for item in report["results"]}
    for required in ("source_is_ascii", "language_keys_match", "pages_build",
                     "pipeline_captures_output", "pipeline_reports_failure",
                     "url_parser_box", "url_from_agent_env", "chrome_localised",
                     "result_page_link", "no_control_overflow", "language_detection"):
        assert required in names, "the self-test lost its %r check" % required


@needs_windows
def test_start_language_is_detected_when_no_flag_is_given():
    """Without -Lang the wizard must use the language it detected.

    The script's internal language variable used to share the parameter's name:
    at script scope "$script:Lang" *is* "$Lang", so the initialiser
    "$script:Lang = 'en'" overwrote whatever -Lang had bound. 'en' is truthy, so
    the detection branch never ran and the wizard always started in English on a
    Russian Windows.
    """
    _returncode, report = _run_self_test()
    detail = _detail(report, "language_detection")
    detected = _field(detail, "detected")
    effective = _field(detail, "effective")
    assert detected in ("en", "ru"), detail
    assert detected == effective, (
        "detection chose %r but the wizard started in %r: %s" % (detected, effective, detail)
    )


@needs_windows
@pytest.mark.parametrize("language", ["ru", "en"])
def test_explicit_lang_reaches_the_script(language):
    """-Lang must actually reach the entry point, not be overwritten."""
    _returncode, report = _run_self_test("-Lang", language)
    detail = _detail(report, "language_detection")
    assert _field(detail, "effective") == language, detail


@needs_windows
def test_detection_reads_the_user_language_list_not_only_the_process_culture():
    """The signals must include the registry, not just CurrentUICulture.

    On the machine this was developed on, CurrentUICulture is en-US while
    PreferredUILanguages and InstalledUICulture are ru-RU, so a check against the
    process culture alone starts the wizard in the wrong language.
    """
    _returncode, report = _run_self_test()
    detail = _detail(report, "language_detection")
    signals = _field(detail, "signals")
    assert signals, "the detection reported no signals at all: %s" % detail
    assert "detected=" in detail, detail
