"""Download a pinned public foundation for the separate local HAYAT Open model.

No credentials, private corpus, uploads, remote code, or automatic model promotion.
Run using .venv-cuda. Large weights live in the ignored data/models directory.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "huihui-ai/Huihui-Qwen3-4B-Instruct-2507-abliterated"
REVISION = "c9bd464550d4078c72af0dd22aa18d0437868ce3"
DESTINATION = ROOT / "data/models/hayat-open-4b/foundation"


def main():
    os.environ.update(HF_HUB_DISABLE_TELEMETRY="1", DO_NOT_TRACK="1", HF_HUB_DISABLE_XET="1")
    from huggingface_hub import HfApi, snapshot_download

    info = HfApi(token=False).model_info(REPOSITORY, revision=REVISION, files_metadata=True)
    allowed = [entry.rfilename for entry in info.siblings
               if "/" not in entry.rfilename and (
                   entry.rfilename.endswith((".json", ".safetensors", ".jinja", ".txt"))
                   or entry.rfilename in {"README.md", "LICENSE"})]
    print(json.dumps({"stage": "download", "repository": REPOSITORY, "revision": REVISION,
                      "destination": str(DESTINATION),
                      "expected_bytes": sum(entry.size or 0 for entry in info.siblings if entry.rfilename in allowed)}), flush=True)
    snapshot_download(REPOSITORY, revision=REVISION, local_dir=DESTINATION,
                      allow_patterns=allowed, token=False, max_workers=2)
    records = []
    for entry in info.siblings:
        if entry.rfilename not in allowed:
            continue
        path = DESTINATION / entry.rfilename
        if path.stat().st_size != entry.size:
            raise ValueError(f"Size mismatch: {entry.rfilename}")
        with path.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        expected = getattr(entry.lfs, "sha256", None) if entry.lfs else None
        if expected and digest != expected:
            raise ValueError(f"SHA-256 mismatch: {entry.rfilename}")
        records.append({"file": entry.rfilename, "bytes": entry.size,
                        "sha256": digest, "publisher_lfs_sha256_verified": bool(expected)})
    provenance = {"repository": REPOSITORY, "revision": REVISION,
                  "files": records, "local_only": True, "trust_remote_code": False,
                  "training_completed": False, "active_hayat_changed": False}
    (DESTINATION.parent / "download-provenance.json").write_text(
        json.dumps(provenance, indent=2), encoding="utf-8")
    print(json.dumps({"stage": "verified", "files": len(records), "destination": str(DESTINATION)}), flush=True)


if __name__ == "__main__":
    main()
