"""Copy what HAYAT needs into its own folder so it runs by itself.

    python scripts/make_portable.py            # copy, then make the shortcuts
    python scripts/make_portable.py --check    # say what is still missing

Copies, never moves: the machine's Python, its Ollama and every Ollama model
stay exactly where they are. Into ``runtime/`` go a copy of Python, of the
Ollama program (without its tray app, which would start a second server on
the default port), and of only the models in :data:`app.portable.MODELS`,
with the storage layers they share copied once. Re-running it copies only
what is missing or changed size, so an interrupted copy just resumes.

Standard library only; run it with any Python 3.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import portable  # noqa: E402

#: Not needed to run HAYAT: docs, the standard library's own test suite, the
#: system's own packages and pip wrappers.
PYTHON_SKIP = {"Doc", "Scripts", "site-packages", "__pycache__", "test"}
#: The tray app and uninstaller belong to the machine's install, not HAYAT's.
OLLAMA_SKIP = {"ollama app.exe", "unins000.dat", "unins000.exe", "unins000.msg", "Ollama.lnk", "app.ico"}
#: OCR needs tesseract.exe, its DLLs and tessdata; not its manual or uninstaller.
TESSERACT_SKIP = {"doc", "tesseract-uninstall.exe"}


def _copy_file(source: Path, target: Path) -> int:
    if target.is_file() and target.stat().st_size == source.stat().st_size:
        return 0
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".partial")
    shutil.copyfile(source, partial)
    shutil.copystat(source, partial)
    os.replace(partial, target)
    return source.stat().st_size


def copy_tree(source: Path, target: Path, skip: set[str]) -> int:
    copied = 0
    for folder, dirs, files in os.walk(source):
        dirs[:] = [name for name in dirs if name not in skip]
        here = Path(folder)
        for name in files:
            if name not in skip:
                copied += _copy_file(here / name, target / (here / name).relative_to(source))
    if "site-packages" in skip and (source / "Lib" / "site-packages").is_dir():
        (target / "Lib" / "site-packages").mkdir(parents=True, exist_ok=True)
    return copied


def python_source() -> Path | None:
    """The Python HAYAT's venv was built from, unless that is already the copy."""
    config = ROOT / ".venv" / "pyvenv.cfg"
    candidates = []
    if config.is_file():
        for line in config.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            if key.strip().lower() == "home":
                candidates.append(Path(value.strip()))
    candidates.append(Path(sys.base_prefix))
    home = portable.python_home(ROOT).resolve()
    for candidate in candidates:
        if (candidate / "python.exe").is_file() and candidate.resolve() != home:
            return candidate
    return None


def ollama_source() -> Path | None:
    found = shutil.which("ollama")
    candidates = [Path(found).parent] if found else []
    candidates.append(Path(os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama")))
    own = portable.ollama_executable(ROOT).parent.resolve()
    for candidate in candidates:
        if (candidate / "ollama.exe").is_file() and candidate.resolve() != own:
            return candidate
    return None


def tesseract_source() -> Path | None:
    """The installed Tesseract OCR folder, if any; optional, so None is not an error."""
    found = shutil.which("tesseract")
    candidates = [Path(found).parent] if found else []
    candidates.append(Path(os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR")))
    candidates.append(Path(os.path.expandvars(r"%ProgramFiles%\Tesseract-OCR")))
    own = portable.tesseract_executable(ROOT).parent.resolve()
    for candidate in candidates:
        if (candidate / "tesseract.exe").is_file() and candidate.resolve() != own:
            return candidate
    return None


def store_source() -> Path | None:
    own = portable.models_dir(ROOT).resolve()
    override = os.getenv("OLLAMA_MODELS")
    for candidate in ([Path(override)] if override else []) + [Path.home() / ".ollama" / "models"]:
        if candidate.is_dir() and candidate.resolve() != own:
            return candidate
    return None


def copy_models(source: Path, target: Path) -> int:
    copied = 0
    for name in portable.MODELS:
        if not portable.model_gap(target, name):
            # Already whole in HAYAT's store, e.g. a model imported straight
            # into it that the machine's Ollama never had.
            print(f"  {name}: already in place", flush=True)
            continue
        manifest = portable.manifest_path(source, name)
        if not manifest.is_file():
            raise SystemExit(f"{name} is not installed in {source}; pull it with Ollama first.")
        for digest, size in portable.manifest_digests(manifest):
            blob = portable.blob_path(source, digest)
            print(f"  {name}: {digest[:19]} ({size / 1e9:.2f} GB)", flush=True)
            copied += _copy_file(blob, portable.blob_path(target, digest))
        # The manifest last: a model is listed only once all its layers are in.
        copied += _copy_file(manifest, portable.manifest_path(target, name))
    return copied


def make_shortcut(path: Path) -> None:
    pythonw = portable.python_home(ROOT) / "pythonw.exe"
    script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:HAYAT_LNK); "
        "$s.TargetPath = $env:HAYAT_TARGET; $s.Arguments = $env:HAYAT_ARGS; "
        "$s.WorkingDirectory = $env:HAYAT_ROOT; $s.Description = 'HAYAT'; "
        "if (Test-Path $env:HAYAT_ICON) { $s.IconLocation = $env:HAYAT_ICON }; $s.Save()"
    )
    env = {**os.environ, "HAYAT_LNK": str(path), "HAYAT_TARGET": str(pythonw),
           "HAYAT_ARGS": f'"{ROOT / "launch.pyw"}"', "HAYAT_ROOT": str(ROOT),
           "HAYAT_ICON": str(ROOT / "app" / "assets" / "hayat.ico")}
    subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                   check=True, env=env, capture_output=True)


def desktop() -> Path | None:
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
         "[Environment]::GetFolderPath('Desktop')"], capture_output=True, text=True)
    folder = Path(completed.stdout.strip()) if completed.returncode == 0 and completed.stdout.strip() else None
    return folder if folder and folder.is_dir() else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="only report what is missing")
    parser.add_argument("--no-desktop", action="store_true", help="do not put a shortcut on the desktop")
    args = parser.parse_args(argv)
    if args.check:
        gaps = portable.missing(ROOT)
        print("HAYAT's folder is complete." if not gaps else "Missing:\n  " + "\n  ".join(gaps))
        return 0 if not gaps else 1
    total = 0
    source = python_source()
    if source is not None:
        print(f"Python: {source} -> {portable.python_home(ROOT)}", flush=True)
        total += copy_tree(source, portable.python_home(ROOT), PYTHON_SKIP)
    source = ollama_source()
    if source is not None:
        print(f"Ollama: {source} -> {portable.ollama_executable(ROOT).parent}", flush=True)
        total += copy_tree(source, portable.ollama_executable(ROOT).parent, OLLAMA_SKIP)
    source = tesseract_source()
    if source is not None:
        print(f"Tesseract OCR: {source} -> {portable.tesseract_executable(ROOT).parent}", flush=True)
        total += copy_tree(source, portable.tesseract_executable(ROOT).parent, TESSERACT_SKIP)
    source = store_source()
    if source is not None:
        print(f"Models: {source} -> {portable.models_dir(ROOT)}", flush=True)
        total += copy_models(source, portable.models_dir(ROOT))
    print(f"Copied {total / 1e9:.2f} GB.")
    gaps = portable.missing(ROOT)
    if gaps:
        print("Still missing:\n  " + "\n  ".join(gaps))
        return 1
    if portable.repair_venv(ROOT):
        print(f".venv now uses {portable.python_home(ROOT)}")
    if os.name == "nt":
        make_shortcut(ROOT / "HAYAT.lnk")
        print(f"Shortcut: {ROOT / 'HAYAT.lnk'}")
        folder = None if args.no_desktop else desktop()
        if folder is not None:
            make_shortcut(folder / "HAYAT.lnk")
            print(f"Shortcut: {folder / 'HAYAT.lnk'}")
    print("HAYAT's folder is complete: open HAYAT.lnk to run it by itself.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
