"""Unified sensitive-data detection, labelling, and redaction.

One implementation is used by logs, tool results, task history, checkpoints,
events, reflections, training records, research content, and clipboard data.
Detection inspects *values*, not only dictionary keys, because a credential
pasted into a prompt or returned in stdout has no helpful key name.

Redaction stays diagnostic. A redacted value keeps its detected type, its
length, and a short digest so a failure can still be investigated without the
secret being recoverable from the record.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Iterable


class Sensitivity(StrEnum):
    """Handling label attached to a payload."""

    PUBLIC = "PUBLIC"          # may be stored, logged, and transmitted
    PROJECT = "PROJECT"        # project-internal; stored and logged, not transmitted by default
    PRIVATE = "PRIVATE"        # user-private; stored redacted, never transmitted without approval
    SECRET = "SECRET"          # credentials; never stored, never trained on, never transmitted
    EPHEMERAL = "EPHEMERAL"    # session-only (clipboard, audio); never persisted


_ORDER = {
    Sensitivity.PUBLIC: 0,
    Sensitivity.PROJECT: 1,
    Sensitivity.PRIVATE: 2,
    Sensitivity.EPHEMERAL: 3,
    Sensitivity.SECRET: 4,
}

#: Labels that must never reach durable memory or training data.
NON_PERSISTABLE = {Sensitivity.SECRET, Sensitivity.EPHEMERAL}

#: Labels that must never leave the machine without an exact, explicit approval.
NON_TRANSMITTABLE = {Sensitivity.SECRET, Sensitivity.EPHEMERAL, Sensitivity.PRIVATE}


def highest(labels: Iterable[Sensitivity]) -> Sensitivity:
    return max(labels, key=lambda item: _ORDER[item], default=Sensitivity.PUBLIC)


# --------------------------------------------------------------------- patterns

# (name, pattern, group holding the secret material)
VALUE_PATTERNS: list[tuple[str, re.Pattern[str], int]] = [
    ("private_key_block", re.compile(r"-----BEGIN[ A-Z]*PRIVATE KEY-----.*?-----END[ A-Z]*PRIVATE KEY-----", re.S), 0),
    ("openai_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"), 0),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b"), 0),
    ("github_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{20,})\b"), 0),
    ("aws_access_key_id", re.compile(r"\b(?:AKIA|ASIA|AIDA|AROA)[0-9A-Z]{12,}\b"), 0),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"), 0),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), 0),
    ("stripe_key", re.compile(r"\b[rs]k_(?:live|test)_[A-Za-z0-9]{16,}\b"), 0),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"), 0),
    ("authorization_header", re.compile(r"(?i)\b(?:bearer|basic|token)\s+([A-Za-z0-9._~+/=-]{16,})"), 1),
    ("azure_storage_key", re.compile(r"(?i)AccountKey=([A-Za-z0-9+/=]{20,})"), 1),
    ("connection_string_password", re.compile(r"(?i)\b(?:password|pwd)\s*=\s*([^;\s\"']{6,})"), 1),
    ("url_credentials", re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.-]*://[^/\s:@]+:([^/\s:@]{4,})@"), 1),
    (
        "assigned_secret",
        re.compile(
            r"(?i)\b(?:api[_-]?key|apikey|secret[_-]?key|access[_-]?token|auth[_-]?token|"
            r"refresh[_-]?token|client[_-]?secret|private[_-]?key|passwd|password|passphrase|credential)"
            r"\s*[:=]\s*[\"']?([^\s\"',;]{8,})"
        ),
        1,
    ),
    # The same secret spoken rather than assigned: "log in with password X",
    # "my api key is X". A credential dictated into a prompt or a failure
    # message is exactly as sensitive as one written with an equals sign, and
    # free prose is the form a voice session produces.
    (
        "spoken_secret",
        re.compile(
            r"(?i)\b(?:pass(?:word|phrase|code)|api[_ -]?key|secret[_ -]?key|"
            r"access[_ -]?token|auth[_ -]?token|refresh[_ -]?token|client[_ -]?secret)"
            r"\s+(?:is\s+|was\s+|to\s+|of\s+)?[\"']?([^\s\"',;]{8,})"
        ),
        1,
    ),
]

#: Words that follow "password"/"token" in ordinary prose about a credential
#: rather than being the credential. Without these, "the password is incorrect"
#: would redact the word "incorrect" and make the sentence unreadable.
_CREDENTIAL_PROSE = frozenset({
    "incorrect", "invalid", "expired", "required", "missing", "mismatch",
    "protected", "manager", "field", "prompt", "prompts", "reset", "change",
    "changed", "changes", "correct", "wrong", "empty", "blank", "unknown",
    "hidden", "rejected", "accepted", "verified", "unverified", "strength",
    "policy", "rotation", "storage", "entry", "authentication", "credentials",
    "again", "already", "instead", "should", "cannot", "before", "because",
})

#: Key names whose value is redacted regardless of its shape.
SECRET_KEY_NAMES = re.compile(
    r"(?i)(api[_-]?key|apikey|token|password|passwd|secret|authorization|credential|"
    r"private[_-]?key|passphrase|session[_-]?id|cookie)"
)

#: Values that look like placeholders and are safe to keep readable.
_PLACEHOLDER = re.compile(r"(?i)^(?:|none|null|true|false|x+|\*+|<[^>]*>|\$\{[^}]*\}|example|placeholder|dummy|fake|changeme|redacted|\[redacted[^\]]*\])$")

_MINIMUM_LENGTH = 8


@dataclass(slots=True)
class Finding:
    kind: str
    start: int
    end: int
    digest: str
    length: int

    def placeholder(self, *, include_digest: bool = True) -> str:
        if not include_digest:
            return f"[REDACTED:{self.kind}]"
        return f"[REDACTED:{self.kind} len={self.length} sha256={self.digest}]"


def digest_of(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:12]


def environment_secrets() -> set[str]:
    """Literal secret values present in the process environment.

    Lets an exact `.env` value be redacted even when it matches no pattern.
    The values themselves are never returned to callers or logged.
    """
    values: set[str] = set()
    for name, value in os.environ.items():
        if not value or len(value) < _MINIMUM_LENGTH:
            continue
        if _PLACEHOLDER.match(value.strip()):
            continue
        if SECRET_KEY_NAMES.search(name):
            values.add(value)
    return values


def scan(text: str, *, extra_values: Iterable[str] | None = None) -> list[Finding]:
    """Locate secret-shaped material in free text. Non-overlapping, left to right."""
    if not text:
        return []
    findings: list[Finding] = []
    for value in extra_values or ():
        if not value or len(value) < _MINIMUM_LENGTH:
            continue
        start = 0
        while (index := text.find(value, start)) != -1:
            findings.append(Finding("environment_secret", index, index + len(value), digest_of(value), len(value)))
            start = index + len(value)
    for kind, pattern, group in VALUE_PATTERNS:
        for match in pattern.finditer(text):
            captured = match.group(group)
            if not captured or len(captured) < _MINIMUM_LENGTH:
                continue
            if _PLACEHOLDER.match(captured.strip()):
                continue
            if kind == "spoken_secret" and captured.strip().strip(".,!?").lower() in _CREDENTIAL_PROSE:
                continue
            findings.append(Finding(kind, match.start(group), match.end(group), digest_of(captured), len(captured)))
    findings.sort(key=lambda item: (item.start, -item.length))
    merged: list[Finding] = []
    for finding in findings:
        if merged and finding.start < merged[-1].end:
            continue
        merged.append(finding)
    return merged


def redact_text(
    text: str, *, extra_values: Iterable[str] | None = None, include_digest: bool = True
) -> tuple[str, list[Finding]]:
    """Replace secret material with a placeholder.

    ``include_digest`` controls whether the placeholder carries the length and a
    short digest. Operator-facing records (logs, events, task history) keep them
    because they make a failure diagnosable. Exportable corpora (training data)
    drop them: a digest is still value-derived, and value-derived placeholders
    also defeat deduplication of samples that differ only by their secret.
    """
    findings = scan(text, extra_values=extra_values)
    if not findings:
        return text, []
    pieces: list[str] = []
    cursor = 0
    for finding in findings:
        pieces.append(text[cursor : finding.start])
        pieces.append(finding.placeholder(include_digest=include_digest))
        cursor = finding.end
    pieces.append(text[cursor:])
    return "".join(pieces), findings


def classify_text(text: str, *, extra_values: Iterable[str] | None = None) -> Sensitivity:
    return Sensitivity.SECRET if scan(text, extra_values=extra_values) else Sensitivity.PUBLIC


class Redactor:
    """Recursive key-aware and value-aware redaction over arbitrary structures."""

    def __init__(
        self, *, include_environment: bool = True, max_string: int = 200_000, include_digest: bool = True
    ) -> None:
        self._environment = environment_secrets() if include_environment else set()
        self.max_string = max_string
        self.include_digest = include_digest
        self.findings: list[Finding] = []

    def refresh_environment(self) -> None:
        self._environment = environment_secrets()

    def text(self, value: str) -> str:
        if not isinstance(value, str) or len(value) > self.max_string:
            return value
        redacted, findings = redact_text(
            value, extra_values=self._environment, include_digest=self.include_digest
        )
        self.findings.extend(findings)
        return redacted

    def value(self, value: Any, *, key: str | None = None) -> Any:
        if key is not None and SECRET_KEY_NAMES.search(str(key)):
            if isinstance(value, str) and value and not _PLACEHOLDER.match(value.strip()):
                if not self.include_digest:
                    return "[REDACTED]"
                return f"[REDACTED:key:{key} len={len(value)} sha256={digest_of(value)}]"
            return "[REDACTED]"
        if isinstance(value, dict):
            return {name: self.value(item, key=str(name)) for name, item in value.items()}
        if isinstance(value, (list, tuple)):
            rendered = [self.value(item) for item in value]
            return type(value)(rendered) if isinstance(value, tuple) else rendered
        if isinstance(value, str):
            return self.text(value)
        return value

    def sensitivity(self) -> Sensitivity:
        return Sensitivity.SECRET if self.findings else Sensitivity.PUBLIC


def redact(value: Any) -> Any:
    """Redact a structure by key name and by value content."""
    return Redactor().value(value)


def contains_secret(value: Any) -> bool:
    """True when any string anywhere in the structure carries secret material."""
    redactor = Redactor()
    redactor.value(value)
    return bool(redactor.findings)


def assert_persistable(value: Any, label: Sensitivity = Sensitivity.PUBLIC) -> None:
    """Raise when a payload must not enter durable memory or training data."""
    if label in NON_PERSISTABLE:
        raise PermissionError(f"{label.value} data must not be persisted.")
    if contains_secret(value):
        raise PermissionError("Detected credential material; this payload must not be persisted.")


def assert_transmittable(value: Any, label: Sensitivity, *, authorized: bool = False) -> None:
    """Raise when a payload must not leave the machine without exact authorization."""
    if label in NON_TRANSMITTABLE and not authorized:
        raise PermissionError(f"{label.value} data requires explicit authorization before network transmission.")
    if contains_secret(value) and not authorized:
        raise PermissionError("Detected credential material; explicit authorization is required before transmission.")
