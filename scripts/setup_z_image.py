"""Switch HAYAT's image generation to Z-Image Turbo, after proving it works on this machine.

    .\\.venv\\Scripts\\python.exe scripts\\setup_z_image.py            # verify, extract, test render, enable
    .\\.venv\\Scripts\\python.exe scripts\\setup_z_image.py --disable  # go back to SDXL

The model files and the newer stable-diffusion.cpp engine were downloaded on
2026-10-31 into data/models/diffusion/z-image/ and data/engines/, with their
sources and SHA-256 in data/models/diffusion/z-image/provenance.json. This
script checks every hash again, extracts the engine into its own folder, renders
one test picture with the documented Z-Image settings, and only if that picture
is a valid PNG writes ready.json, which is what makes HAYAT use Z-Image. The
SDXL engine and model are left untouched, and HAYAT_IMAGE_MODEL=sdxl in .env
switches back without undoing anything.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.models.diffusion import ENGINE_ROOT, Z_IMAGE_CFG, Z_IMAGE_ROOT, validate_png  # noqa: E402

TEST_PROMPT = "a wooden shop sign that says HAYAT above a cafe door, warm evening light"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def extract(archive: Path, folder: Path) -> Path:
    """Unpack the engine, refusing any member that would land outside its folder."""
    folder.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            target = (folder / member.filename).resolve()
            if folder.resolve() not in target.parents and target != folder.resolve():
                raise ValueError(f"unsafe path in engine archive: {member.filename}")
        bundle.extractall(folder)
    engine = next(iter(sorted(folder.rglob("sd-cli.exe")) or sorted(folder.rglob("sd-cli"))), None)
    if engine is None:
        raise FileNotFoundError("the engine archive holds no sd-cli")
    return engine


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--disable", action="store_true", help="remove ready.json so HAYAT uses SDXL again")
    args = parser.parse_args()
    ready_file = Z_IMAGE_ROOT / "ready.json"
    if args.disable:
        ready_file.unlink(missing_ok=True)
        print("Z-Image disabled: HAYAT uses SDXL again.")
        return 0
    provenance = json.loads((Z_IMAGE_ROOT / "provenance.json").read_text(encoding="utf-8"))
    for item in provenance["files"]:
        path = Z_IMAGE_ROOT / item["file"]
        print(f"checking {path.name} ...", flush=True)
        if not path.is_file() or sha256(path) != item["sha256"]:
            print(f"FAILED: {path.name} is missing or does not match its published SHA-256.")
            return 1
    archive = ROOT / provenance["engine"]["asset"]
    if not archive.is_file() or sha256(archive) != provenance["engine"]["sha256"]:
        print(f"FAILED: {archive.name} is missing or does not match its published SHA-256.")
        return 1
    folder = ENGINE_ROOT / f"stable-diffusion.cpp-{provenance['engine']['release']}-vulkan-x64"
    engine = extract(archive, folder)
    print(f"engine: {engine}")

    test = Z_IMAGE_ROOT / "setup-test.png"
    test.unlink(missing_ok=True)
    files = {name: Z_IMAGE_ROOT / item["file"] for name, item in
             zip(("model", "llm", "vae"), provenance["files"])}
    command = [str(engine), "--diffusion-model", str(files["model"]), "--vae", str(files["vae"]),
               "--llm", str(files["llm"]), "-p", TEST_PROMPT, "--cfg-scale", str(Z_IMAGE_CFG), "--steps", "8",
               "-W", "512", "-H", "512", "--offload-to-cpu", "-s", "7", "-o", str(test)]
    print("rendering a 512x512 test picture (the first run loads the models; allow a few minutes) ...", flush=True)
    started = time.perf_counter()
    run = subprocess.run(command, capture_output=True, text=True, timeout=1800,  # noqa: S603
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    seconds = round(time.perf_counter() - started, 1)
    valid, detail = validate_png(test, 512, 512) if test.is_file() else (False, "no picture was written")
    if run.returncode != 0 or not valid:
        tail = " ".join((run.stdout or "").strip().splitlines()[-4:])[:600]
        print(f"FAILED after {seconds}s: {detail}. Engine said: {tail}")
        print("HAYAT keeps using SDXL.")
        return 1
    ready_file.write_text(json.dumps({
        "engine": str(engine), "release": provenance["engine"]["release"], "test_image": str(test),
        "test_seconds": seconds, "verified_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }, indent=1) + "\n", encoding="utf-8")
    print(f"OK: Z-Image rendered a valid test picture in {seconds}s ({test}).")
    print("HAYAT now generates images with Z-Image Turbo. Restart HAYAT if it is running.")
    print("Look at the test picture; to go back, run this script with --disable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
