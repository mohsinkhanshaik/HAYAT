"""Confirming the owner is present, with Windows Hello.

This is a *verification*, not a search. It asks Windows to prove that the
person at the keyboard is the one already signed in to this machine, using
the fingerprint, face or PIN they enrolled with Windows itself. HAYAT never
sees the fingerprint or the face: Windows keeps the biometric templates and
returns a single yes or no. Nobody but the signed-in owner is ever enrolled,
and nothing here looks anyone up or matches anyone against a database.

It builds on `winsdk` -- the same official Microsoft WinRT projection the
local OCR already uses -- so it needs no download and no network call. The
API is Windows.Security.Credentials.UI.UserConsentVerifier, the documented
way for an app to ask "is the real user still here?" before a sensitive step.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any

from app.logging import log_swallowed

_log = logging.getLogger(__name__)


#: Windows.Security.Credentials.UI.UserConsentVerifierAvailability, by value.
#: (ok, plain reason). ok=True only when Hello can actually prompt.
_AVAILABILITY: dict[int, tuple[bool, str]] = {
    0: (True, "Windows Hello is set up and ready."),
    1: (False, "This device has no fingerprint reader, camera or PIN that Windows Hello can use."),
    2: (False, "Windows Hello is not set up for the signed-in user. Add a PIN, fingerprint or face in Windows Settings → Accounts → Sign-in options."),
    3: (False, "Windows Hello has been turned off by a device policy."),
    4: (False, "The Windows Hello device is busy. Try again in a moment."),
}

#: Windows.Security.Credentials.UI.UserConsentVerificationResult, by value.
_RESULT: dict[int, tuple[bool, str]] = {
    0: (True, "Verified."),
    1: (False, "No Windows Hello device is present."),
    2: (False, "Windows Hello is not set up for the signed-in user."),
    3: (False, "Windows Hello has been turned off by a device policy."),
    4: (False, "The Windows Hello device is busy."),
    5: (False, "Too many failed attempts; verification was stopped."),
    6: (False, "Verification was cancelled."),
}


def _await(coroutine: Any) -> Any:
    """Run a WinRT coroutine whether or not this thread already owns a loop.

    Same shape as app/perception/camera/ocr.py: ``asyncio.run`` refuses when a
    loop is already running on this thread, so that case is handed to a free
    one.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine)
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(1, thread_name_prefix="hello-await") as pool:
        return pool.submit(asyncio.run, coroutine).result()


class OwnerPresence:
    """Ask Windows Hello to confirm the signed-in owner is present."""

    def __init__(self) -> None:
        self._error: str | None = None
        self._verifier: Any = None
        if sys.platform != "win32":
            self._error = "Windows Hello verification requires Windows."
            return
        try:
            from winsdk.windows.security.credentials.ui import UserConsentVerifier

            self._verifier = UserConsentVerifier
        except Exception as exc:  # pragma: no cover -- winsdk always present on target
            self._error = f"{type(exc).__name__}: {exc}"

    # -- the two raw WinRT calls, isolated so tests can stand in for them ----

    def _check_availability(self) -> int:
        from winsdk.windows.security.credentials.ui import UserConsentVerifier

        return int(_await(UserConsentVerifier.check_availability_async()))

    def _request(self, message: str) -> int:
        from winsdk.windows.security.credentials.ui import UserConsentVerifier

        return int(_await(UserConsentVerifier.request_verification_async(message)))

    # -- what the rest of HAYAT calls ---------------------------------------

    def status(self) -> dict[str, Any]:
        """Whether Hello can prompt here, without prompting."""
        if self._error is not None:
            return {"available": False, "reason": self._error, "provider": "windows_hello"}
        try:
            code = self._check_availability()
        except Exception as exc:
            log_swallowed(_log, "checking Windows Hello availability")
            return {"available": False, "reason": f"{type(exc).__name__}: {exc}", "provider": "windows_hello"}
        ok, reason = _AVAILABILITY.get(code, (False, f"Unknown availability code {code}."))
        return {"available": ok, "reason": reason, "code": code, "provider": "windows_hello"}

    def verify(self, reason: str = "") -> dict[str, Any]:
        """Prompt Windows Hello and report whether the owner confirmed.

        `reason` is the one line Windows shows the owner; it is the only thing
        sent into the prompt. The result is a yes/no plus a plain explanation.
        """
        message = " ".join(str(reason or "").split()) or "HAYAT needs to confirm it's you before continuing."
        if self._error is not None:
            return {"verified": False, "reason": self._error, "prompt": message, "provider": "windows_hello"}
        availability = self.status()
        if not availability.get("available"):
            return {"verified": False, "reason": availability.get("reason", "Windows Hello is unavailable."),
                    "prompt": message, "provider": "windows_hello"}
        try:
            code = self._request(message)
        except Exception as exc:
            log_swallowed(_log, "requesting Windows Hello verification")
            return {"verified": False, "reason": f"{type(exc).__name__}: {exc}", "prompt": message, "provider": "windows_hello"}
        ok, reason_text = _RESULT.get(code, (False, f"Unknown verification code {code}."))
        return {"verified": ok, "reason": reason_text, "code": code, "prompt": message, "provider": "windows_hello"}


#: One process-wide instance is enough; the verifier holds no state.
_presence: OwnerPresence | None = None


def owner_presence() -> OwnerPresence:
    global _presence
    if _presence is None:
        _presence = OwnerPresence()
    return _presence
