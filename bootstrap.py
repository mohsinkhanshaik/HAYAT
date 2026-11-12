#!/usr/bin/env python3
"""One-command, cross-platform setup for HAYAT.

Copy the project onto a fresh Windows, macOS, or Linux machine and run:

    python bootstrap.py

It creates the project virtual environment, installs every Python dependency,
makes the data folders, seeds the .env, checks for Ollama (the local model
runtime) and best-effort provisions the models, then runs the project doctor.
It is safe to run more than once -- each step is a no-op when already done.

Only the standard library is used here, because this is the script that installs
everything else: it has to run before any dependency exists.
"""
from __future__ import annotations

import os
import platform
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
IS_WINDOWS = os.name == "nt"
VENV = ROOT / ".venv"
VENV_PYTHON = VENV / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")
VENV_MARKER = VENV / ".hayat-platform.json"
MIN_PYTHON = (3, 11)

DATA_FOLDERS = ("memory", "tasks", "logs", "skills", "artifacts", "work")


def say(message: str) -> None:
    print(f"\n\033[1m==> {message}\033[0m" if not IS_WINDOWS else f"\n==> {message}")


def run(command: list[str], *, check: bool = True, **kwargs) -> int:
    print("    $ " + " ".join(str(part) for part in command))
    result = subprocess.run(command, cwd=str(ROOT), **kwargs)
    if check and result.returncode != 0:
        raise SystemExit(f"\nCommand failed ({result.returncode}): {' '.join(map(str, command))}")
    return result.returncode


def check_python() -> None:
    if sys.version_info < MIN_PYTHON:
        raise SystemExit(
            f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ is required; this is "
            f"{sys.version.split()[0]}. Install a newer Python and rerun.")
    say(f"Python {sys.version.split()[0]} on {platform.system()} -- OK")


def host_fingerprint() -> dict[str, str]:
    """Fields that determine whether an environment's native wheels can run."""
    return {
        "system": platform.system().casefold(),
        "machine": platform.machine().casefold(),
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
    }


def inspect_venv(python_path: Path = VENV_PYTHON) -> tuple[bool, dict[str, str] | None]:
    """Probe the venv interpreter itself; never trust a copied path layout."""
    if not python_path.is_file():
        return False, None
    probe = (
        "import json,platform,sys;print(json.dumps({"
        "'system':platform.system().casefold(),'machine':platform.machine().casefold(),"
        "'python':f'{sys.version_info.major}.{sys.version_info.minor}'}))"
    )
    try:
        result = subprocess.run(
            [str(python_path), "-c", probe], cwd=str(ROOT), capture_output=True,
            text=True, timeout=20, check=False,
        )
        detected = json.loads(result.stdout.strip()) if result.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        detected = None
    return detected == host_fingerprint(), detected


def write_venv_marker() -> None:
    VENV_MARKER.write_text(json.dumps(host_fingerprint(), indent=2) + "\n", encoding="utf-8")


def archive_incompatible_venv() -> Path:
    """Keep an incompatible copied environment recoverable instead of deleting it."""
    suffix = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = ROOT / f".venv-incompatible-{platform.system().lower()}-{suffix}"
    counter = 2
    while target.exists():
        target = ROOT / f".venv-incompatible-{platform.system().lower()}-{suffix}-{counter}"
        counter += 1
    VENV.rename(target)
    return target


def make_venv() -> None:
    compatible, detected = inspect_venv()
    if compatible:
        write_venv_marker()
        say("Virtual environment matches this operating system and Python")
        return
    if VENV.exists():
        say("Copied virtual environment is incompatible with this host")
        archived = archive_incompatible_venv()
        print(f"    Preserved it as {archived.name}; creating native dependencies here.")
        if detected:
            print(f"    Copied environment: {detected}; current host: {host_fingerprint()}")
    say("Creating the project virtual environment (.venv)")
    run([sys.executable, "-m", "venv", str(VENV)])
    write_venv_marker()


def lock_for_this_host() -> Path | None:
    """The tested-version lock, when it was recorded on this kind of host.

    Pins are platform- and Python-specific (wheels differ), so a lock from
    Windows/3.14 must not constrain a Linux or 3.13 install; that falls back
    to the ranges in requirements.txt.
    """
    lock = ROOT / "requirements-lock.txt"
    if not lock.is_file():
        return None
    tag = f"{sys.platform}-py{sys.version_info.major}.{sys.version_info.minor}"
    for line in lock.read_text(encoding="utf-8").splitlines()[:10]:
        if line.startswith("# host:"):
            return lock if line.split(":", 1)[1].strip() == tag else None
    return None


def install_dependencies() -> None:
    say("Installing Python dependencies (this can take a few minutes)")
    run([str(VENV_PYTHON), "-m", "pip", "install", "--upgrade", "pip"])
    command = [str(VENV_PYTHON), "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")]
    if lock := lock_for_this_host():
        print(f"    Using the tested versions in {lock.name}")
        command += ["-c", str(lock)]
    run(command)


def make_data_folders() -> None:
    say("Creating data folders")
    for name in DATA_FOLDERS:
        (ROOT / "data" / name).mkdir(parents=True, exist_ok=True)
    print("    " + ", ".join(f"data/{name}" for name in DATA_FOLDERS))


def seed_env() -> None:
    env, example = ROOT / ".env", ROOT / ".env.example"
    if env.exists() or not example.exists():
        return
    say("Creating .env from .env.example")
    shutil.copyfile(example, env)
    print("    Add API keys only if you choose to use cloud models; the default is $0 local.")


def ollama_install_hint() -> str:
    system = platform.system()
    if system == "Windows":
        return "  winget install Ollama.Ollama   (or download from https://ollama.com/download)"
    if system == "Darwin":
        return "  brew install ollama            (or download from https://ollama.com/download)"
    return "  curl -fsSL https://ollama.com/install.sh | sh"


#: The brains HAYAT can be built on. ``hayat:latest`` is always the name the app uses; only its FROM differs.
BRAINS = {
    "35b": {"label": "HAYAT 35B-A3B (uncensored, vision; the full brain)",
            "pull": "hf.co/Mohsinkhanshaik/HAYAT-35B-A3B-Uncensored-GGUF:Q4_K_M",
            "base": "qwen3.6-35b-a3b-uncensored:q4_k_m", "download": "22 GB", "min_ram_gib": 30},
    "4b": {"label": "HAYAT Open 4B (small and fast; text only)",
           "pull": "hf.co/Mohsinkhanshaik/HAYAT-Open-4B:Q4_K_M",
           "base": "hf.co/Mohsinkhanshaik/HAYAT-Open-4B:Q4_K_M", "download": "2.5 GB", "min_ram_gib": 0},
}


def total_ram_gib() -> float | None:
    """This computer's installed memory, with the standard library only (None when it cannot be read)."""
    try:
        if IS_WINDOWS:
            import ctypes

            class MemoryStatus(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                            ("available", ctypes.c_ulonglong), ("page_total", ctypes.c_ulonglong),
                            ("page_available", ctypes.c_ulonglong), ("virtual_total", ctypes.c_ulonglong),
                            ("virtual_available", ctypes.c_ulonglong), ("extended", ctypes.c_ulonglong)]

            status = MemoryStatus()
            status.length = ctypes.sizeof(MemoryStatus)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return None
            return status.total / 1024**3
        if platform.system() == "Darwin":
            output = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, check=False)
            return int(output.stdout.strip()) / 1024**3
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024**3
    except (OSError, ValueError, AttributeError):
        return None


def installed_models() -> set[str]:
    """Model names Ollama already has (`ollama list`); empty when it cannot be asked."""
    try:
        output = subprocess.run(["ollama", "list"], capture_output=True, text=True, check=False, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return set()
    names = set()
    for line in output.stdout.splitlines()[1:]:
        if line.split():
            name = line.split()[0]
            names.add(name.lower())
            if name.lower().endswith(":latest"):
                names.add(name.lower().removesuffix(":latest"))
    return names


def choose_brain(ram_gib: float | None, installed: set[str], wanted: str = "") -> tuple[str, str]:
    """Which brain to build, and why: a brain already installed wins; otherwise the machine's memory decides.

    HAYAT_BRAIN=35b or HAYAT_BRAIN=4b overrides the choice.
    """
    wanted = (wanted or "").strip().lower()
    if wanted in BRAINS:
        return wanted, f"you chose it (HAYAT_BRAIN={wanted})"
    if BRAINS["35b"]["base"] in installed:
        return "35b", "its model is already installed"
    if ram_gib is None:
        return "4b", "this computer's memory could not be read, so the small brain is the safe start"
    if ram_gib >= BRAINS["35b"]["min_ram_gib"]:
        return "35b", f"this computer has {ram_gib:.0f} GB of memory (the full brain needs about 30)"
    return "4b", (f"this computer has {ram_gib:.0f} GB of memory; the full brain needs about 30, so the small "
                  "one runs well instead (set HAYAT_BRAIN=35b to try the full one anyway)")


def brain_modelfile(key: str) -> Path:
    """HAYAT's Modelfile built on the chosen brain: the same identity and settings, only FROM changes."""
    source = ROOT / "models" / "hayat" / "Modelfile"
    if key == "35b":
        return source
    lines = source.read_text(encoding="utf-8").splitlines()
    swapped = [f"FROM {BRAINS[key]['base']}" if line.startswith("FROM ") else line for line in lines]
    # The full brain's vision projector has no counterpart on the small model: keep one FROM line only.
    first = next(index for index, line in enumerate(swapped) if line.startswith("FROM "))
    swapped = [line for index, line in enumerate(swapped) if not line.startswith("FROM ") or index == first]
    target = ROOT / "data" / "models" / f"hayat-{key}.Modelfile"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(swapped) + "\n", encoding="utf-8")
    return target


def provision_brain() -> None:
    key, reason = choose_brain(total_ram_gib(), installed_models(), os.environ.get("HAYAT_BRAIN", ""))
    brain = BRAINS[key]
    print(f"    Brain: {brain['label']} - {reason}.")
    if brain["base"] not in installed_models():
        print(f"    Downloading it ({brain['download']}); this is the longest step and can be resumed by rerunning.")
        if run(["ollama", "pull", brain["pull"]], check=False) != 0:
            print("    The download did not finish. Rerun setup to resume it.")
            return
        if brain["base"] != brain["pull"]:
            run(["ollama", "cp", brain["pull"], brain["base"]], check=False)
    run(["ollama", "create", "hayat:latest", "-f", str(brain_modelfile(key))], check=False)


def provision_models() -> None:
    say("Checking for Ollama (the local model runtime)")
    if shutil.which("ollama") is None:
        print("    Ollama is not installed yet. HAYAT needs it for its local models.")
        print("    Install it with:")
        print(ollama_install_hint())
        print("    Then rerun this script, or provision models yourself (see README).")
        return
    print("    Ollama found. Provisioning models (best-effort; failures are non-fatal).")
    # nomic-embed-text is the embedding model (about 270 MB); all-minilm (46 MB) is its fallback.
    run(["ollama", "pull", "nomic-embed-text"], check=False)
    run(["ollama", "pull", "all-minilm"], check=False)
    # Build HAYAT's identity model (hayat:latest) on the brain this computer can run.
    if (ROOT / "models" / "hayat" / "Modelfile").exists():
        provision_brain()
    print("    If any model failed, see config/models.yaml for what HAYAT expects and")
    print("    pull/build them with `ollama pull <name>` / `ollama create`.")


def provision_voice() -> None:
    say("Checking the portable Piper voice")
    code = (
        "from app.voice.tts import PiperTTS; ok,detail=PiperTTS({}).ensure_voice(download=True);"
        "print('    '+detail); raise SystemExit(0 if ok else 1)"
    )
    if run([str(VENV_PYTHON), "-c", code], check=False) != 0:
        print("    Voice download was not completed. HAYAT still runs; retry setup when online.")


def run_doctor() -> None:
    say("Running the project doctor")
    run([str(VENV_PYTHON), "-m", "scripts.doctor"], check=False)


def final_instructions() -> None:
    say("Setup complete")
    print("    Start on every operating system with:  python run.py")
    print("    The launcher selects GUI or headless CLI and repairs a copied .venv.")
    print("    HAYAT memory, knowledge, settings, models, and project data stay in this folder.")


def main() -> int:
    check_python()
    make_venv()
    install_dependencies()
    make_data_folders()
    seed_env()
    provision_models()
    provision_voice()
    run_doctor()
    final_instructions()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
