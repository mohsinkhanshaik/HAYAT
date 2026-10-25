from __future__ import annotations

import math
import re
import hashlib
import threading
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from app.safety.permissions import PermissionManager
from app.tools.registry import ToolRegistry


COMPUTER_TOOL_NAMES = ('computer_status', 'computer_screenshot', 'computer_mouse_position',
                       'computer_move_mouse', 'computer_click', 'computer_drag', 'computer_observe',
                       'computer_click_observed', 'computer_press_key', 'computer_hotkey',
                       'computer_scroll', 'computer_type', 'ui_read', 'ui_elements', 'ui_click', 'ui_type')


def direct_computer_plan(goal: str, names):
    """Fast, exact desktop requests. Application-semantic tasks use the normal planner."""
    value = goal.strip().rstrip('.!?').lower()
    value = re.sub(r'^(?:please |can you |could you )', '', value)
    if value in {'computer status', 'desktop status', 'check computer control'}:
        selected = [('Check desktop input readiness without sending input.', 'computer_status', {})]
    elif value in {'where is the cursor', 'where is my cursor', 'where is the mouse', 'cursor position'}:
        selected = [('Read the pointer position.', 'computer_mouse_position', {})]
    elif value in {'show my screen', 'show the screen', 'take a screenshot', 'capture my screen'}:
        selected = [('Capture the current desktop for observation.', 'computer_screenshot', {})]
    elif value in {'observe my desktop', 'observe the desktop', 'observe my screen'}:
        selected = [('Capture fresh desktop evidence with a short-lived observation token.', 'computer_observe', {})]
    else:
        move = re.fullmatch(r'move (?:the )?(?:cursor|mouse|pointer) to\s*\(?\s*(\d+)\s*,\s*(\d+)\s*\)?', value)
        click = re.fullmatch(r'(double click|right click|click) (?:at|on)\s*\(?\s*(\d+)\s*,\s*(\d+)\s*\)?', value)
        drag = re.fullmatch(r'drag (?:the )?(?:cursor |mouse )?from\s*\(?\s*(\d+)\s*,\s*(\d+)\s*\)?\s+to\s*\(?\s*(\d+)\s*,\s*(\d+)\s*\)?', value)
        scroll = re.fullmatch(r'scroll (up|down)(?: (\d+)(?: clicks?)?)?', value)
        key = re.fullmatch(r'press (?:the )?([a-z0-9]+)(?: key)?', value)
        shortcut = re.fullmatch(r'(?:press|hotkey) ([a-z0-9]+(?:\+[a-z0-9]+){1,5})', value)
        if move:
            tool, arguments = 'computer_move_mouse', {'x': int(move[1]), 'y': int(move[2])}
        elif click:
            tool, arguments = 'computer_click', {'x': int(click[2]), 'y': int(click[3]),
                                                'button': 'right' if click[1] == 'right click' else 'left',
                                                'clicks': 2 if click[1] == 'double click' else 1}
        elif drag:
            tool, arguments = 'computer_drag', dict(zip(('start_x', 'start_y', 'end_x', 'end_y'), map(int, drag.groups())))
        elif scroll:
            tool, arguments = 'computer_scroll', {'clicks': int(scroll[2] or 3) * (1 if scroll[1] == 'up' else -1)}
        elif shortcut:
            tool, arguments = 'computer_hotkey', {'keys': shortcut[1].split('+')}
        elif key:
            tool, arguments = 'computer_press_key', {'key': key[1]}
        else:
            # Controls by name ("click Save in notepad", "type 'x' into File name"), from the original wording.
            from app.tools.ui_automation import ui_plan
            steps = ui_plan(goal)
            if not steps or any(tool not in names for _, tool, _ in steps):
                return None
            return [{'description': description, 'tool': tool, 'arguments': arguments,
                     'expected': {'type': 'tool_success'}, 'max_retries': 0} for description, tool, arguments in steps]
        selected = [('Observe the screen before the requested pointer action.', 'computer_screenshot', {}),
                    ('Send the exact requested desktop input; observe the application to confirm its effect.', tool, arguments),
                    ('Capture the screen after input so the result can be reviewed.', 'computer_screenshot', {})]
    if any(tool not in names for _, tool, _ in selected):
        return None
    return [{'description': description, 'tool': tool, 'arguments': arguments,
             'expected': {'type': 'tool_success'}, 'max_retries': 0} for description, tool, arguments in selected]


class ComputerTools:
    def __init__(self, permissions: PermissionManager) -> None:
        self.permissions = permissions
        self._pyautogui: Any = None
        self._error = ""
        self._observations: dict[str, dict[str, Any]] = {}
        self._observation_lock = threading.RLock()
        try:
            # Lazy import matters on a headless Linux host: importing
            # PyAutoGUI itself opens the display and can raise before the CLI
            # or web server has even started.
            import pyautogui

            pyautogui.FAILSAFE = True
            self._pyautogui = pyautogui
        except Exception as exc:
            self._error = f"{type(exc).__name__}: {exc}"

    def _backend(self) -> Any:
        if self._pyautogui is None:
            raise RuntimeError(
                "Desktop input is unavailable on this host"
                + (f": {self._error}" if self._error else ".")
            )
        return self._pyautogui

    def status(self) -> dict[str, Any]:
        """Observe desktop readiness without moving, clicking, typing or saving a screenshot."""
        if self._pyautogui is None:
            return {"available": False, "reason": self._error or "Desktop input backend is unavailable", "input_sent": False}
        try:
            backend = self._backend()
            size, pointer = backend.size(), backend.position()
            return {"available": True, "screen": {"width": size.width, "height": size.height},
                    "pointer": {"x": pointer.x, "y": pointer.y},
                    "failsafe": bool(backend.FAILSAFE), "input_sent": False,
                    "scope": "Primary display coordinates. Observe the target window before acting.",
                    "verification": "The desktop backend responded. Target application control has not been tested."}
        except Exception as exc:
            return {"available": False, "reason": f"{type(exc).__name__}: {exc}", "input_sent": False}

    def screenshot(self, path: str = "") -> dict[str, Any]:
        path = path or f'data/artifacts/screen-{uuid.uuid4().hex}.png'
        output = self.permissions.validate_project_path(path, write=True)
        output.parent.mkdir(parents=True, exist_ok=True)
        image = self._backend().screenshot()
        image.save(output)
        return {"path": str(output), "width": image.width, "height": image.height, "exists": output.exists(),
                "captured_at": datetime.now(UTC).isoformat(), "input_sent": False}

    @staticmethod
    def _fingerprint(image):
        return hashlib.sha256(image.convert('RGB').tobytes()).hexdigest()

    @staticmethod
    def _active_window():
        from app.perception.displays import DisplayManager
        return DisplayManager().active_window()

    def observe(self) -> dict[str, Any]:
        """Keep distinct evidence for every observation; tokens expire after 30 seconds."""
        with self._observation_lock:
            backend = self._backend()
            window = self._active_window()
            image = backend.screenshot()
            if self._active_window() != window:
                raise ValueError('The foreground window changed during observation; observe again.')
            size = backend.size()
            if (image.width, image.height) != (size.width, size.height):
                raise ValueError('Screenshot and input coordinates do not match; use display-specific tools.')
            identifier = uuid.uuid4().hex
            output = self.permissions.validate_project_path(f'data/artifacts/desktop-{identifier}.png', write=True)
            output.parent.mkdir(parents=True, exist_ok=True)
            image.save(output)
            captured = time.monotonic()
            record = {'captured': captured, 'fingerprint': self._fingerprint(image),
                      'width': image.width, 'height': image.height, 'active_window': window}
            self._observations = {token: row for token, row in self._observations.items()
                                  if captured - row['captured'] <= 30}
            self._observations = dict(list(self._observations.items())[-31:])
            self._observations[identifier] = record
            return {'observation_id': identifier, 'path': str(output), 'exists': output.is_file(),
                    'captured_at': datetime.now(UTC).isoformat(), 'expires_in_seconds': 30,
                    'width': image.width, 'height': image.height, 'active_window': window,
                    'screen_sha256': record['fingerprint'], 'input_sent': False,
                    'next_step': 'Inspect the screenshot before computer_click_observed. Changed pixels, geometry or window require a new observation.'}

    def click_observed(self, observation_id: str, x: int, y: int,
                       button: str = 'left', clicks: int = 1) -> dict[str, Any]:
        """An optional conservative guard; animated desktops may require re-observation."""
        with self._observation_lock:
            record = self._observations.pop(observation_id, None)
            if record is None or time.monotonic() - record['captured'] > 30:
                raise ValueError('Observation is missing, expired or already used; observe again.')
            backend = self._backend()
            image = backend.screenshot()
            size = backend.size()
            if ((image.width, image.height) != (record['width'], record['height'])
                    or (size.width, size.height) != (record['width'], record['height'])
                    or self._active_window() != record['active_window']
                    or self._fingerprint(image) != record['fingerprint']):
                raise ValueError('The desktop changed since observation; no input sent. Observe again.')
            result = self.click(x, y, button, clicks)
            return {**result, 'observation_id': observation_id, 'observation_checked': True,
                    'verification': 'Fresh screen and foreground checked immediately before input; observe the application to verify the result.'}

    def move_mouse(self, x: int, y: int, duration: float = 0.2) -> dict[str, Any]:
        backend = self._backend()
        self._validate_point(x, y)
        if not math.isfinite(duration) or not 0 <= duration <= 5:
            raise ValueError('Mouse movement duration must be between 0 and 5 seconds')
        backend.moveTo(x, y, duration=duration)
        position = backend.position()
        self._verify_point(x, y, position)
        return {"x": position.x, "y": position.y, "pointer_verified": True}

    def _validate_point(self, x, y):
        size = self._backend().size()
        if type(x) is not int or type(y) is not int or not 0 <= x < size.width or not 0 <= y < size.height:
            raise ValueError('Coordinates must be inside the observed primary display; observe the target before acting')

    @staticmethod
    def _verify_point(x, y, position):
        if (position.x, position.y) != (x, y):
            raise ValueError(f'Cursor moved to ({position.x}, {position.y}) instead of ({x}, {y}); observe the screen again')

    def click(self, x: int, y: int, button: str = "left", clicks: int = 1) -> dict[str, Any]:
        backend = self._backend()
        self._validate_point(x, y)
        if button not in {'left', 'right', 'middle'} or type(clicks) is not int or not 1 <= clicks <= 2:
            raise ValueError('Choose a valid button and one or two clicks')
        backend.moveTo(x, y, duration=.15)
        self._verify_point(x, y, backend.position())
        backend.click(button=button, clicks=clicks, interval=0.12)
        return {"x": x, "y": y, "button": button, "clicks": clicks, "pointer_verified": True,
                "verification": 'Input sent at the observed pointer. Observe the application to confirm its effect.'}

    def type_text(self, text: str, interval: float = 0.02) -> dict[str, Any]:
        if not math.isfinite(interval) or not 0 <= interval <= .2 or len(text) > 32000:
            raise ValueError('Typing supports up to 32000 characters and intervals between 0 and 0.2 seconds')
        # PyAutoGUI silently drops non-ASCII characters. Refuse rather than
        # claim Arabic or other Unicode text was entered; named editors and
        # clipboard tools support Unicode without guessing the focused target.
        if not text.isascii():
            raise ValueError('Use notepad_edit or clipboard_write_text for Unicode text; desktop key typing supports ASCII only')
        self._backend().write(text, interval=interval)
        return {"typed_characters": len(text), "verification": 'Keys sent; read back the target to confirm the text.'}

    def press_key(self, key: str) -> dict[str, Any]:
        backend = self._backend()
        self._validate_keys([key], backend)
        backend.press(key)
        return {"key": key, "verification": 'Key sent; observe the application to confirm its effect.'}

    def hotkey(self, keys: list[str]) -> dict[str, Any]:
        backend = self._backend()
        self._validate_keys(keys, backend)
        backend.hotkey(*keys)
        return {"keys": keys, "verification": 'Shortcut sent; observe the application to confirm its effect.'}

    @staticmethod
    def _validate_keys(keys, backend):
        if (not isinstance(keys, list) or not 1 <= len(keys) <= 6
                or any(not isinstance(key, str) or key not in backend.KEYBOARD_KEYS for key in keys)):
            raise ValueError('Use 1–6 supported desktop key names; unknown keys are not sent.')

    def scroll(self, clicks: int) -> dict[str, Any]:
        if type(clicks) is not int or not -100 <= clicks <= 100 or clicks == 0:
            raise ValueError('Scroll clicks must be a nonzero integer between -100 and 100.')
        self._backend().scroll(clicks)
        return {"clicks": clicks, "verification": 'Scroll sent; observe the application to confirm its effect.'}

    def mouse_position(self) -> dict[str, Any]:
        """Where the pointer actually is.

        Observation, not action. A plan that moves the mouse and then clicks had
        no way to confirm the move landed, so a move silently clamped by a
        display boundary was followed by a click somewhere nobody intended.
        """
        backend = self._backend()
        position = backend.position()
        size = backend.size()
        return {"x": position.x, "y": position.y,
                "screen_width": size.width, "screen_height": size.height}

    def drag(self, start_x: int, start_y: int, end_x: int, end_y: int,
             button: str = "left", duration: float = 0.4) -> dict[str, Any]:
        """Press at one point, move, release at another.

        Nothing else could do this, and a surprising amount of a desktop needs
        it: sliders, text selection, resizing a window, moving a file into a
        folder, drawing a region. Every one of those was out of reach with only
        click and move, because a click that releases where it pressed is not a
        drag however many of them you chain together.

        The move is deliberately not instantaneous. An application that tracks
        the pointer sees a zero-duration drag as a press and release in the same
        place and does nothing with it.
        """
        backend = self._backend()
        self._validate_point(start_x, start_y)
        self._validate_point(end_x, end_y)
        if button not in {'left', 'right', 'middle'} or not math.isfinite(duration):
            raise ValueError('Choose a valid drag button and finite duration')
        backend.moveTo(start_x, start_y, duration=min(max(duration, 0.0), 5.0) / 2)
        self._verify_point(start_x, start_y, backend.position())
        backend.mouseDown(button=button)
        try:
            backend.moveTo(end_x, end_y, duration=min(max(duration, 0.05), 5.0))
        finally:
            # Releasing in a finally block matters more here than it looks. A
            # failed move used to leave the button held down, and every
            # subsequent observation of the desktop was taken mid-drag.
            backend.mouseUp(button=button)
        position = backend.position()
        self._verify_point(end_x, end_y, position)
        return {"start": {"x": start_x, "y": start_y}, "end": {"x": end_x, "y": end_y},
                "button": button, "released_at": {"x": position.x, "y": position.y}}


def register_computer_tools(registry: ToolRegistry) -> ComputerTools:
    computer = ComputerTools(registry.permissions)
    obj = lambda props, required=None: {"type": "object", "properties": props, "required": required or [], "additionalProperties": False}
    registry.register("computer_status", "Check desktop backend, primary display size, pointer and failsafe without sending input.", obj({}), "low", "computer_observe", computer.status)
    registry.register("computer_screenshot", "Capture the current desktop to a project artifact for observation.", obj({"path": {"type": "string"}}), "low", "computer_observe", computer.screenshot)
    registry.register("computer_observe", "Capture unique primary-screen evidence, geometry and foreground window with a single-use 30-second token for guarded input.", obj({}), "low", "computer_observe", computer.observe)
    registry.register("computer_click_observed", "Click only if a computer_observe token is fresh and pixels, geometry and foreground window are unchanged; then observe to verify the effect.", obj({"observation_id": {"type": "string"}, "x": {"type": "integer"}, "y": {"type": "integer"}, "button": {"enum": ["left", "right", "middle"]}, "clicks": {"type": "integer", "minimum": 1, "maximum": 2}}, ["observation_id", "x", "y"]), "high", "computer_input", computer.click_observed)
    registry.register("computer_move_mouse", "Move the mouse after observing the screen.", obj({"x": {"type": "integer"}, "y": {"type": "integer"}, "duration": {"type": "number"}}, ["x", "y"]), "medium", "computer_input", computer.move_mouse)
    registry.register("computer_click", "Click at screen coordinates after observation; approval required.", obj({"x": {"type": "integer"}, "y": {"type": "integer"}, "button": {"enum": ["left", "right", "middle"]}, "clicks": {"type": "integer", "minimum": 1, "maximum": 2}}, ["x", "y"]), "high", "computer_input", computer.click)
    registry.register("computer_type", "Type text into the focused desktop control; approval required.", obj({"text": {"type": "string"}, "interval": {"type": "number"}}, ["text"]), "high", "computer_input", computer.type_text)
    registry.register("computer_press_key", "Press one keyboard key; approval required.", obj({"key": {"type": "string"}}, ["key"]), "high", "computer_input", computer.press_key)
    registry.register("computer_hotkey", "Press a validated keyboard shortcut; approval required.", obj({"keys": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 6}}, ["keys"]), "high", "computer_input", computer.hotkey)
    registry.register("computer_scroll", "Scroll the active desktop UI by a bounded number of clicks; approval required.", obj({"clicks": {"type": "integer", "minimum": -100, "maximum": 100}}, ["clicks"]), "medium", "computer_input", computer.scroll)
    registry.register("computer_mouse_position", "Read the current pointer position and screen size to confirm a move landed.", obj({}), "low", "computer_observe", computer.mouse_position)
    registry.register("computer_drag", "Press at one point, move, and release at another for sliders, selections, resizing, and moving items; approval required.", obj({"start_x": {"type": "integer"}, "start_y": {"type": "integer"}, "end_x": {"type": "integer"}, "end_y": {"type": "integer"}, "button": {"enum": ["left", "right", "middle"]}, "duration": {"type": "number", "minimum": 0, "maximum": 5}}, ["start_x", "start_y", "end_x", "end_y"]), "high", "computer_input", computer.drag)
    return computer
