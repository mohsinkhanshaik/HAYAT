"""Fetch the pinned Torchvision person detector; no additional inference service."""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from app.perception.people import MODEL, MODEL_SHA256, MODEL_URL
    from app.research.fetcher import PublicFetcher
    from app.tools.file_editing import atomic_save
    if MODEL.is_file() and hashlib.sha256(MODEL.read_bytes()).hexdigest() == MODEL_SHA256:
        print("The pinned person detector is already installed.")
        return
    fetcher = PublicFetcher("HAYAT-person-detector/1.0", timeout=60)
    try:
        resource = fetcher.fetch(MODEL_URL, max_bytes=40_000_000, accept=("application/octet-stream", "binary/octet-stream"))
        if hashlib.sha256(resource.content).hexdigest() != MODEL_SHA256:
            raise ValueError("Unexpected model SHA-256; the existing detector is unchanged.")
        atomic_save(MODEL, resource.content)
        print("Installed the pinned local person detector at", MODEL)
    finally:
        fetcher.close()


if __name__ == "__main__":
    main()
