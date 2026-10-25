"""The codebase must import where the Windows-only packages are absent.

macOS and Linux do not have pywin32, pywinauto, winsdk, or comtypes -- they are
pinned to ``sys_platform == "win32"`` in requirements.txt. A single new
top-level ``import win32...`` or ``from pywinauto import ...`` would therefore
break every non-Windows install at startup, silently, because the Windows
suite would stay green. This reproduces the non-Windows condition in a fresh
interpreter -- those packages blocked -- and asserts the core modules still
import.

winreg is deliberately not blocked: it is a Windows stdlib module, but the one
third-party user of it (tzlocal) selects a unix backend off Windows and never
imports it there, so blocking it would only manufacture a false failure on this
Windows test host.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap

from app.config import PROJECT_ROOT

WINDOWS_ONLY_PACKAGES = [
    "win32api", "win32gui", "win32con", "win32clipboard", "win32process",
    "win32event", "win32file", "win32com", "pywinauto", "winsdk",
    "pythoncom", "pywintypes", "comtypes",
]

CORE_MODULES = [
    "main", "app.cli", "app.runtime", "app.gui", "app.assistant",
    "app.windows.applications", "app.tools.vision", "app.perception.clipboard",
    "app.tools.filesystem", "app.agent.conversation", "app.answers",
    # The setup entry points bootstrap.py and start.sh invoke -- they must run
    # on the machine being set up, which may not be Windows.
    "scripts.doctor", "scripts.init_db", "scripts.generate_reference",
]


def test_core_modules_import_without_windows_only_packages():
    script = textwrap.dedent(f"""
        import sys, importlib
        blocked = {WINDOWS_ONLY_PACKAGES!r}
        for name in list(sys.modules):
            if name.split(".")[0] in blocked:
                del sys.modules[name]
        class Blocker:
            def find_spec(self, name, path=None, target=None):
                if name.split(".")[0] in blocked:
                    raise ImportError("simulated-non-windows: " + name)
                return None
        sys.meta_path.insert(0, Blocker())
        for module in {CORE_MODULES!r}:
            importlib.import_module(module)
        print("ALL-IMPORTED")
    """)
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=str(PROJECT_ROOT),
        capture_output=True, text=True, timeout=180)
    assert "ALL-IMPORTED" in result.stdout, (
        "A core module failed to import with the Windows-only packages absent -- "
        "a new top-level Windows import has broken non-Windows startup.\n\n"
        + result.stdout + "\n" + result.stderr)


def test_headless_entrypoint_imports_without_tk_or_a_desktop_backend():
    script = textwrap.dedent("""
        import importlib, sys
        blocked = {'tkinter', 'tkinterdnd2', 'pyautogui'}
        class Blocker:
            def find_spec(self, name, path=None, target=None):
                if name.split('.')[0] in blocked:
                    raise ImportError('simulated-headless: ' + name)
                return None
        sys.meta_path.insert(0, Blocker())
        importlib.import_module('main')
        importlib.import_module('app.runtime')
        print('HEADLESS-IMPORTED')
    """)
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=str(PROJECT_ROOT),
        capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert "HEADLESS-IMPORTED" in result.stdout
