from __future__ import annotations

import subprocess

from PIL import Image, ImageDraw

from app.config import PROJECT_ROOT
from app.voice.models import VoiceState


class VoiceTray:
    """Optional lightweight tray shell around the same VoiceManager."""

    def __init__(self, manager) -> None:
        self.manager = manager
        self.icon = None

    @staticmethod
    def _image(color: str = "#2563eb") -> Image.Image:
        icon_path = PROJECT_ROOT / "app" / "assets" / "hayat.ico"
        if icon_path.is_file():
            with Image.open(icon_path) as icon:
                return icon.convert("RGBA").resize((64, 64), Image.Resampling.LANCZOS)
        image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.ellipse((4, 4, 60, 60), fill=color)
        draw.text((20, 15), "AI", fill="white")
        return image

    def run(self) -> int:
        try:
            import pystray
        except ImportError as exc:
            raise RuntimeError("Tray support is not installed. Run .\\setup.ps1.") from exc
        import pystray
        menu = pystray.Menu(
            pystray.MenuItem("Open HAYAT", self._open),
            pystray.MenuItem("Start Listening", self._start), pystray.MenuItem("Stop Listening", self._stop),
            pystray.MenuItem("Mute Voice", self._mute), pystray.MenuItem("Unmute Voice", self._unmute),
            pystray.MenuItem("Open Settings", self._settings), pystray.MenuItem("Status", self._status),
            pystray.MenuItem("Exit", self._exit),
        )
        self.icon = pystray.Icon("AI", self._image(), "AI Ready", menu)
        self.manager.add_state_callback(self._state_changed)
        self.icon.run(setup=lambda icon: self.manager.start_background())
        return 0

    def _state_changed(self, state: VoiceState) -> None:
        if self.icon:
            self.icon.title = f"AI - {state.value.replace('_', ' ').title()}"

    def _start(self, icon=None, item=None) -> None:
        self.manager.start_background()

    def _open(self, icon=None, item=None) -> None:
        # Only one process owns the microphone. Hand it off before opening the
        # full desktop surface in a new process.
        self.manager.stop()
        subprocess.Popen(
            [str(PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"), str(PROJECT_ROOT / "main.py"), "--gui"],
            cwd=str(PROJECT_ROOT), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if self.icon:
            self.icon.stop()

    def _stop(self, icon=None, item=None) -> None:
        self.manager.stop()

    def _mute(self, icon=None, item=None) -> None:
        self.manager.process_transcript("mute")

    def _unmute(self, icon=None, item=None) -> None:
        self.manager.process_transcript("unmute")

    def _settings(self, icon=None, item=None) -> None:
        subprocess.Popen(["notepad.exe", str(PROJECT_ROOT / "config" / "settings.yaml")])

    def _status(self, icon=None, item=None) -> None:
        if self.icon:
            status = self.manager.status()
            self.icon.notify(f"State: {status['state']}\nMode: {status['mode']}\nMuted: {status['muted']}", "AI Status")

    def _exit(self, icon=None, item=None) -> None:
        self.manager.stop()
        if self.icon:
            self.icon.stop()
