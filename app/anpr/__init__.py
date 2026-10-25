"""Automatic number plate recognition for the owner's own vehicles, authorized cameras and test footage.

Scope, by design:
- Reads plates from uploaded images and videos, USB webcams, and RTSP cameras
  the owner registers as authorized on a private network (app/anpr/sources.py).
  Public cameras, including Global Intelligence's traffic cameras, are refused.
- Stores plate text, confidence, time, source and an optional crop locally,
  with retention and deletion (app/anpr/store.py).
- Never identifies vehicle owners, never retrieves addresses, never connects
  to government or police databases, never issues notices or fines. Parking
  rule breaches are internal review events for a person to handle through
  their own authorized process.
"""
from __future__ import annotations

from pathlib import Path


def data_folder() -> Path:
    """data/anpr inside HAYAT's own folder (everything HAYAT loads lives in D:\\AI)."""
    from app.config import PROJECT_ROOT
    return Path(PROJECT_ROOT) / "data" / "anpr"


def open_system(folder: Path | None = None):
    """(store, pipeline) with old records purged to the retention period on opening."""
    from app.anpr.pipeline import AnprPipeline
    from app.anpr.store import AnprStore
    root = folder or data_folder()
    store = AnprStore(root)
    store.purge()
    return store, AnprPipeline(store, root.parent / "models" / "anpr" if folder is None else root / "models")
