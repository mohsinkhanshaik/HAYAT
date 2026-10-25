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


def provision_models() -> None:
    say("Checking for Ollama (the local model runtime)")
    if shutil.which("ollama") is None:
        print("    Ollama is not installed yet. HAYAT needs it for its local models.")
        print("    Install it with:")
        print(ollama_install_hint())
        print("    Then rerun this script, or provision models yourself (see README).")
        return
    print("    Ollama found. Provisioning models (best-effort; failures are non-fatal).")
    # all-minilm is the embedding model and is small and always available.
    run(["ollama", "pull", "all-minilm"], check=False)
    # Build HAYAT's identity model from its Modelfile. `ollama create` pulls the
    # FROM base automatically when it is a registry model.
    modelfile = ROOT / "models" / "hayat" / "Modelfile"
    if modelfile.exists():
        run(["ollama", "create", "hayat:latest", "-f", str(modelfile)], check=False)
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
