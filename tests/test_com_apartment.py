from __future__ import annotations

import os
import subprocess
import sys

import pytest


@pytest.mark.skipif(os.name != "nt", reason="Windows COM policy")
def test_gui_startup_declares_sta_before_pywinauto() -> None:
    completed = subprocess.run(
        [
            sys.executable, "-W", "error", "-c",
            "import sys, main, pywinauto; assert sys.coinit_flags == 2",
        ],
        cwd=os.getcwd(), capture_output=True, text=True, timeout=30,
    )
    assert completed.returncode == 0, completed.stderr


@pytest.mark.skipif(os.name != "nt", reason="Windows COM policy")
def test_gui_can_load_tkdnd_before_windows_automation() -> None:
    completed = subprocess.run(
        [
            sys.executable, "-W", "error", "-c",
            "from app.gui import reserve_window; "
            "assert reserve_window(); import pywinauto; "
            "assert __import__('sys').coinit_flags == 2",
        ],
        cwd=os.getcwd(), capture_output=True, text=True, timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
