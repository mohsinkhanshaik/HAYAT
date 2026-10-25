"""Desktop apps by their named controls (Windows UI Automation), on fake windows: no real window is touched."""
import pytest

from app.tools.computer import direct_computer_plan
from app.tools.ui_automation import UIAutomation, register_ui_automation_tools, ui_plan


class Info:
    def __init__(self, name, kind, enabled=True, automation_id=""):
        self.name, self.control_type, self.enabled, self.automation_id = name, kind, enabled, automation_id


class Rect:
    left, top, right, bottom = 10, 20, 110, 40


class Control:
    def __init__(self, name, kind, value=None, enabled=True, invokable=True):
        self.element_info = Info(name, kind, enabled)
        self.value, self.invokable, self.pressed, self.keys = value, invokable, [], []

    def rectangle(self):
        return Rect()

    def invoke(self):
        if not self.invokable:
            raise RuntimeError("no invoke pattern")
        self.pressed.append("invoke")

    def click_input(self):
        self.pressed.append("click")

    def get_value(self):
        return self.value or ""

    def set_edit_text(self, text):
        self.value = text

    def type_keys(self, text, with_spaces=True, set_foreground=True):
        self.keys.append(text)
        self.value = (self.value or "") + text


class Window:
    def __init__(self, title, controls):
        self.title, self.controls = title, controls

    def window_text(self):
        return self.title

    def descendants(self, depth=None):
        return list(self.controls)


class Desktop:
    def __init__(self, *windows):
        self.list = list(windows)

    def windows(self, visible_only=True, top_level_only=True):
        return self.list


@pytest.fixture
def notepad():
    return Window("notes.txt - Notepad", [Control("File", "MenuItem"), Control("Edit", "MenuItem"), Control("Save", "Button"),
                                           Control("Save as", "Button"), Control("Print", "Button", enabled=False),
                                           Control("Text editor", "Document", value="line one\r\nline two")])


def automation(*windows):
    return UIAutomation(lambda: Desktop(*windows))


def test_the_window_is_found_by_app_or_title_and_hayat_itself_is_never_the_target(notepad):
    hayat = Window("HAYAT", [Control("Send", "Button")])
    ui = automation(hayat, Window("Program Manager", []), notepad, Window("Report - Word", []))
    assert ui.window().window_text() == "notes.txt - Notepad"  # topmost that is not HAYAT
    assert ui.window("notepad") is notepad and ui.window("report").window_text() == "Report - Word"
    with pytest.raises(LookupError, match="No open window matches 'excel'"):
        ui.window("excel")
    with pytest.raises(LookupError, match="besides HAYAT"):
        automation(hayat).window()


def test_reading_and_listing_controls_never_presses_anything(notepad):
    ui = automation(notepad)
    read = ui.read("notepad")
    assert "line one" in read["text"] and read["method"] == "Windows UI Automation"
    listed = ui.elements("notepad")
    assert [row["name"] for row in listed["elements"]] == ["File", "Edit", "Save", "Save as", "Print"]
    assert listed["elements"][4]["enabled"] is False and listed["elements"][0]["box"] == [10, 20, 110, 40]
    assert not any(control.pressed or control.keys for control in notepad.controls)


def test_a_control_is_pressed_by_its_exact_name_before_a_partial_one(notepad):
    ui = automation(notepad)
    result = ui.click("save", window="notepad")
    assert result["clicked"] == "Save" and result["method"] == "invoke"
    assert notepad.controls[2].pressed == ["invoke"] and notepad.controls[3].pressed == []


def test_several_matches_are_offered_never_guessed_and_a_missing_name_lists_what_is_there(notepad):
    ui = automation(Window("App", [Control("Save draft", "Button"), Control("Save copy", "Button")]))
    with pytest.raises(LookupError, match="2 controls match 'save'.*1. Save draft.*2. Save copy"):
        ui.click("save")
    assert ui.click("save", index=2)["clicked"] == "Save copy"
    with pytest.raises(LookupError, match="No control named 'Open'.*It has: Save copy, Save draft"):
        ui.click("Open")


def test_a_greyed_out_control_is_not_pressed_and_one_without_invoke_is_clicked(notepad):
    ui = automation(notepad)
    with pytest.raises(PermissionError, match="greyed out"):
        ui.click("Print", window="notepad")
    stubborn = Control("Go", "Button", invokable=False)
    assert automation(Window("App", [stubborn])).click("Go")["method"] == "click" and stubborn.pressed == ["click"]


def test_a_single_line_box_is_replaced_and_read_back():
    box = Control("File name", "Edit", value="old.txt")
    result = automation(Window("Save as", [box, Control("Search", "Edit")])).type("file name", "report.txt")
    assert box.value == "report.txt" and result["verified"] and box.keys == []


def test_a_document_is_never_wiped_text_goes_in_at_the_cursor(notepad):
    """A live check replaced the whole text of an open Notepad tab; a document now only ever gets text added."""
    ui = automation(notepad)
    result = ui.type("", " and three", "notepad")
    editor = notepad.controls[5]
    assert editor.value == "line one\r\nline two and three" and editor.keys == [" and three"] and result["verified"]
    multi = Control("Notes", "Edit", value="first\nsecond")
    automation(Window("App", [multi])).type("Notes", "!", replace=True)
    assert multi.value == "first\nsecond!"  # a multi-line box is not replaced either


def test_typing_without_a_name_needs_a_single_box():
    with pytest.raises(LookupError, match="Name the box to type into: this window has 2 text boxes"):
        automation(Window("Form", [Control("First", "Edit"), Control("Last", "Edit")])).type("", "x")


def test_the_tools_keep_the_existing_permissions():
    class Registry:
        def __init__(self):
            self.tools = {}

        def register(self, name, description, schema, risk, category, fn):
            self.tools[name] = (risk, category)
    registry = Registry()
    register_ui_automation_tools(registry, automation())
    assert registry.tools == {"ui_read": ("low", "computer_observe"), "ui_elements": ("low", "computer_observe"),
                              "ui_click": ("high", "computer_input"), "ui_type": ("high", "computer_input")}


@pytest.mark.parametrize("words,tool,arguments", [
    ("click the Save button in notepad", "ui_click", {"name": "Save", "kind": "Button", "window": "notepad"}),
    ("press \"OK\" button", "ui_click", {"name": "OK", "kind": "Button"}),
    ("click File menu in the notepad window", "ui_click", {"name": "File", "kind": "MenuItem", "window": "notepad"}),
    ("click Settings", "ui_click", {"name": "Settings"}),
    ("type 'report.txt' into the file name box", "ui_type", {"name": "file name", "text": "report.txt"}),
    ("read the notepad window", "ui_read", {"window": "notepad"}),
    ("what buttons are in the calculator app", "ui_elements", {"window": "calculator"}),
])
def test_plain_words_route_to_the_named_control_tools(words, tool, arguments):
    plan = ui_plan(words)
    assert plan[0][1] == tool and plan[0][2] == arguments
    if tool == "ui_click":
        assert plan[1][1] == "ui_read"  # a press is followed by a look


@pytest.mark.parametrize("words", ["click here", "click it", "click 500, 300", "click at 40 60", "what is the weather today",
                                   "tell me about the button industry",
                                   "choose the cheapest option", "press the button", "select a plan for me",
                                   # a web page's link belongs to the browser tools, not a desktop control named "browser login"
                                   "Click the browser login link", "click the website contact button"])
def test_vague_places_coordinates_and_other_requests_are_left_alone(words):
    assert ui_plan(words) is None


def test_the_computer_planner_hands_named_controls_to_ui_automation():
    from app.intent.router import IntentKind, IntentRouter
    steps = direct_computer_plan("click the Save button in notepad", ["ui_click", "ui_read"])
    assert [step["tool"] for step in steps] == ["ui_click", "ui_read"] and all(step["max_retries"] == 0 for step in steps)
    assert direct_computer_plan("click the Save button in notepad", ["ui_read"]) is None  # tools not registered: no plan
    assert IntentRouter().route("click the Save button in notepad").intent == IntentKind.COMPUTER_CONTROL


def test_the_planner_offers_the_named_control_tools_only_for_a_window_and_its_controls():
    from app.agent.planner import Planner
    from app.runtime import build_runtime
    runtime = build_runtime(use_model=False, start_background_services=False)
    try:
        planner = Planner(runtime.registry)
        assert "ui_click" in planner._candidate_tools("in notepad hit the Save button and then close the window")
        assert not any(name.startswith("ui_") for name in planner._candidate_tools("where am I right now"))
        assert not any(name.startswith("ui_") for name in planner._candidate_tools("what's plugged into this pc"))
    finally:
        runtime.close()
