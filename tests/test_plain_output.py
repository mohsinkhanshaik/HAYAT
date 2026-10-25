"""Results are said in plain lines, never pasted as code.

Real replies from the owner's history: "Final evidence: {'application':
'notepad', 'pid': 48848, 'running': True}", a reply that was the router's
intent dictionary, and slash commands that printed raw JSON.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.plain_output import describe, label, scalar

NOTEPAD = {"application": "notepad", "path": r"C:\Windows\System32\notepad.exe", "pid": 48848, "running": True}
SEARCH = {"query": "Python 3.13 release date", "count": 2, "results": [
    {"title": "Python Release Python 3.13.0", "url": "https://www.python.org/downloads/release/python-3130/"},
    {"title": "PEP 719", "url": "https://peps.python.org/pep-0719/"}]}


def no_code(text):
    assert not any(mark in text for mark in ("{", "}", "':", '":', "True", "False", "None")), text


def test_a_tool_result_becomes_labelled_plain_lines():
    text = describe(NOTEPAD)
    no_code(text)
    assert "Application: notepad" in text and "Process ID: 48848" in text and "Running: yes" in text


def test_lists_show_a_count_and_each_item_by_its_title():
    text = describe(SEARCH)
    no_code(text)
    assert "Results (2):" in text and "• PEP 719 (https://peps.python.org/pep-0719/)" in text


def test_long_lists_are_bounded_and_say_how_many_more():
    text = describe({"files": [{"name": f"f{i}.txt"} for i in range(20)]})
    assert "Files (20):" in text and "… and 12 more" in text


def test_values_read_like_a_person_wrote_them():
    assert label("target_application") == "Target application"
    assert label("pid") == "Process ID" and label("elapsedMs") == "Elapsed ms"
    assert scalar(None) == "not available" and scalar(False) == "no"
    assert scalar(0.954, "confidence") == "95.4%"
    assert scalar(2_497_280_256, "size") == "2.3 GB"


def test_text_that_is_a_printed_dictionary_is_parsed_and_described():
    text = describe(str(NOTEPAD))  # Python's printout, as it used to reach replies
    no_code(text)
    assert "Process ID: 48848" in text
    assert describe("Just a sentence.") == "Just a sentence."


def test_a_purpose_written_sentence_still_wins():
    from app.observed_reply import render

    listing = {"path": "D:/AI", "entries": [{"name": "a.txt", "type": "file", "size": 3}]}
    if render(listing):
        assert describe(listing) == render(listing)


def test_task_summaries_are_readable():
    from app.agent.core import Agent

    history = [{"verification": {"passed": False}, "result": {"output": SEARCH}}]
    summary = Agent._summarize("Python 3.13 release date", history, False, SimpleNamespace(reason="0/1 user-goal criteria satisfied"))
    assert summary.startswith("Task failed.")  # older records and parsers keep this opening
    assert "what was asked for was not confirmed" in summary and "criteria" not in summary
    no_code(summary.split("Last result:")[1])
    timed_out = Agent._summarize("x", [{"verification": {"passed": False},
                                       "result": {"output": {"error": "model timed out after 120 s"}}}], False)
    assert "model timed out" in timed_out


@pytest.mark.parametrize("display,expected", [
    ("{'intent': 'CONVERSATION', 'confidence': 0.95, 'requires_tools': True}", "Intent: CONVERSATION"),
    ({"application": "notepad", "running": True}, "Running: yes"),
])
def test_a_reply_is_never_a_pasted_python_object(display, expected):
    from app.assistant import AssistantService

    text = AssistantService._never_raw(display)
    assert expected in text
    no_code(text)


def test_json_the_owner_asked_for_is_left_alone():
    from app.assistant import AssistantService

    asked = json.dumps({"name": "HAYAT", "version": 1}, indent=2)
    assert AssistantService._never_raw(asked) == asked
    assert AssistantService._never_raw("Here you go: {not code}") == "Here you go: {not code}"


def test_cli_commands_print_plain_lines_and_json_on_request(capsys, monkeypatch):
    from app.cli import _show

    monkeypatch.delenv("HAYAT_CLI_JSON", raising=False)
    _show(NOTEPAD)
    plain = capsys.readouterr().out
    no_code(plain)
    assert "Process ID: 48848" in plain
    monkeypatch.setenv("HAYAT_CLI_JSON", "1")
    _show(NOTEPAD)
    assert json.loads(capsys.readouterr().out) == NOTEPAD


def test_search_failures_are_one_plain_sentence():
    from app.research.jobs import plain_errors

    refused = ("duckduckgo_html: ConnectError: [WinError 10061] No connection could be made because the target "
               "machine actively refused it; bing_rss: ConnectError: [WinError 10061] No connection could be made "
               "because the target machine actively refused it")
    text = plain_errors(refused)
    assert text.startswith("Every search source tried (DuckDuckGo and Bing) failed: the connection was refused")
    assert "WinError" not in text and "ConnectError" not in text and "_" not in text
    mixed = plain_errors("duckduckgo_html: ReadTimeout: timed out; bing_rss: ConnectError: getaddrinfo failed")
    assert "DuckDuckGo failed: it took too long to answer." in mixed and "Bing failed: the address could not be looked up" in mixed
    assert plain_errors("") == "" and plain_errors("No usable evidence.") == "No usable evidence."


def test_the_user_guide_matches_the_capability_catalog():
    """docs/how-to-use-hayat.md is generated; run scripts/generate_user_guide.py after changing capabilities."""
    from scripts.generate_user_guide import TARGET, render

    assert TARGET.read_text(encoding="utf-8") == render()
    from app.capabilities import CATALOG

    guide = TARGET.read_text(encoding="utf-8")
    assert all(f"### {capability.title}" in guide for capability in CATALOG)


def test_a_tools_input_contract_is_a_readable_list_not_a_json_schema():
    from app.plain_output import describe_schema

    schema = {"type": "object", "required": ["input_path"], "properties": {
        "input_path": {"type": "string", "minLength": 1},
        "format": {"enum": ["directory", "asar"]},
        "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10, "description": "How many results."}}}
    text = describe_schema(schema)
    assert "• input_path (text, required)" in text
    assert "format (optional): one of directory, asar" in text
    assert "limit (whole number, optional, 1 to 50); default 10 — How many results." in text
    assert "{" not in text and '"type"' not in text
    assert describe_schema({"type": "object", "properties": {}}) == "No inputs needed."


def test_status_lines_say_the_detail_not_the_dictionary():
    from app.gui_studio import _plain

    assert _plain({"detail": "GUI-owned Colibri stopped."}) == "GUI-owned Colibri stopped."
    assert "{" not in _plain({"running": True, "port": 8080}) and "Running: yes" in _plain({"running": True})
    assert _plain("already text") == "already text"


def test_intelligence_source_details_end_with_a_readable_full_record():
    from app.gui_intelligence import source_detail as source_details

    text = source_details({"title": "USGS earthquakes", "key": "usgs", "status": "ok", "stale": False,
                           "interval_seconds": 600, "license": "Public domain", "change": {"added_count": 2},
                           "count": 41, "enabled": True})
    assert "Full record" in text and "Enabled: yes" in text
    assert "Raw record" not in text and '"enabled": true' not in text
