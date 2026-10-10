from core.redact import REDACTED, redact_entry, redact_secrets

SECRET = "hunter2-Sup3rSecret"


def _synthetic(prefix: str, seed: str, length: int) -> str:
    """Build a credential of the shape a secret scanner looks for, from parts.

    A complete vendor-shaped token literal in a test file is indistinguishable
    from a live one: GitHub's push protection refuses the push and gitleaks
    reports the file rather than let it through. The value is therefore assembled
    at run time, so only the prefix - never a whole token - appears in the source.
    """
    return prefix + (seed * (length // len(seed) + 1))[:length]


def test_sshpass_password_is_redacted_in_every_quoting():
    # The rest of the command must survive: a redacted command still documents
    # what was executed and against which host, so the later host check is part of
    # every case.
    cases = (
        (f"sshpass -p '{SECRET}' ssh root@100.64.0.11 'uptime'", "100.64.0.11"),
        (f'sshpass -p "{SECRET}" ssh root@100.64.0.11 "uptime"', "100.64.0.11"),
        (f"sshpass -p {SECRET} ssh root@100.64.0.11 uptime", "100.64.0.11"),
        (f"sshpass -f /root/.pw -p '{SECRET}' scp a root@host:/tmp", "root@host"),
    )
    for command, kept in cases:
        redacted = redact_secrets(command)
        assert SECRET not in redacted
        assert REDACTED in redacted
        assert kept in redacted


def test_variable_references_and_placeholders_are_kept():
    for command in (
        "sshpass -p '$MATEBOOK_PASS' ssh <ssh-user>@<workstation-ip> '<cmd>'",
        "sshpass -p \"$DEBIAN_PASS\" ssh root@<compute-ip> '<cmd>'",
    ):
        assert redact_secrets(command) == command


def test_known_credential_formats_are_redacted():
    google_access = _synthetic("ya29.", "a0AdMD6EjT", 60)
    google_refresh = _synthetic("1//0", "1LX5ZqYI", 50)
    google_client_secret = _synthetic("GOCSPX-", "abcdefghijkl", 30)
    github_pat = _synthetic("ghp_", "abcdefghijklmnop", 36)
    slack_token = _synthetic("xoxb-", "1234567890", 24)
    aws_key = _synthetic("AKIA", "ABCDEFGHIJKLMNOP", 16)
    # The recognisable prefix (GOCSPX-, ya29., ghp_) is kept on purpose - it says
    # what kind of credential was there - so the assertion is on the secret body.
    cases = (
        (f"token='{slack_token}'", slack_token),
        (f"GET https://api.example.com?api_key={aws_key}", aws_key),
        (f"client_secret: {google_client_secret}", google_client_secret.removeprefix("GOCSPX-")),
        (f"access_token={google_access}", google_access.removeprefix("ya29.")),
        (f"refresh_token {google_refresh}", google_refresh.removeprefix("1//0")),
        (f"github_token={github_pat}", github_pat.removeprefix("ghp_")),
        ("https://user:s3cr3t-pw@gateway.example.com/health", "s3cr3t-pw"),
        ("Authorization: Bearer abcdef1234567890", "abcdef1234567890"),
    )
    for text, secret_body in cases:
        redacted = redact_secrets(text)
        assert REDACTED in redacted, text
        assert secret_body not in redacted, text


def test_ordinary_commands_are_untouched():
    for command in (
        "ls -la && df -h",
        "systemctl status agy-webhook.service",
        "python3 -m pytest tests --report token_uri=https://oauth2.googleapis.com/token",
        "grep -c 'failed' /var/log/auth.log",
    ):
        assert redact_secrets(command) == command


def test_redaction_is_idempotent():
    once = redact_secrets(f"sshpass -p '{SECRET}' ssh root@h 'uptime'")
    assert redact_secrets(once) == once


def test_redact_entry_walks_the_metric_shape():
    entry = {
        "task_id": "task_1",
        "commands": [
            {
                "command": f"sshpass -p '{SECRET}' ssh root@100.64.0.11 'uptime'",
                # Remote output is persisted too, so it is redacted on the same
                # pass as the command.
                "stdout": f"password={SECRET}\n",
                "exit_code": 0,
            }
        ],
        "output_length_chars": 24,
    }
    redacted = redact_entry(entry)
    assert redacted["task_id"] == "task_1"
    assert redacted["output_length_chars"] == 24
    assert redacted["commands"][0]["exit_code"] == 0
    assert SECRET not in str(redacted)
