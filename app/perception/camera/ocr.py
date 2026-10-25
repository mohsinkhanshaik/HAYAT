from __future__ import annotations

import asyncio
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


class WindowsMediaOcr:
    """Windows.Media.Ocr via the official Microsoft winsdk WinRT projection.

    This ships inside Windows 10/11 itself -- no download, no network call,
    no subprocess per read. It is the preferred backend: $0, local, and
    already present on essentially every target machine, unlike Tesseract
    which requires a separate install this project cannot silently perform.
    """

    def __init__(self) -> None:
        self._engine = None
        self._error: str | None = None
        if sys.platform != "win32":
            self._error = "Windows.Media.Ocr requires Windows."
            return
        try:
            from winsdk.windows.media.ocr import OcrEngine

            self._engine = OcrEngine.try_create_from_user_profile_languages()
            if self._engine is None:
                self._error = "No OCR-capable language is installed for the current Windows user profile."
        except Exception as exc:
            self._error = f"{type(exc).__name__}: {exc}"

    @property
    def available(self) -> bool:
        return self._engine is not None

    def status(self) -> dict[str, Any]:
        language = None
        if self._engine is not None:
            language = self._engine.recognizer_language.display_name
        return {"provider": "windows_media_ocr", "available": self.available, "language": language, "cloud": False, "reason": self._error}

    def read(self, path: Path) -> dict[str, Any]:
        if self._engine is None:
            return {"available": False, "text": "", "provider": "windows_media_ocr", "reason": self._error or "OCR engine unavailable."}
        started = time.perf_counter()
        lines, text = self._run(self._read_async(path))
        return {
            "available": True, "provider": "windows_media_ocr", "cloud": False,
            "text": text, "lines": lines, "line_count": len(lines),
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        }

    @staticmethod
    def _run(coroutine: Any) -> Any:
        """Await a coroutine from a caller that may or may not have a loop.

        `asyncio.run` refuses outright when the calling thread already has a
        running event loop -- and refuses *after* creating the coroutine, so
        the failure comes with a "never awaited" warning attached to it. Every
        caller today is a plain thread, which is why this has not bitten yet;
        it is the same shape of assumption as the browser one, where code
        assumed it owned the thread it was standing on and was right until it
        was not.

        A loop already running means someone else owns this thread, so the
        work goes to one that is free.
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coroutine)  # the ordinary case: no loop here

        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(1, thread_name_prefix="ocr-await") as pool:
            return pool.submit(asyncio.run, coroutine).result()

    async def _read_async(self, path: Path) -> tuple[list[str], str]:
        from winsdk.windows.graphics.imaging import BitmapDecoder
        from winsdk.windows.storage import StorageFile

        file = await StorageFile.get_file_from_path_async(str(path.resolve()))
        stream = await file.open_async(0)  # FileAccessMode.READ
        decoder = await BitmapDecoder.create_async(stream)
        bitmap = await decoder.get_software_bitmap_async()
        result = await self._engine.recognize_async(bitmap)
        lines = [line.text for line in result.lines]
        return lines, result.text


class TesseractOcr:
    """Optional local Tesseract adapter, used only when installed. It never calls a network service."""

    #: Where Windows installers actually put Tesseract. PATH alone was the
    #: only thing checked, and the official winget package installs per-user
    #: into LOCALAPPDATA without touching PATH -- so it was installed, working,
    #: and reported unavailable.
    _KNOWN_LOCATIONS = (
        r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe",
        r"%PROGRAMFILES%\Tesseract-OCR\tesseract.exe",
        r"%PROGRAMFILES(X86)%\Tesseract-OCR\tesseract.exe",
        r"%PROGRAMDATA%\chocolatey\bin\tesseract.exe",
    )

    def __init__(self) -> None:
        self.executable = self._in_folder() or shutil.which("tesseract") or self._find_installed()

    @staticmethod
    def _in_folder() -> str | None:
        """HAYAT's own copy in runtime/, placed there by scripts/make_portable.py."""
        from app import portable

        own = portable.tesseract_executable()
        return str(own) if own.is_file() else None

    @classmethod
    def _find_installed(cls) -> str | None:
        """Look where it lands when nobody adds it to PATH."""
        import os

        for template in cls._KNOWN_LOCATIONS:
            candidate = Path(os.path.expandvars(template))
            if "%" not in str(candidate) and candidate.is_file():
                return str(candidate)
        return None

    @property
    def available(self) -> bool:
        return bool(self.executable)

    def status(self) -> dict[str, Any]:
        return {"provider": "tesseract", "available": self.available, "executable": self.executable, "cloud": False}

    def read(self, path: Path) -> dict[str, Any]:
        if not self.executable:
            return {"available": False, "text": "", "provider": "tesseract", "reason": "No local Tesseract executable is installed."}
        started = time.perf_counter()
        completed = subprocess.run([self.executable, str(path), "stdout", "--psm", "6"], capture_output=True, text=True, timeout=30, encoding="utf-8", errors="replace")
        if completed.returncode:
            raise RuntimeError(completed.stderr.strip() or "Tesseract failed.")
        text = completed.stdout.strip()
        return {"available": True, "text": text, "lines": text.splitlines(), "provider": "tesseract", "cloud": False, "latency_ms": round((time.perf_counter() - started) * 1000, 2)}


class LocalOCR:
    """Local-only OCR: Windows.Media.Ocr first, Tesseract as a fallback when installed.

    Neither backend calls a network service. `status()` reports which backend
    is actually serving reads, not merely which one was tried first.
    """

    def __init__(self) -> None:
        self.windows = WindowsMediaOcr()
        self.tesseract = TesseractOcr()

    def _backend(self):
        return self.windows if self.windows.available else self.tesseract

    def status(self) -> dict[str, Any]:
        active = self._backend()
        return active.status() | {"active_backend": active.status()["provider"] if active.available else None, "backends": {"windows_media_ocr": self.windows.status(), "tesseract": self.tesseract.status()}}

    def read(self, path: Path) -> dict[str, Any]:
        backend = self._backend()
        original = backend.read(path)
        if not backend.available:
            return original

        # OCR is unusually sensitive to lighting and scale. Run two local
        # preprocessing variants and keep/merge the strongest reading. Raw
        # camera data never leaves the machine and all derivative images are
        # deleted before this method returns.
        variants: list[tuple[str, Path]] = []
        try:
            import cv2

            image = cv2.imread(str(path))
            if image is not None:
                gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
                scale = max(1.0, min(2.5, 1800 / max(1, gray.shape[1])))
                if scale > 1.05:
                    gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
                contrast = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
                threshold = cv2.adaptiveThreshold(
                    contrast, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                    cv2.THRESH_BINARY, 31, 11,
                )
                for label, data in (("contrast", contrast), ("threshold", threshold)):
                    target = path.with_name(f"{path.stem}.ocr-{label}.png")
                    if cv2.imwrite(str(target), data):
                        variants.append((label, target))
        except Exception:
            variants = []

        readings: list[tuple[str, dict[str, Any]]] = [("original", original)]
        try:
            for label, candidate in variants:
                readings.append((label, backend.read(candidate)))
        finally:
            for _, candidate in variants:
                candidate.unlink(missing_ok=True)

        def score(item: tuple[str, dict[str, Any]]) -> tuple[int, int]:
            text = str(item[1].get("text", ""))
            return (len(re.findall(r"[A-Za-z0-9]", text)), len(text))

        best_label, best = max(readings, key=score)
        lines: list[str] = []
        seen: set[str] = set()
        for _, reading in sorted(readings, key=score, reverse=True):
            for line in reading.get("lines") or str(reading.get("text", "")).splitlines():
                clean = " ".join(str(line).split()).strip()
                key = clean.casefold()
                if clean and key not in seen:
                    seen.add(key); lines.append(clean)
        return best | {
            "text": "\n".join(lines), "lines": lines, "line_count": len(lines),
            "best_variant": best_label, "variants_analyzed": len(readings),
        }
