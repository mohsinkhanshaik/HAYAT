"""Shared test fixtures.

One Tk interpreter for the whole session. Creating a fresh ``tk.Tk()`` in each
GUI test module, after the previous module destroyed its own, occasionally
failed to start on Windows; the module's fixture then skipped every test in
it as "Tk needs a display", so a run could report green with tests silently
not run. Modules now open a ``Toplevel`` on this one interpreter instead.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

# ------------------------------------------------------------ the owner's data
#
# Every store HAYAT remembers with -- chat memory, knowledge, authority, the
# event log the 4D brain is drawn from, tool reliability -- is a SQLite file
# under data/, opened through sqlite3.connect, and most default to that live
# folder. Tests that built the full runtime were writing into it: each full
# run left 8 fake chat turns in the owner's memory, ~180 authority audit rows
# and ~300 events. From here on, a test that opens a database under the live
# data/ folder gets a throwaway one in its place; the owner's files are never
# opened. data/work is excluded: it is scratch space, and older runs used it
# as their pytest base directory.
LIVE_DATA = (Path(__file__).resolve().parents[1] / "data").resolve()
# Shared through the environment: this module can be imported twice (as
# "conftest" by pytest and "tests.conftest" by helpers), and both must agree.
SANDBOX = Path(os.environ.setdefault("HAYAT_TEST_SANDBOX", tempfile.mkdtemp(prefix="hayat-test-data-")))
_real_connect = getattr(sqlite3.connect, "real_connect", sqlite3.connect)


def sandboxed(database):
    """Where a test's database really lives: a live data/ path moves to the sandbox."""
    if not isinstance(database, (str, Path)):
        return database
    text = str(database)
    if text == ":memory:" or text.startswith("file::memory:"):
        return database
    uri = text.startswith("file:")
    location, _, query = text[5:].partition("?") if uri else (text, "", "")
    try:
        path = Path(location).resolve()
        relative = path.relative_to(LIVE_DATA)
    except (OSError, ValueError):
        return database
    if relative.parts[:1] == ("work",):
        return database
    target = SANDBOX / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if not uri:
        return str(target)
    return f"file:{target.as_posix()}" + (f"?{query}" if query else "")


def _guarded_connect(database, *args, **kwargs):
    return _real_connect(sandboxed(database), *args, **kwargs)


_guarded_connect.real_connect = _real_connect
sqlite3.connect = _guarded_connect


@pytest.fixture(autouse=True)
def location_fix_does_not_leak_between_tests():
    """The location guard keeps the latest fix in memory (app/location_privacy.SHIELD). A test that reads a
    stand-in location must not leave it behind to round or hold another test's coordinates."""
    yield
    from app.location_privacy import SHIELD

    SHIELD.forget_fix()
    SHIELD._allow_until = 0.0
    SHIELD.events.clear()
    from app.privacy_masks import ROUTE

    ROUTE.set(None)  # a test that switched the Tor mask on must not route the next test's fetchers through it


@pytest.fixture(scope="session", autouse=True)
def owner_files_stay_untouched():
    """The few owner-visible files that are not databases, moved to the sandbox too.

    The two real-engine image tests had left 616 entries in the owner's
    gallery manifest ("a red square", "a single green leaf"), each pointing
    at a picture since deleted. Model and engine folders are left alone:
    those tests read the real installed model on purpose.
    """
    patch = pytest.MonkeyPatch()
    from app.perception import faces
    patch.setattr(faces, 'GALLERY_PATH', SANDBOX / 'faces' / 'gallery.json')
    original_face_gallery = faces.FaceGallery.__init__
    def _sandboxed_face_gallery(self, path=None, *args, **kwargs):
        requested = Path(path if path is not None else faces.GALLERY_PATH)
        resolved = requested.resolve()
        live = resolved == LIVE_DATA or LIVE_DATA in resolved.parents
        target = SANDBOX / resolved.relative_to(LIVE_DATA) if live else requested if path is not None else None
        original_face_gallery(self, target, *args, **kwargs)
    patch.setattr(faces.FaceGallery, '__init__', _sandboxed_face_gallery)
    from app import privacy_masks
    patch.setattr(privacy_masks, "SETTINGS_FILE", SANDBOX / "privacy-masks.json")
    try:
        from app.models import diffusion

        patch.setattr(diffusion, "IMAGE_ROOT", SANDBOX / "artifacts" / "images")
        patch.setattr(diffusion, "IMAGE_MANIFEST", SANDBOX / "artifacts" / "images" / "manifest.jsonl")
        patch.setattr(diffusion, "VIDEO_ROOT", SANDBOX / "artifacts" / "videos")
    except Exception:
        pass
    try:
        from app import projects

        original = projects.ProjectManager.__init__
        patch.setattr(projects.ProjectManager, "__init__",
                      lambda self, path=None: original(self, path or SANDBOX / "memory" / "projects.json"))
    except Exception:
        pass
    try:
        from app import gui

        patch.setattr(gui.HayatGUI, "_workspace_file", property(lambda self: SANDBOX / "workspace.json"))
        patch.setattr(gui.HayatGUI, "_panels_file", property(lambda self: SANDBOX / "gui-panels.json"))
    except Exception:
        pass
    try:
        from app.voice import manager as voice

        # Only the voice log is built from PROJECT_ROOT in that module.
        patch.setattr(voice, "PROJECT_ROOT", SANDBOX / "project")
    except Exception:
        pass
    try:
        from app import logging as task_logging

        # Task logs: likewise the module's only use of PROJECT_ROOT.
        patch.setattr(task_logging, "PROJECT_ROOT", SANDBOX / "project")
    except Exception:
        pass
    try:
        from app.safety import confirmations

        # The confirmed-actions audit log is a JSONL file beside the memory
        # database; a test that proposes an action must not write into it.
        from app.config import PROJECT_ROOT as _ROOT

        original_gate = confirmations.ConfirmationGate.__init__

        def _sandboxed_gate(self, log_path=None):
            live = log_path is not None and (_ROOT / "data") in Path(log_path).resolve().parents
            original_gate(self, SANDBOX / "memory" / "confirmed-actions.jsonl" if live else log_path)
        patch.setattr(confirmations.ConfirmationGate, "__init__", _sandboxed_gate)
    except Exception:
        pass
    try:
        from app.jobs import center as job_center

        # The job profile (the owner's CV reading) and application packets are files, not databases.
        original_center = job_center.JobCenter.__init__

        def _sandboxed_center(self, folder, *args, **kwargs):
            live = (LIVE_DATA in Path(folder).resolve().parents) or Path(folder).resolve() == LIVE_DATA
            original_center(self, SANDBOX / "jobs" if live else folder, *args, **kwargs)
        patch.setattr(job_center.JobCenter, "__init__", _sandboxed_center)
    except Exception:
        pass
    try:
        from app import networking

        # Networking records are account-encrypted files rather than SQLite;
        # sqlite's live-data redirect cannot protect the owner's address book.
        original_networking = networking.NetworkingBook.__init__

        def _sandboxed_networking(self, directory, *args, **kwargs):
            resolved = Path(directory).resolve()
            live = resolved == LIVE_DATA or LIVE_DATA in resolved.parents
            target = SANDBOX / resolved.relative_to(LIVE_DATA) if live else directory
            original_networking(self, target, *args, **kwargs)
        patch.setattr(networking.NetworkingBook, "__init__", _sandboxed_networking)
    except Exception:
        pass
    try:
        from app.research import record_vault

        # The authorized-record vault is one encrypted file beside the memory database. A GUI test's save and
        # delete rewrote the owner's data/memory/authorized-research/records.dpapi on every run (2026-10-24).
        original_vault = record_vault.RecordVault.__init__

        def _sandboxed_vault(self, directory, *args, **kwargs):
            resolved = Path(directory).resolve()
            live = resolved == LIVE_DATA or LIVE_DATA in resolved.parents
            original_vault(self, SANDBOX / resolved.relative_to(LIVE_DATA) if live else directory, *args, **kwargs)
        patch.setattr(record_vault.RecordVault, "__init__", _sandboxed_vault)
    except Exception:
        pass
    try:
        from app import csi

        # Wi-Fi CSI recordings and still-room baselines are files under data/sensing.
        patch.setattr(csi, "CSI_ROOT", SANDBOX / "sensing" / "csi")
    except Exception:
        pass
    try:
        from app import network

        # The IEEE maker list downloaded for device names is a file under data/network.
        patch.setattr(network, "NETWORK_ROOT", SANDBOX / "network")
    except Exception:
        pass
    try:
        from app import text_analysis

        # NLTK's language data is downloaded into data/nltk_data; a test must never fill the owner's copy.
        patch.setattr(text_analysis, "NLTK_DATA", SANDBOX / "nltk_data")
    except Exception:
        pass
    # The persona switch, languages seen/installed and the software install
    # journal are JSON files, not databases.
    for module_name in ("app.persona", "app.languages", "app.software"):
        try:
            import importlib

            patch.setattr(importlib.import_module(module_name), "PROJECT_ROOT", SANDBOX / "project")
        except Exception:
            pass
    yield
    patch.undo()


@pytest.fixture(scope="session")
def shared_tk():
    tkinter = pytest.importorskip("tkinter")
    try:
        window = tkinter.Tk()
    except tkinter.TclError as exc:
        pytest.skip(f"Tk needs a display: {exc}")
    window.withdraw()
    # HAYAT runs DPI-unaware, so Windows gives its Tk 96 DPI: a scaling of 96/72 points. Importing
    # pyautogui makes a process DPI-aware, and once any earlier test had imported it, this shared Tk
    # started at 1.67 on the owner's 125 % display: every window's text grew a quarter compared with
    # the app, and a 760 x 580 layout test lost its map in full runs only. Tests lay out like the app.
    if sys.platform == "win32":
        window.tk.call("tk", "scaling", 96 / 72)
    window._hayat_initial_scaling = window.tk.call("tk", "scaling")
    from app.gui_resources import manage_tk_collection
    manage_tk_collection(window)
    # HayatGUI adopts an unclaimed root that is already standing (so the app
    # never shows a second blank window) and destroys it on close. Left
    # unclaimed, this interpreter was adopted by the test_gui window and torn
    # down with it, and every GUI test after that errored.
    try:
        from app import gui as gui_module

        gui_module._CLAIMED_ROOTS.add(id(window))
    except Exception:
        pass
    yield window
    try:
        window.destroy()
    except tkinter.TclError:
        pass


@pytest.fixture(autouse=True)
def _reset_shared_text_scale(request):
    """Put the interpreter-wide ttk text sizes back after each GUI test.

    A large-text preview resizes the named ttk styles (button, label and table
    fonts, row height) for every window on the interpreter. Unreset, one
    test's 1.5x left the image-research and contact-discovery layout tests
    with evidence panes 8-50 px short, in full runs only.
    """
    yield
    if "shared_tk" not in request.fixturenames:
        return
    try:
        from app.gui_theme import style_text_scale

        style_text_scale(request.getfixturevalue("shared_tk"), 1.0)
    except Exception:
        pass  # the interpreter may already be gone; nothing is left to reset


def open_window(shared_tk, geometry: str | None = None, *, hidden: bool = False):
    """A test window on the shared interpreter: off screen, or withdrawn."""
    import tkinter

    initial = getattr(shared_tk, "_hayat_initial_scaling", None)
    if initial is not None:
        shared_tk.tk.call("tk", "scaling", initial)
    window = tkinter.Toplevel(shared_tk)
    if geometry:
        window.geometry(geometry)
    if hidden:
        window.withdraw()
    window.update_idletasks()
    return window


def close_window(window) -> None:
    import tkinter

    try:
        window.destroy()
    except tkinter.TclError:
        pass
