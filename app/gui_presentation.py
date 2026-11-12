"""Presentation-only credential masking; editable fields retain their own data.

This is a screen/output boundary, not a substitute for access control or a
general personal-information detector. Never use the masked text as a tool input.
"""
from __future__ import annotations

import re
from urllib.parse import unquote

from app.safety.sensitive import environment_secrets, redact_text


_PARAMETER = re.compile(r"([?&#;])([^\s?&#;=]+)=([^\s&#;]*)")
_PRIVATE_PARAMETER = re.compile(
    r"(?i)^(?:.*(?:token|secret|password|passwd|credential|signature|api[_-]?key|session[_-]?id)|"
    r"auth|authorization|code|sig|key|pwd|access[_-]?key|x-amz-.*|x-goog-.*)$"
)
_USERINFO = re.compile(r"(?i)(\b[a-z][a-z0-9+.-]*://)[^\s/@]+@")
_MARKER = re.compile(r"\[REDACTED[^\]\n]*(?:\]|$)")
_HEADER = re.compile(r"(?im)^(\s*(?:set-cookie|cookie|authorization|proxy-authorization)\s*[:=])[^\r\n]+")
_JSON_SECRET = re.compile(r'''(?is)(["'](?:api[_-]?key|token|access[_-]?token|password|secret|cookie|authorization)["']\s*:\s*)(["'])((?:\\.|(?!\2)[^\\])*)\2''')


def safe_text(value) -> str:
    """Mask known secret shapes, environment literals and URL credentials.

    Query parameter names are decoded only for classification; the original
    surrounding text is retained. Even short authorization codes are private.
    """
    text = str(value if value is not None else "")
    def parameter(match):
        key = match[2]
        for _ in range(3):
            key = unquote(key)
        if _PRIVATE_PARAMETER.fullmatch(key) and match[3]:
            return match[1] + match[2] + "=[hidden]"
        return match[0]
    text = _PARAMETER.sub(parameter, text)
    text = _USERINFO.sub(r"\1[hidden]@", text)
    text = _HEADER.sub(r"\1 [hidden]", text)
    text = _JSON_SECRET.sub(lambda match: match[1] + match[2] + "[hidden]" + match[2], text)
    text = redact_text(text, extra_values=environment_secrets(), include_digest=False)[0]
    return _MARKER.sub("[hidden]", text)


def diagnostic_text(value, limit=400) -> str:
    """Bound a diagnostic preview after masking, with no home-directory leak."""
    from pathlib import Path

    text = safe_text(value)
    home = str(Path.home())
    for prefix in (home, home.replace("\\", "/")):
        text = re.sub(re.escape(prefix) + r"(?=[/\\\s]|$)", "~", text, flags=re.I)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:max(0, limit - 1)] + "…"


