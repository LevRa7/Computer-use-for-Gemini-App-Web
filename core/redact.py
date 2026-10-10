"""Redaction of credentials from text that is about to be logged or persisted.

The mesh runs remote commands through ``sshpass``, so a command string can carry
a password verbatim (``sshpass -p '<password>' ssh ...``). The telemetry writers
(:mod:`agy_watcher`, :mod:`agy_webhook_server`) store those command strings in
``task_metrics.json`` and ``webhook_history.json``, which live next to the code
and have been committed to a public repository before. Every string headed for
one of those files therefore goes through :func:`redact_secrets` first.

The patterns are deliberately anchored on credential *shapes*: they never touch a
shell variable reference (``$MATEBOOK_PASS``) or an explicit placeholder
(``<compute-ip>``), so a redacted command still documents itself.
"""
from __future__ import annotations

import re
from typing import Any

REDACTED = "<REDACTED>"

# A value that is a shell variable reference or a placeholder is not a
# credential, so it is left as it is: the command stays readable, and redaction
# is stable when it runs twice over the same string.
_NOT_A_SECRET = re.compile(r"^[$<]")

# Every pattern exposes the same three groups - prefix, value, suffix - so one
# replacement function can serve all of them and keep the surrounding syntax
# (quotes, ``=``, ``@``) intact.
_PATTERNS: tuple[re.Pattern[str], ...] = (
    # sshpass -p 'secret' / sshpass -p "secret" / sshpass -p secret, with any of
    # sshpass's own options (each with an optional separate argument) in front of
    # -p. The block of options is anchored immediately before -p, so a later -p
    # that belongs to another command on the same line is never rewritten.
    re.compile(
        r"(?P<prefix>\bsshpass\b(?:\s+-\w+(?:\s+\S+)?)*\s+-p\s*(?P<quote>['\"]?))"
        r"(?P<value>[^\s'\"]+)"
        r"(?P<suffix>(?P=quote))"
    ),
    # key=value forms: password=..., "client_secret": "...", token: ...
    re.compile(
        r"(?P<prefix>(?i:\b(?:password|passwd|passphrase|secret|token|api[_-]?key"
        r"|access[_-]?key|client[_-]?secret|auth[_-]?token)\b\s*[:=]\s*(?P<quote>['\"]?)))"
        r"(?P<value>[^\s'\"]{8,})"
        r"(?P<suffix>(?P=quote))"
    ),
    # Google OAuth: access token, refresh token, client secret.
    re.compile(r"(?P<prefix>\bya29\.)(?P<value>[A-Za-z0-9_\-.]{20,})(?P<suffix>)"),
    re.compile(r"(?P<prefix>\b1//0)(?P<value>[A-Za-z0-9_\-]{20,})(?P<suffix>)"),
    re.compile(r"(?P<prefix>\bGOCSPX-)(?P<value>[A-Za-z0-9_\-]{10,})(?P<suffix>)"),
    # GitHub tokens.
    re.compile(r"(?P<prefix>\bgh[pousr]_)(?P<value>[A-Za-z0-9]{30,})(?P<suffix>)"),
    # Credentials embedded in a URL ("https://user:pass@host").
    re.compile(r"(?P<prefix>://[^/\s:@]+:)(?P<value>[^/\s@]{3,})(?P<suffix>@)"),
    # An explicit HTTP bearer credential.
    re.compile(r"(?P<prefix>(?i:\bbearer\s+))(?P<value>[^\s'\"]{8,})(?P<suffix>)"),
)


def _mask(match: re.Match[str]) -> str:
    """Replace one credential-shaped match, leaving placeholders alone."""
    value = match.group("value")
    if _NOT_A_SECRET.match(value):
        return match.group(0)
    return f"{match.group('prefix')}{REDACTED}{match.group('suffix')}"


def redact_secrets(text: str) -> str:
    """Return ``text`` with every credential-shaped span replaced by ``<REDACTED>``."""
    for pattern in _PATTERNS:
        text = pattern.sub(_mask, text)
    return text


def redact_entry(value: Any) -> Any:
    """Recursively redact every string in a JSON-shaped structure.

    Keys are left untouched: they are fixed field names, and rewriting them would
    silently change the telemetry schema.
    """
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, dict):
        return {key: redact_entry(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_entry(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_entry(item) for item in value)
    return value
