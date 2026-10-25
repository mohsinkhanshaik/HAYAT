"""Desktop apps by their controls, not by pixels: Windows UI Automation (the layer screen readers use).

Clicking at screen coordinates breaks when a window moves, the display scale changes or a theme
differs. UI Automation names every button, menu item, box and tab the way the app itself does, so
HAYAT can read a window, list what can be pressed, press "Save" or type into "File name" wherever
they are on screen.

* ``ui_read``: the window's title and visible text (read-only).
* ``ui_elements``: its interactive controls: name, kind, position, enabled (read-only).
* ``ui_click``: press a control by its name; several matches are offered, never guessed.
* ``ui_type``: type into a named text box and read it back.

Pressing and typing go through the same "computer_input" permission and approval as the existing
coordinate click and typing tools; reading is "computer_observe", like a screenshot. With no
window named, the target is the topmost visible window that is not HAYAT itself (HAYAT is usually
in front while the owner gives the instruction).
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Callable

from app.logging import log_swallowed

_log = logging.getLogger(__name__)

INTERACTIVE = ("Button", "MenuItem", "Edit", "CheckBox", "RadioButton", "TabItem", "ListItem", "Hyperlink", "ComboBox", "TreeItem",
               "SplitButton", "MenuBarItem")
TEXTUAL = ("Text", "Edit", "Document", "Button", "MenuItem", "ListItem", "TabItem", "Hyperlink", "CheckBox", "RadioButton", "TreeItem",
           "DataItem", "HeaderItem", "Group", "Pane")
DEPTH = 14


def _info(element: Any) -> dict[str, Any]:
    info = element.element_info
    try:
        rect = element.rectangle()
        box = [rect.left, rect.top, rect.right, rect.bottom]
    except Exception:  # some controls have no geometry
        box = None
    return {"name": (info.name or "").strip(), "kind": info.control_type or "", "box": box,
            "enabled": bool(getattr(info, "enabled", True)), "automation_id": getattr(info, "automation_id", "") or ""}


class UIAutomation:
    def __init__(self, desktop: Callable[[], Any] | None = None) -> None:
        self._desktop = desktop

    def desktop(self) -> Any:
        if self._desktop is not None:
            return self._desktop()
        from pywinauto import Desktop
        return Desktop(backend="uia")

    # ------------------------------------------------------------------ the window
    def window(self, window: str = "") -> Any:
        """A named window (an app such as notepad, or words of its title), else the topmost one that is not HAYAT."""
        from app.windows.applications import WindowsApplicationController
        pattern = WindowsApplicationController.TITLE_PATTERNS.get(window.strip().lower()) if window else None
        candidates = []
        for item in self.desktop().windows(visible_only=True, top_level_only=True):
            try:
                title = item.window_text() or ""
            except Exception:  # a window closing while listed
                log_swallowed(_log, "reading a window title")
                continue
            if not title.strip() or title.startswith("HAYAT") or title in ("Program Manager", "Task Switching"):
                continue
            if window:
                if pattern and not re.match(pattern, title):
                    continue
                if not pattern and window.strip().lower() not in title.lower():
                    continue
            candidates.append(item)
        if not candidates:
            raise LookupError(f"No open window matches {window!r}." if window else "No open window to work with besides HAYAT.")
        return candidates[0]

    def _elements(self, target: Any, kinds: tuple[str, ...]) -> list[Any]:
        try:
            items = target.descendants(depth=DEPTH)
        except TypeError:  # older pywinauto: no depth argument
            items = target.descendants()
        return [item for item in items if (item.element_info.control_type or "") in kinds]

    # ------------------------------------------------------------------ reading
    def read(self, window: str = "", max_chars: int = 6000) -> dict[str, Any]:
        target = self.window(window)
        lines, seen, content_lines = [], set(), 0
        for item in self._elements(target, TEXTUAL):
            info = item.element_info
            # Accessibility values can expose the actual password behind bullets.
            try:
                if getattr(info, "is_password", False) or getattr(getattr(info, "element", None), "CurrentIsPassword", False):
                    continue
            except Exception:
                log_swallowed(_log, "checking a control's password flag")
                continue
            text = (info.name or "").strip()
            if info.control_type in ("Edit", "Document"):
                try:
                    value = item.get_value() if hasattr(item, "get_value") else ""
                    text = (value or text or "").strip()
                except Exception:
                    log_swallowed(_log, "reading a text box's value")
            if text and text not in seen:
                seen.add(text)
                lines.append(text)
                if info.control_type in ("Text", "Edit", "Document", "DataItem"):
                    content_lines += 1
        content = "\n".join(lines)
        return {"window": target.window_text(), "text": content[:max_chars], "truncated": len(content) > max_chars, "lines": len(lines),
                "read_at": time.strftime("%H:%M:%S"), "method": "Windows UI Automation", "content_lines": content_lines}

    def elements(self, window: str = "", limit: int = 120) -> dict[str, Any]:
        target = self.window(window)
        rows = []
        for item in self._elements(target, INTERACTIVE):
            row = _info(item)
            if not row["name"] and not row["automation_id"]:
                continue
            rows.append(row)
            if len(rows) >= limit:
                break
        return {"window": target.window_text(), "elements": rows, "count": len(rows),
                "basis": "Names as the app gives them to screen readers; press one with ui_click by its name."}

    # ------------------------------------------------------------------ acting
    def _match(self, target: Any, name: str, kind: str, index: int) -> Any:
        wanted = name.strip().lower().replace("&", "")
        kinds = (kind,) if kind else INTERACTIVE
        items = self._elements(target, kinds)
        named = lambda item: (item.element_info.name or "").strip().lower().replace("&", "")
        exact = [item for item in items if named(item) == wanted or (item.element_info.automation_id or "").lower() == wanted]
        matches = exact or [item for item in items if wanted and wanted in named(item)]
        if not matches:
            nearby = sorted({(item.element_info.name or "").strip() for item in items if (item.element_info.name or "").strip()})[:15]
            raise LookupError(f"No control named {name!r} in {target.window_text()!r}." + (" It has: " + ", ".join(nearby) if nearby else ""))
        if len(matches) > 1 and index < 1:
            options = "; ".join(f"{position}. {item.element_info.name} ({item.element_info.control_type})"
                                for position, item in enumerate(matches[:8], 1))
            raise LookupError(f"{len(matches)} controls match {name!r}: {options}. Say which one (index).")
        chosen = matches[max(0, index - 1)] if index else matches[0]
        if not chosen.element_info.enabled:
            raise PermissionError(f"{chosen.element_info.name!r} is greyed out (disabled) right now.")
        return chosen

    def click(self, name: str, kind: str = "", window: str = "", index: int = 0) -> dict[str, Any]:
        target = self.window(window)
        before = target.window_text()
        control = self._match(target, name, kind, index)
        label = control.element_info.name
        method = "invoke"
        try:
            control.invoke()
        except Exception:
            method = "click"
            control.click_input()
        time.sleep(.3)
        try:
            after = self.window(window).window_text() if window else before
        except LookupError:
            after = "(the window closed)"
        return {"clicked": label, "kind": control.element_info.control_type, "window": before, "window_after": after, "method": method,
                "complete": True}

    def type(self, name: str, text: str, window: str = "", replace: bool = True) -> dict[str, Any]:
        target = self.window(window)
        edits = self._elements(target, ("Edit", "ComboBox", "Document"))
        if not name and len(edits) == 1:
            control = edits[0]
        else:
            control = self._match(target, name, "Edit", 0) if name else None
        if control is None:
            raise LookupError("Name the box to type into: this window has " + str(len(edits)) + " text boxes.")
        try:
            existing = str(control.get_value() if hasattr(control, "get_value") else "")
        except Exception:
            existing = ""
        # Replacing is for single-line fields (a file name, a search box). A document or a multi-line editor is never
        # wiped: in a live check the whole text of an open Notepad tab was replaced; text there goes in at the cursor.
        single_line = control.element_info.control_type in ("Edit", "ComboBox") and "\n" not in existing and "\r" not in existing
        if replace and single_line and hasattr(control, "set_edit_text"):
            control.set_edit_text(text)
        else:
            control.type_keys(text, with_spaces=True, set_foreground=True)
        try:
            readback = control.get_value() if hasattr(control, "get_value") else ""
        except Exception:
            readback = ""
        return {"typed_into": control.element_info.name or "(text box)", "window": target.window_text(),
                "verified": bool(readback) and text in str(readback), "complete": True}


def register_ui_automation_tools(registry: Any, automation: UIAutomation | None = None) -> UIAutomation:
    automation = automation or UIAutomation()

    def obj(properties: dict[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
        return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}
    window = {"type": "string", "maxLength": 120, "description": "An app (notepad, chrome, excel…) or words of the window title; "
                                                                  "empty = the topmost window that is not HAYAT."}
    registry.register("ui_read", "Read a desktop window's title and visible text through Windows UI Automation (what a screen reader "
                      "hears). Read-only.", obj({"window": window, "max_chars": {"type": "integer", "minimum": 200, "maximum": 50000}}),
                      "low", "computer_observe", automation.read)
    registry.register("ui_elements", "List a desktop window's buttons, menu items, boxes, checkboxes and tabs by name, kind and position, "
                      "through Windows UI Automation. Read-only.", obj({"window": window, "limit": {"type": "integer", "minimum": 1, "maximum": 400}}),
                      "low", "computer_observe", automation.elements)
    registry.register("ui_click", "Press a button, menu item, tab or checkbox in a desktop window by its name (\"Save\", \"OK\", \"File\"), "
                      "wherever it is on screen; several matches are offered, never guessed. Approval required.",
                      obj({"name": {"type": "string", "minLength": 1, "maxLength": 120}, "kind": {"enum": ["", *INTERACTIVE]}, "window": window,
                           "index": {"type": "integer", "minimum": 0, "maximum": 50}}, ("name",)),
                      "high", "computer_input", automation.click)
    registry.register("ui_type", "Type text into a named text box in a desktop window (or its only one) and read it back. A "
                      "single-line field is replaced; a document or multi-line editor gets the text at its cursor and is never "
                      "wiped. Approval required.", obj({"name": {"type": "string", "maxLength": 120}, "text": {"type": "string", "maxLength": 20000},
                                         "window": window, "replace": {"type": "boolean"}}, ("text",)),
                      "high", "computer_input", lambda text, name='', window='', replace=True: automation.type(name, text, window, replace))
    return automation


_WINDOW = r"(?:\s+(?:in|on|of)\s+(?:the\s+)?(?P<window>[\w .'&-]{2,60}?)(?:\s+(?:window|app|application))?)?"
_CLICK = re.compile(r"^(?:please\s+)?(?:click|tap|press|choose|select)\s+(?:on\s+)?(?:the\s+)?['\"]?(?P<name>[^'\"]{1,80}?)['\"]?"
                    r"(?:\s+(?P<kind>button|menu item|menu|tab|link|checkbox))" + _WINDOW + r"\s*[.!]?$", re.IGNORECASE)
_CLICK_BARE = re.compile(r"^(?:please\s+)?(?:click|tap)\s+(?:on\s+)?(?:the\s+)?['\"]?(?P<name>[^'\"\d][^'\"]{0,79}?)['\"]?" + _WINDOW + r"\s*[.!]?$",
                         re.IGNORECASE)
_TYPE = re.compile(r"^(?:please\s+)?(?:type|enter|write)\s+['\"](?P<text>[^'\"]{1,2000})['\"]\s+(?:in|into|in to)\s+(?:the\s+)?"
                   r"(?P<name>[\w .'&-]{1,60}?)(?:\s+(?:box|field|text box))?" + _WINDOW + r"\s*[.!]?$", re.IGNORECASE)
_READ = re.compile(r"^(?:please\s+)?(?:read|what\s+does|what(?:'s|\s+is)\s+in)\s+(?:the\s+)?(?P<window>[\w .'&-]{2,60}?)\s+(?:window|app)"
                   r"(?:\s+say)?\s*[?.!]?$", re.IGNORECASE)
_ELEMENTS = re.compile(r"^(?:please\s+)?(?:what|which|list(?:\s+the)?|show(?:\s+me)?(?:\s+the)?)\s+(?:buttons|controls|options|menus)"
                       r"(?:\s+(?:are|can\s+i\s+press))?\s+(?:in|on)\s+(?:the\s+)?(?P<window>[\w .'&-]{2,60}?)(?:\s+(?:window|app))?\s*[?.!]?$",
                       re.IGNORECASE)
UI_TOOL_NAMES = ("ui_read", "ui_elements", "ui_click", "ui_type")
KINDS = {"button": "Button", "menu item": "MenuItem", "menu": "MenuItem", "tab": "TabItem", "link": "Hyperlink", "checkbox": "CheckBox"}


def ui_plan(goal: str) -> list[tuple[str, str, dict[str, Any]]] | None:
    """Desktop requests by control name, in plain words; None when the words are something else."""
    text = " ".join(str(goal or "").split())
    if match := _TYPE.match(text):
        window = (match["window"] or "").strip()
        return [("Type into the named box and read it back.", "ui_type",
                 {"name": match["name"].strip(), "text": match["text"], **({"window": window} if window else {})})]
    if match := _READ.match(text):
        return [("Read the window's text through UI Automation.", "ui_read", {"window": match["window"].strip()})]
    if match := _ELEMENTS.match(text):
        return [("List the window's controls through UI Automation.", "ui_elements", {"window": match["window"].strip()})]
    match = _CLICK.match(text) or _CLICK_BARE.match(text)
    if match:
        name = match["name"].strip()
        if re.fullmatch(r"\d+\s*,\s*\d+|(?:at|on)\s.*|here|there|this|that|it|them|somewhere|anywhere|the|a|an|one|button|link", name, re.IGNORECASE):
            return None  # coordinates stay with the pointer tools; "click here" names nothing
        if re.match(r"(?:web\s*)?(?:browser|page|website|site|tab's|web)\b", name, re.IGNORECASE):
            return None  # "click the browser login link" is a web page's link: the browser tools read and press those
        window = (match["window"] or "").strip()
        kind = KINDS.get((match.groupdict().get("kind") or "").lower(), "")
        arguments = {"name": name, **({"kind": kind} if kind else {}), **({"window": window} if window else {})}
        return [("Press the named control through UI Automation.", "ui_click", arguments),
                ("Read the window after pressing, to confirm the effect.", "ui_read", {"window": window} if window else {})]
    return None
