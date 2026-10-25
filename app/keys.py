"""Free developer keys the owner can add: which exist, what each unlocks, and safe local storage.

HAYAT cannot register these accounts: each is the owner's own sign-up with the provider
(a free form and an email). Once a key is pasted into Studios → Keys it is written to the
project's .env (local, git-ignored), applied at once, and tested for real. Keys are never
shown again in full, sent anywhere else, or put in chat or tool results.
"""
from __future__ import annotations

import os
import re
import threading
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT

ENV_PATH = PROJECT_ROOT / ".env"
_NAME = re.compile(r"HAYAT_KEY_[A-Z0-9_]{2,40}")
_lock = threading.Lock()


def catalog() -> list[dict[str, Any]]:
    """Every key HAYAT knows how to use, with where to get it and what it unlocks."""
    from app.intelligence.feeds import KEYED_511
    keys = [{"name": "HAYAT_KEY_FINNHUB", "title": "Finnhub · exact stock prices", "group": "Markets",
             "unlocks": "The exchange's own price beside each tokenized stock in the Trade Center and chat.",
             "signup": "https://finnhub.io/register", "test": "finnhub"},
            {"name": "HAYAT_KEY_WSDOT", "title": "WSDOT · Washington highway cameras (access code)", "group": "Cameras",
             "unlocks": "Washington State's HighwayCameras API, alongside the keyless WSDOT map catalog.",
             "signup": "https://wsdot.wa.gov/traffic/api/", "test": "feed:cameras_wsdot"}]
    for name, host, title in KEYED_511:
        keys.append({"name": f"HAYAT_KEY_511_{name.upper()}", "title": f"{title} · traffic cameras", "group": "Cameras",
                     "unlocks": f"Every public camera on {title}.", "signup": f"https://{host}/developers/doc",
                     "test": f"feed:cameras_511_{name}"})
    return keys


def _valid(name: str, value: str) -> str:
    if not _NAME.fullmatch(name) or name not in {item["name"] for item in catalog()}:
        raise ValueError("Unknown key name.")
    value = str(value or "").strip()
    if not value or len(value) > 200 or not re.fullmatch(r"[A-Za-z0-9._~+/=-]+", value):
        raise ValueError("Paste the key exactly as the provider shows it (letters, digits and - _ . only, no spaces).")
    return value


def mask(value: str | None) -> str:
    if not value:
        return "not set"
    return "set · ends …" + value[-4:] if len(value) > 8 else "set"


def status(path: Path = ENV_PATH) -> list[dict[str, Any]]:
    values = read_env(path)
    return [{**item, "state": mask(os.environ.get(item["name"]) or values.get(item["name"]))} for item in catalog()]


def read_env(path: Path = ENV_PATH) -> dict[str, str]:
    values = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition("=")
            if separator and _NAME.fullmatch(key.strip()):
                values[key.strip()] = value.strip().strip('"')
    except FileNotFoundError:
        pass
    return values


def save(name: str, value: str, path: Path = ENV_PATH) -> str:
    """Write one key into .env (other lines untouched) and apply it now. Returns the masked state."""
    value = _valid(name, value)
    with _lock:
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        kept = [line for line in lines if line.partition("=")[0].strip() != name]
        kept.append(f"{name}={value}")
        partial = path.with_suffix(".partial")
        partial.write_text("\n".join(kept) + "\n", encoding="utf-8")
        partial.replace(path)
        os.environ[name] = value
    return mask(value)


def remove(name: str, path: Path = ENV_PATH) -> None:
    if name not in {item["name"] for item in catalog()}:
        raise ValueError("Unknown key name.")
    with _lock:
        if path.exists():
            lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.partition("=")[0].strip() != name]
            partial = path.with_suffix(".partial")
            partial.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
            partial.replace(path)
        os.environ.pop(name, None)


def test(name: str, runtime: Any) -> str:
    """Use the key for real once: a stock quote, or a refresh of the camera network it unlocks."""
    item = next((entry for entry in catalog() if entry["name"] == name), None)
    if item is None:
        raise ValueError("Unknown key name.")
    if item["test"] == "finnhub":
        quote = runtime.markets.exchange_quote("AAPL")
        if not quote:
            return "Finnhub answered without a price; check the key on finnhub.io."
        return f"Works: Apple {quote['price']:.2f} USD on the exchange ({quote['at'][11:16]} UTC)."
    feed = item["test"].split(":", 1)[1]
    result = runtime.intelligence.refresh([feed])
    source = next((entry for entry in result.get("sources", []) if entry.get("feed") == feed), {})
    if source.get("status") == "ok":
        return f"Works: {source.get('count', 0):,} cameras connected."
    return f"Did not work yet: {str(source.get('error') or source.get('status') or 'no answer')[:160]}"
