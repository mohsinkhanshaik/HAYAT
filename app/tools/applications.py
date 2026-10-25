from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT
from app.safety.permissions import PermissionManager
from app.tools.registry import ToolRegistry
from app.windows.applications import WindowsApplicationController


class ApplicationTools:
    ALIASES = {
        "chrome": ("chrome.exe", "google-chrome", "chromium", "chromium-browser",
                   "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        "edge": ("msedge.exe", "microsoft-edge", "microsoft-edge-stable",
                 "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
        "vscode": ("Code.exe", "code",
                   "/Applications/Visual Studio Code.app/Contents/MacOS/Electron"),
        "notepad": ("notepad.exe", "gedit", "kate", "mousepad", "xed",
                    "/Applications/TextEdit.app/Contents/MacOS/TextEdit"),
        "calculator": ("calc.exe", "gnome-calculator", "kcalc", "mate-calc",
                       "/System/Applications/Calculator.app/Contents/MacOS/Calculator"),
        "file_explorer": ("explorer.exe", "xdg-open", "open"),
        "whatsapp": ("WhatsApp.exe", "whatsapp", "/Applications/WhatsApp.app/Contents/MacOS/WhatsApp"),
        # Office registers App Paths, so these resolve without a PATH entry.
        "word": ("WINWORD.EXE", "/Applications/Microsoft Word.app/Contents/MacOS/Microsoft Word"),
        "excel": ("EXCEL.EXE", "/Applications/Microsoft Excel.app/Contents/MacOS/Microsoft Excel"),
        "powerpoint": ("POWERPNT.EXE", "/Applications/Microsoft PowerPoint.app/Contents/MacOS/Microsoft PowerPoint"),
        "outlook": ("OUTLOOK.EXE", "/Applications/Microsoft Outlook.app/Contents/MacOS/Microsoft Outlook"),
        "onenote": ("ONENOTE.EXE", "/Applications/Microsoft OneNote.app/Contents/MacOS/Microsoft OneNote"),
        "teams": ("ms-teams.exe", "teams", "/Applications/Microsoft Teams.app/Contents/MacOS/MSTeams"),
        "discord": ("Discord.exe", "discord", "/Applications/Discord.app/Contents/MacOS/Discord"),
        "zoom": ("Zoom.exe", "zoom", "/Applications/zoom.us.app/Contents/MacOS/zoom.us"),
        "telegram": ("Telegram.exe", "telegram-desktop", "/Applications/Telegram.app/Contents/MacOS/Telegram"),
        "signal": ("Signal.exe", "signal-desktop", "/Applications/Signal.app/Contents/MacOS/Signal"),
        "spotify": ("Spotify.exe", "spotify", "/Applications/Spotify.app/Contents/MacOS/Spotify"),
    }
    # Microsoft Store packages have no executable on PATH; they launch by
    # AppUserModelID through the shell's AppsFolder.
    STORE_APPS = {
        "whatsapp": "5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App",
    }
    # Start-menu names for apps whose launcher is not a plain executable
    # (Store packages, Squirrel installers such as Discord). Looked up in
    # shell:AppsFolder on this machine, because their IDs differ per install.
    START_MENU_NAMES = {
        "whatsapp": ("WhatsApp",), "teams": ("Microsoft Teams", "Teams"),
        "discord": ("Discord",), "zoom": ("Zoom Workplace", "Zoom"),
        "outlook": ("Outlook (classic)", "Outlook"), "onenote": ("OneNote",),
        "telegram": ("Telegram Desktop", "Telegram"), "signal": ("Signal",),
        "spotify": ("Spotify",),
    }

    def __init__(self, permissions: PermissionManager) -> None:
        self.permissions = permissions
        self.windows = WindowsApplicationController() if os.name == "nt" else PortableApplicationController()

    def discover(self) -> dict[str, Any]:
        result = {}
        for name in self.ALIASES:
            path = self._find(name)
            store = None if path else self._store_app(name)
            result[name] = {"available": bool(path or store), "path": str(path) if path else store}
        return result

    def launch(self, application: str, path: str | None = None) -> dict[str, Any]:
        normalized = application.lower().strip().replace(" ", "_")
        if normalized not in self.ALIASES:
            raise ValueError(f"Application is not on the approved launch list: {application}")
        executable = self._find(normalized)
        if not executable and (app_id := self._store_app(normalized)):
            existing = self.windows.windows(normalized)
            if existing:
                focused = self.windows.focus(normalized, existing[-1]["handle"])
                return {"application": normalized, "path": app_id, "pid": existing[-1]["pid"], "running": True, "launched": False, **focused}
            subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app_id}"], cwd=str(PROJECT_ROOT), close_fds=True)
            window = self.windows.wait_for_window(normalized, timeout=15.0)
            return {"application": normalized, "path": app_id, "pid": window["pid"] if window else None,
                    "running": bool(window), "launched": True, "window": window}
        if not executable:
            raise FileNotFoundError(f"Approved application is not installed or discoverable: {application}")
        existing = self.windows.windows(normalized)
        if existing and not path:
            focused = self.windows.focus(normalized, existing[-1]["handle"])
            return {"application": normalized, "path": str(executable), "pid": existing[-1]["pid"], "running": True, "launched": False, **focused}
        arguments = [str(executable)]
        if path:
            if normalized != "file_explorer":
                raise ValueError("Only File Explorer accepts a project path.")
            arguments.append(str(self.permissions.validate_project_path(path)))
        process = subprocess.Popen(arguments, cwd=str(PROJECT_ROOT), close_fds=True)
        window = self.windows.wait_for_window(normalized) if os.name == "nt" else None
        running = process.poll() is None or bool(window)
        return {"application": normalized, "path": str(executable), "pid": window["pid"] if window else process.pid, "running": running, "launched": True, "window": window}

    def state(self, application: str, handle: int | None = None) -> dict[str, Any]:
        return self.windows.state(application, handle)

    def notepad_edit(self, text: str, append_line: bool = False, handle: int | None = None) -> dict[str, Any]:
        return self.windows.write_notepad(text, append_line, handle)

    def close(self, application: str, handle: int | None = None, save_choice: str = "ask") -> dict[str, Any]:
        return self.windows.close(application, handle, save_choice)

    def open_project_folder(self) -> dict[str, Any]:
        return self.launch("file_explorer", str(PROJECT_ROOT))

    def _find(self, application: str) -> Path | None:
        if application not in self.ALIASES:
            raise ValueError(f"Application is not on the approved launch list: {application}")
        for executable in self.ALIASES[application]:
            candidate = Path(executable).expanduser()
            if candidate.is_absolute() and candidate.is_file():
                return candidate.resolve()
            if found := shutil.which(executable):
                return Path(found).resolve()
            if found := self._app_path(executable):
                return found
        return None

    def _store_app(self, application: str) -> str | None:
        """The AppUserModelID to launch through the shell, when installed here."""
        if os.name != "nt":
            return None
        app_id = self.STORE_APPS.get(application)
        if app_id:
            family = app_id.split("!", 1)[0]
            if (Path(os.environ.get("LOCALAPPDATA", "")) / "Packages" / family).is_dir():
                return app_id
        installed = self._start_menu_apps()
        for name in self.START_MENU_NAMES.get(application, ()):
            if name.casefold() in installed:
                return installed[name.casefold()]
        return None

    _start_menu_cache: dict[str, str] | None = None

    @classmethod
    def _start_menu_apps(cls) -> dict[str, str]:
        """Start-menu display name -> AppUserModelID, read once per process."""
        if cls._start_menu_cache is None:
            apps: dict[str, str] = {}
            try:
                import pythoncom
                import win32com.client

                pythoncom.CoInitialize()
                folder = win32com.client.Dispatch("Shell.Application").NameSpace("shell:AppsFolder")
                for item in folder.Items():
                    apps.setdefault(str(item.Name).casefold(), str(item.Path))
            except Exception:
                apps = {}
            cls._start_menu_cache = apps
        return cls._start_menu_cache

    @staticmethod
    def _app_path(executable: str) -> Path | None:
        """Resolve Windows App Paths without making it a portable dependency."""
        if os.name != "nt":
            return None
        try:
            import winreg
            for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                for key_name in (
                    rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{executable}",
                    rf"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\{executable}",
                ):
                    try:
                        with winreg.OpenKey(hive, key_name) as key:
                            candidate = Path(winreg.QueryValue(key, None))
                            if candidate.is_file():
                                return candidate.resolve()
                    except OSError:
                        continue
        except ImportError:
            return None
        roots = [Path(os.environ[name]) for name in
                 ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA", "APPDATA", "WINDIR")
                 if os.environ.get(name)]
        relative_candidates = {
            "chrome.exe": (Path("Google/Chrome/Application/chrome.exe"),),
            "msedge.exe": (Path("Microsoft/Edge/Application/msedge.exe"),),
            "Code.exe": (Path("Programs/Microsoft VS Code/Code.exe"), Path("Microsoft VS Code/Code.exe")),
            "notepad.exe": (Path("System32/notepad.exe"), Path("notepad.exe")),
            "calc.exe": (Path("System32/calc.exe"), Path("calc.exe")),
            "explorer.exe": (Path("explorer.exe"),),
            # Per-user installers, which register no App Path.
            "Telegram.exe": (Path("Telegram Desktop/Telegram.exe"),),
            "Signal.exe": (Path("Programs/signal-desktop/Signal.exe"),),
            "Spotify.exe": (Path("Spotify/Spotify.exe"),),
            "Zoom.exe": (Path("Zoom/bin/Zoom.exe"),),
        }
        for root in roots:
            for relative in relative_candidates.get(executable, ()):
                candidate = root / relative
                if candidate.is_file():
                    return candidate.resolve()
        return None


class PortableApplicationController:
    """Small non-Windows adapter; launching works without pretending UIA exists."""

    def windows(self, _application: str) -> list[dict[str, Any]]:
        return []

    def wait_for_window(self, _application: str, timeout: float = 0.0) -> None:
        return None

    def focus(self, application: str, _handle: int | None = None) -> dict[str, Any]:
        return {"application": application, "focused": False,
                "reason": "Native window focusing is not installed on this platform."}

    def state(self, application: str, _handle: int | None = None) -> dict[str, Any]:
        return {"application": application, "running": False, "window_count": 0,
                "windows": [], "target_window_exists": False,
                "native_window_observation": False}

    def write_notepad(self, _text: str, _append_line: bool = False,
                      _handle: int | None = None) -> dict[str, Any]:
        raise RuntimeError("Native editor automation is unavailable on this platform; use project files.")

    def close(self, application: str, _handle: int | None = None,
              _save_choice: str = "ask") -> dict[str, Any]:
        return {"application": application, "closed": False,
                "reason": "Native window closing is unavailable on this platform."}

def register_application_tools(registry: ToolRegistry) -> ApplicationTools:
    applications = ApplicationTools(registry.permissions)
    obj = lambda props, required=None: {"type": "object", "properties": props, "required": required or [], "additionalProperties": False}
    registry.register("discover_applications", "Discover approved desktop applications without launching them.", obj({}), "low", "application_discover", applications.discover)
    registry.register("launch_application", "Launch an approved desktop application by common name. Does not accept arbitrary executables.", obj({"application": {"type": "string", "enum": sorted(ApplicationTools.ALIASES)}, "path": {"type": "string"}}, ["application"]), "medium", "application_launch", applications.launch)
    registry.register("open_project_folder", "Open the current project in the system file manager.", obj({}), "low", "application_launch", applications.open_project_folder)
    registry.register("application_state", "Observe visible windows and application-specific state without changing it.", obj({"application": {"type": "string", "enum": sorted(ApplicationTools.ALIASES)}, "handle": {"type": ["integer", "null"]}}, ["application"]), "low", "computer_observe", applications.state)
    registry.register("notepad_edit", "Focus the intended Notepad editor, insert requested text, then read the editor back and report whether the text is present.", obj({"text": {"type": "string", "minLength": 1}, "append_line": {"type": "boolean"}, "handle": {"type": ["integer", "null"]}}, ["text"]), "high", "computer_input", applications.notepad_edit)
    registry.register("close_application", "Close one intended application window and verify that exact window disappeared. Unsaved changes return an explicit choice instead of guessing.", obj({"application": {"type": "string", "enum": sorted(ApplicationTools.ALIASES)}, "handle": {"type": ["integer", "null"]}, "save_choice": {"type": "string", "enum": ["ask", "save", "discard", "cancel"]}}, ["application"]), "high", "computer_input", applications.close)
    return applications
