"""HAYAT's own runtime, kept inside its folder so it runs by itself.

``runtime/`` holds a copy of Python, of the Ollama program, and of only the
models HAYAT uses. From there HAYAT runs its own Ollama on its own port
(:data:`PORT`), so it never touches, and is never disturbed by, an Ollama
installed on the machine. Nothing is moved out of the system install; the
folder has copies.

Standard library only: the launcher imports this before the virtual
environment is known to work.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
#: HAYAT's port, one above Ollama's default so the two never collide.
PORT = 11435
#: The models HAYAT needs: its identity model, that model's foundation (which also does vision and DEEP-mode
#: advice), and embeddings. Other installed models are optional.
MODELS = ("hayat:latest", "qwen3.6-35b-a3b-uncensored:q4_k_m", "all-minilm:latest")
#: Set to "0" to run against the machine's Ollama even when runtime/ exists.
SWITCH = "HAYAT_PORTABLE"


def runtime_dir(root: Path = ROOT) -> Path:
    return root / "runtime"


def python_home(root: Path = ROOT) -> Path:
    return runtime_dir(root) / "python"


def ollama_executable(root: Path = ROOT) -> Path:
    return runtime_dir(root) / "ollama" / ("ollama.exe" if os.name == "nt" else "ollama")


def tesseract_executable(root: Path = ROOT) -> Path:
    """Optional OCR fallback; Windows OCR is preferred, so its absence is not a gap."""
    return runtime_dir(root) / "tesseract" / ("tesseract.exe" if os.name == "nt" else "tesseract")


def models_dir(root: Path = ROOT) -> Path:
    return runtime_dir(root) / "ollama-models"


def base_url() -> str:
    return f"http://127.0.0.1:{PORT}"


def manifest_path(store: Path, name: str) -> Path:
    model, _, tag = name.partition(":")
    return store / "manifests" / "registry.ollama.ai" / "library" / model / (tag or "latest")


def blob_path(store: Path, digest: str) -> Path:
    return store / "blobs" / digest.replace(":", "-")


def manifest_digests(path: Path) -> list[tuple[str, int]]:
    """Every blob one model manifest needs: (digest, size), config first."""
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = [data.get("config") or {}, *(data.get("layers") or [])]
    return [(str(item["digest"]), int(item.get("size") or 0)) for item in entries if item.get("digest")]


def missing(root: Path = ROOT) -> list[str]:
    """What the folder still lacks to run by itself; empty when complete."""
    gaps: list[str] = []
    python = python_home(root) / ("python.exe" if os.name == "nt" else "bin/python3")
    if not python.is_file():
        gaps.append(f"Python ({python})")
    if not ollama_executable(root).is_file():
        gaps.append(f"Ollama ({ollama_executable(root)})")
    store = models_dir(root)
    for name in MODELS:
        gap = model_gap(store, name)
        if gap:
            gaps.append(gap)
    return gaps


def model_gap(store: Path, name: str) -> str:
    """What one model lacks in ``store``; empty when its manifest and every blob are in place."""
    manifest = manifest_path(store, name)
    if not manifest.is_file():
        return f"model {name}"
    try:
        needed = manifest_digests(manifest)
    except (OSError, ValueError, KeyError):
        return f"model {name} (unreadable manifest)"
    for digest, size in needed:
        blob = blob_path(store, digest)
        if not blob.is_file() or (size and blob.stat().st_size != size):
            return f"model {name} (blob {digest[:19]})"
    return ""


def complete(root: Path = ROOT) -> bool:
    return not missing(root)


def active(root: Path = ROOT) -> bool:
    """Whether this process should use the in-folder runtime."""
    choice = os.getenv(SWITCH, "").strip()
    if choice == "0":
        return False
    return choice == "1" or complete(root)


def environment(root: Path = ROOT) -> dict[str, str]:
    """What HAYAT and its Ollama need to find each other inside the folder."""
    return {
        SWITCH: "1",
        "OLLAMA_HOST": f"127.0.0.1:{PORT}",
        "OLLAMA_MODELS": str(models_dir(root)),
        "OLLAMA_NO_CLOUD": "1",
        "OLLAMA_BASE_URL": base_url(),
        # Flash attention and an 8-bit KV cache reduce context memory. The
        # current 34.7B identity also uses host RAM: its quantized weights do
        # not fit entirely on this 8 GB GPU. Earlier full-GPU/throughput
        # measurements were for the former 9.7B identity, not this model.
        # Set either variable yourself to override.
        "OLLAMA_FLASH_ATTENTION": os.environ.get("OLLAMA_FLASH_ATTENTION", "1"),
        "OLLAMA_KV_CACHE_TYPE": os.environ.get("OLLAMA_KV_CACHE_TYPE", "q8_0"),
    }


def activate(root: Path = ROOT) -> bool:
    """Point this process (and what it starts) at the in-folder runtime."""
    if not active(root):
        return False
    os.environ.update(environment(root))
    return True


def repair_venv(root: Path = ROOT) -> bool:
    """Point ``.venv`` at the Python in the folder; True if the file changed.

    A venv records the absolute path of the Python it was made from. Rewriting
    that record, rather than rebuilding the venv, keeps every installed
    package and lets the folder move to another drive and still start.
    """
    config = root / ".venv" / "pyvenv.cfg"
    home = python_home(root)
    if not config.is_file() or not (home / ("python.exe" if os.name == "nt" else "bin")).exists():
        return False
    before = config.read_text(encoding="utf-8")
    values = {"home": str(home), "executable": str(home / ("python.exe" if os.name == "nt" else "bin/python3"))}
    lines = []
    for line in before.splitlines():
        key = line.split("=", 1)[0].strip().lower()
        lines.append(f"{key} = {values.pop(key)}" if key in values else line)
    lines += [f"{key} = {value}" for key, value in values.items()]
    after = "\n".join(lines) + "\n"
    if after == before:
        return False
    config.write_text(after, encoding="utf-8")
    return True
