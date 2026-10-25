from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import cv2


class OpenCVCameraCapture:
    """One explicitly managed DirectShow device. `close` always releases the handle."""

    def __init__(self) -> None:
        self.device_index: int | None = None
        self.capture: cv2.VideoCapture | None = None
        self._lock = threading.RLock()

    @property
    def active(self) -> bool:
        return bool(self.capture is not None and self.capture.isOpened())

    def open(self, device_index: int = 0, width: int = 1280, height: int = 720) -> dict[str, Any]:
        with self._lock:
            if self.active and self.device_index == device_index:
                return self.properties() | {"existing": True}
            self.close()
            camera = cv2.VideoCapture(device_index, cv2.CAP_DSHOW)
            if not camera.isOpened():
                camera.release(); camera = cv2.VideoCapture(device_index)
            if not camera.isOpened():
                camera.release(); raise RuntimeError(f"Windows camera index {device_index} could not be opened.")
            camera.set(cv2.CAP_PROP_FRAME_WIDTH, width); camera.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
            self.capture = camera; self.device_index = device_index
            # Let automatic exposure settle without retaining frames.
            deadline = time.monotonic() + 1.5
            while time.monotonic() < deadline:
                camera.grab()
            return self.properties() | {"existing": False}

    def read(self) -> Any:
        with self._lock:
            if not self.active:
                raise RuntimeError("Camera is not active.")
            assert self.capture is not None
            ok, frame = self.capture.read()
            if not ok or frame is None:
                raise RuntimeError("Camera did not return a frame.")
            return frame

    def save(self, path: Path) -> dict[str, Any]:
        frame = self.read(); path.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(path), frame):
            raise OSError(f"OpenCV could not write {path}.")
        height, width = frame.shape[:2]
        return {"path": str(path), "width": int(width), "height": int(height), "bytes": path.stat().st_size}

    def properties(self) -> dict[str, Any]:
        if not self.active:
            return {"active": False, "device_index": self.device_index}
        assert self.capture is not None
        return {
            "active": True, "device_index": self.device_index,
            "width": int(self.capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
            "height": int(self.capture.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            "fps": round(float(self.capture.get(cv2.CAP_PROP_FPS)), 2),
            "backend": self.capture.getBackendName(),
        }

    def close(self) -> None:
        with self._lock:
            camera, self.capture = self.capture, None
            if camera is not None:
                camera.release()
            self.device_index = None
