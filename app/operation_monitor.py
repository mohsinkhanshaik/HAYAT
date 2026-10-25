"""Bounded, memory-only desktop previews driven by real tool lifecycle events."""
from __future__ import annotations

from collections import deque
from datetime import UTC, datetime
import re
import math
import threading
import time


def desktop_operation(tool: str) -> bool:
    return tool.startswith(('computer_', 'browser_', 'ui_', 'notepad_', 'window_')) or tool in {
        'launch_application', 'close_application', 'move_active_window_to_display', 'display_screenshot',
        'describe_screen', 'screen_read', 'screen_read_start',
    }


def capture_desktop():
    from PIL import ImageGrab
    return ImageGrab.grab()


class OperationMonitor:
    """One worker and one frame. Observation never sends mouse/keyboard input."""
    def __init__(self, *, capture=capture_desktop, allowed=lambda: True, version=lambda: 0, interval=1.0, active_ttl=300):
        self.capture, self.allowed, self.version = capture, allowed, version
        if not math.isfinite(float(interval)) or not math.isfinite(float(active_ttl)) or active_ttl <= 0:
            raise ValueError('Preview intervals must be finite and operation lifetime positive.')
        self.interval = max(.1, float(interval))
        self.active_ttl = float(active_ttl)
        self._condition = threading.Condition(threading.RLock())
        self._visible = self._paused = self._closed = self._pending = False
        self._generation = 0
        self._active = {}
        self._active_since = {}
        self._epoch = self.version()
        self._dropped_starts = self._expired = 0
        self._events = deque(maxlen=100)
        self._frame = None
        self._frame_id = 0
        self._captured_at = self._error = ''
        self._next_capture = 0.0
        self._worker = threading.Thread(target=self._run, daemon=True, name='hayat-operation-preview')
        self._worker.start()

    def _permitted(self):
        try:
            return bool(self.allowed())
        except Exception:
            return False

    def _clear(self):
        self._generation += 1
        self._frame = None
        self._frame_id += 1
        self._captured_at = ''
        self._pending = False

    def _refresh_guard(self):
        epoch = self.version()
        if epoch != self._epoch:
            self._epoch = epoch
            self._clear()
            self._active.clear()
            self._active_since.clear()
        for key, stamp in list(self._active_since.items()):
            if time.monotonic() - stamp > self.active_ttl:
                self._expired += self._active.pop(key, 0)
                self._active_since.pop(key, None)

    def receive(self, event):
        payload = event.get('payload') or {}
        tool, kind = str(payload.get('tool') or ''), str(event.get('event_type') or '')
        if not desktop_operation(tool) or kind not in {'TOOL_STARTED', 'TOOL_COMPLETED', 'TOOL_FAILED'}:
            return False
        key = (tool, event.get('task_id'), event.get('turn_id'), payload.get('call_id'))
        with self._condition:
            if self._closed:
                return False
            self._refresh_guard()
            if kind == 'TOOL_STARTED':
                if sum(self._active.values()) < 64:
                    self._active[key] = self._active.get(key, 0) + 1
                    self._active_since[key] = time.monotonic()
                    self._pending = True
                else:
                    self._dropped_starts += 1
            else:
                started = self._active.get(key, 0)
                if started > 1:
                    self._active[key] -= 1
                else:
                    self._active.pop(key, None)
                    self._active_since.pop(key, None)
                self._pending |= bool(started)
            # Typed text, URLs, contact details, tool arguments and outputs are not copied.
            phase = {'TOOL_STARTED': 'Running', 'TOOL_COMPLETED': 'Returned', 'TOOL_FAILED': 'Failed'}[kind]
            self._events.append({'sequence': event.get('sequence'), 'at': str(event.get('timestamp') or '')[:40],
                                 'tool': tool[:100], 'phase': phase})
            self._condition.notify_all()
        return True

    def visible(self, value):
        with self._condition:
            self._visible = bool(value)
            if not value:
                self._clear()
            self._condition.notify_all()

    def pause(self, value=True):
        with self._condition:
            self._paused = bool(value)
            if value:
                self._clear()
            self._condition.notify_all()

    def observe_once(self):
        with self._condition:
            if self._closed or self._paused or not self._visible or not self._permitted():
                return False
            self._pending = True
            self._condition.notify_all()
            return True

    def invalidate(self):
        """Synchronously discard observations across a privacy transition."""
        with self._condition:
            self._clear()
            self._active.clear()
            self._active_since.clear()
            self._condition.notify_all()

    def snapshot(self, last_frame_id=None):
        with self._condition:
            self._refresh_guard()
            blocked = not self._permitted()
            if blocked:
                if self._frame is not None or self._active or self._pending:
                    self._clear()
                self._active.clear()
                self._active_since.clear()
            return {'events': list(self._events), 'active': sum(self._active.values()),
                    'dropped_starts': self._dropped_starts, 'expired': self._expired,
                    'paused': self._paused, 'blocked': blocked, 'visible': self._visible,
                    'closed': self._closed, 'frame_id': self._frame_id,
                    'frame': self._frame.copy() if self._frame is not None and last_frame_id != self._frame_id else None,
                    'has_frame': self._frame is not None, 'captured_at': self._captured_at, 'error': self._error}

    def _run(self):
        while True:
            with self._condition:
                if self._closed:
                    return
                self._refresh_guard()
                permitted = self._permitted()
                if not permitted:
                    if self._frame is not None or self._active or self._pending:
                        self._clear()
                    self._active.clear()
                    self._active_since.clear()
                ready = self._visible and not self._paused and permitted and (self._pending or self._active)
                delay = self._next_capture - time.monotonic()
                if not ready or delay > 0:
                    self._condition.wait(timeout=max(.05, min(self.interval, delay)) if ready else self.interval)
                    continue
                generation = self._generation
                epoch = self._epoch
                self._pending = False
                self._next_capture = time.monotonic() + self.interval
            try:
                frame = self.capture()
                if frame.width < 2 or frame.height < 2 or frame.width * frame.height > 40_000_000:
                    raise ValueError('Unsupported desktop image dimensions')
                frame = frame.convert('RGB')
                frame.thumbnail((1280, 800))
                error = ''
            except Exception as exc:
                frame = None
                error = type(exc).__name__  # Avoid embedding native exceptions containing private titles/paths.
            with self._condition:
                self._refresh_guard()
                if (generation != self._generation or epoch != self._epoch or self._closed or self._paused
                        or not self._visible or not self._permitted()):
                    continue
                self._frame = frame
                self._frame_id += 1
                self._captured_at = datetime.now(UTC).isoformat(timespec='seconds') if frame is not None else ''
                self._error = error

    def close(self):
        with self._condition:
            self._closed = True
            self._visible = False
            self._active.clear()
            self._active_since.clear()
            self._clear()
            self._condition.notify_all()
        self._worker.join(timeout=.5)


def action_label(event):
    tool = re.sub(r'[^a-zA-Z0-9_]', '', event['tool']).replace('_', ' ')
    return f"{event['phase']}: {tool}"
