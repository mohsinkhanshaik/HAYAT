"""Replies say what the tools found, and file questions reach the file tools."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.intent.local_files import local_file_request
from app.intent.models import IntentKind
from app.intent.router import IntentRouter
from app.observed_reply import explain_failure, render, report
from app.session import SessionState


def _step(tool, output, passed=True, error=None, arguments=None):
    return {"tool": tool, "arguments": arguments or {},
            "result": {"output": output, "success": error is None, "error": error},
            "verification": {"passed": passed}}


def test_a_listing_is_the_reply_not_the_tool_name():
    reply = report([_step("list_folder", {"path": "D:/AI/app/voice", "entries": [
        {"path": "app/voice/tts.py", "type": "file", "size": 15872},
        {"path": "app/voice/cache", "type": "directory"},
    ]})])
    assert reply.startswith("D:/AI/app/voice has 2 items (1 folder):")
    assert "tts.py  (15.5 KB)" in reply and "cache/" in reply
    assert "Done" not in reply


def test_long_outputs_are_cut_and_say_so():
    entries = [{"path": f"f{index}.txt", "type": "file", "size": 1} for index in range(40)]
    assert "… and 25 more" in render({"path": "x", "entries": entries})
    text = render({"path": "big.txt", "content": "\n".join(f"line {index}" for index in range(100))})
    assert "line 39" in text and "line 40" not in text and "100 lines in total" in text


def test_file_text_script_output_info_and_system_status_are_rendered():
    assert render({"path": "D:/AI/notes.txt", "content": "hello"}) == "notes.txt:\nhello"
    assert render({"path": "D:/AI/empty.txt", "content": "  "}) == "empty.txt is empty."
    assert "printed:\nHello from my AI" in render({"exit_code": 0, "stdout": "Hello from my AI\n", "stderr": ""})
    assert "exited with code 1:\nTraceback" in render({"exit_code": 1, "stdout": "", "stderr": "Traceback ..."})
    assert render({"path": "D:/AI/README.md", "exists": False}) == "README.md does not exist."
    assert "a file of 1.0 KB" in render({"path": "a.txt", "size": 1024, "modified": 0, "is_file": True})
    assert "Python 3.14.2" in render({"platform": "Windows-11", "python": "3.14.2", "disk_total": 2**40, "disk_free": 2**39})
    assert render({"something": "unrecognised"}) is None


def test_secrets_in_file_text_are_redacted():
    reply = render({"path": ".env", "content": "OPENAI_API_KEY=sk-proj-abcdefghijklmnopqrstuvwxyz0123456789ABCD"})
    assert "abcdefghijklmnopqrstuvwxyz0123456789" not in reply


def test_unverified_steps_contribute_nothing():
    assert report([_step("read_file", {"path": "a.txt", "content": "secret plan"}, passed=False)]) is None


def test_a_failure_is_explained_in_plain_words():
    missing = [_step("read_file", None, passed=False, arguments={"path": "missing.txt"},
                     error="FileNotFoundError: [Errno 2] No such file or directory: 'D:\\AI\\missing.txt'")]
    assert explain_failure("Read missing.txt", missing) == "I couldn't read missing.txt: there is no missing.txt."
    nowhere = [_step("list_folder", None, passed=False, arguments={"path": "no/such/dir"},
                     error="NotADirectoryError: D:\\AI\\no\\such\\dir")]
    assert explain_failure("list no/such/dir", nowhere) == "I couldn't list no/such/dir: there is no folder no/such/dir."
    assert explain_failure("anything", []) is None


@pytest.mark.parametrize("request_text, expected", [
    ("list the files in app/voice", {"file_operation": "list", "path": "app/voice"}),
    ("can you list the files in tests?", {"file_operation": "list", "path": "tests"}),
    ("show me the files in this project", {"file_operation": "list", "path": "."}),
    ("what are the files in the scripts folder", {"file_operation": "list", "path": "scripts"}),
    ("read config/settings.yaml", {"file_operation": "read", "path": "config/settings.yaml"}),
    ("show me the contents of notes.txt", {"file_operation": "read", "path": "notes.txt"}),
    ("find all python files in app/voice", {"file_operation": "find", "path": "app/voice", "pattern": "*.py"}),
])
def test_file_requests_are_recognised(request_text, expected):
    assert local_file_request(request_text) == expected


@pytest.mark.parametrize("request_text", [
    "read the news", "show me the weather", "what is in lunch", "what is recursion",
    "read example.com", "list the things I need to buy", "open https://example.com/a.txt",
])
def test_other_requests_are_not_file_requests(request_text):
    assert local_file_request(request_text) is None


def test_the_router_sends_file_requests_to_the_file_tools_not_the_model_or_web():
    router = IntentRouter()
    for text in ("list the files in app/voice", "read config/settings.yaml", "can you list the files in tests?"):
        intent = router.route(text, SessionState())
        assert intent.intent == IntentKind.ACTION, text
        assert intent.entities.get("file_operation"), text
    # A real site is still a site.
    assert router.route("read example.com", SessionState()).intent == IntentKind.BROWSER


def test_the_planner_maps_each_file_operation_to_its_tool():
    from app.agent.planner import Planner

    planner = Planner.__new__(Planner)
    planner.registry = SimpleNamespace(names=lambda: ["list_folder", "read_file", "search_files"])
    for entities, tool in (({"file_operation": "list", "path": "app"}, "list_folder"),
                           ({"file_operation": "read", "path": "a.txt"}, "read_file"),
                           ({"file_operation": "find", "path": "app", "pattern": "*.py"}, "search_files")):
        steps = planner._deterministic_plan("goal", SimpleNamespace(entities=entities))
        assert [step.tool for step in steps] == [tool]


def test_a_new_invention_draft_says_what_was_made_and_how_to_go_on():
    # Live 2026-10-24: "Create invention project: design a low-cost water monitor" got only an id and "DRAFT (0 rounds)".
    from app.observed_reply import _invention

    project = {"id": "invent-" + "a" * 32, "goal": "design a low-cost water monitor", "state": "DRAFT", "rounds": [],
               "tests": [], "acceptance_hash": "h", "best": None, "stop_reason": ""}
    text = _invention(project)
    assert "design a low-cost water monitor" in text and "no test cases yet" in text
    assert f"work out invention {project['id']}" in text and "not verified" in text
    worked = _invention({**project, "rounds": [{}], "tests": [{"inputs": {"x": 1}, "expected": 2}]})
    assert worked.startswith("Invention invent-")  # a project with rounds keeps its report


def test_person_detection_shows_confidence_size_and_the_identity_boundary():
    # Live 2026-10-24: a detection on a photo came back as raw JSON with no readable detail.
    from app.observed_reply import render

    detect = {"person_detection": True, "count": 1, "width": 1254, "height": 1254, "threshold": 0.5,
              "people": [{"box": [11, 79, 1247, 1247], "score": 0.9973}], "path": r"C:\Users\me\Downloads\photo.png"}
    text = render(detect)
    assert "Found 1 person in photo.png" in text and "99.73% confidence" in text
    assert "about 92% of the frame" in text and "centre" in text
    assert "not who it is" in text and "never identifies an unknown person from a face" in text
    assert "100%" not in text  # a 0.9973 score must not read as certain


def test_person_detection_zero_count_and_status_are_readable():
    from app.observed_reply import render

    empty = {"person_detection": True, "count": 0, "people": [], "width": 800, "height": 600, "threshold": 0.6}
    assert "No person was detected" in render(empty) and "60% confidence" in render(empty)
    ready = {"person_detection": True, "installed": True, "dependencies": {"torch": True, "torchvision": True}}
    assert "installed and ready" in render(ready) and "does not identify anyone" in render(ready)
    missing = {"person_detection": True, "installed": False}
    assert "not installed yet" in render(missing) and "setup_person_detection.py" in render(missing)
    needs = {"person_detection": True, "installed": True, "dependencies": {"torch": True, "torchvision": False}}
    assert "torchvision" in render(needs)
