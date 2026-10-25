"""What HAYAT knows about its own tools, and what it must never learn from them."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.selfimprove import FAILURE_RATE_THRESHOLD, SelfImprovement
from app.tools.introspection import register_introspection_tools
from app.tools.registry import ToolRegistry, ToolResult
from app.tools.understanding import MINIMUM_CALLS_FOR_A_VERDICT, ToolUnderstanding


@pytest.fixture
def understanding(tmp_path: Path) -> ToolUnderstanding:
    return ToolUnderstanding(tmp_path / "tools.db")


def _result(tool: str, ok: bool, *, ms: int = 5, error: str = "", permission: str = "ALLOW"):
    return ToolResult(tool, ok, error=error, duration_ms=ms, permission=permission)


# ------------------------------------------------------------ counting truthfully


def test_a_tool_never_used_is_admitted_rather_than_guessed_about(understanding):
    assert understanding.record_for("never_run") == {}
    assert "never actually used" in understanding.explain("never_run")


def test_the_rate_has_a_denominator(understanding):
    """The whole reason this record exists.

    SelfImprovement declared FAILURE_RATE_THRESHOLD and could never apply it,
    because only failures were counted. Four failures out of four and four out
    of four hundred were indistinguishable.
    """
    for _ in range(9):
        understanding.observe(_result("steady", True))
    understanding.observe(_result("steady", False, error="TimeoutError: too slow"))
    for _ in range(4):
        understanding.observe(_result("broken", False, error="TimeoutError: too slow"))

    steady = understanding.record_for("steady")
    broken = understanding.record_for("broken")
    assert steady["calls"] == 10 and steady["failure_rate"] == pytest.approx(0.1)
    assert broken["calls"] == 4 and broken["failure_rate"] == pytest.approx(1.0)


def test_a_verdict_waits_for_enough_evidence(understanding):
    """Two failures out of two says the tool is new, not that it is broken."""
    understanding.observe(_result("young", False, error="ValueError: no"))
    understanding.observe(_result("young", False, error="ValueError: no"))
    assert understanding.record_for("young")["failure_rate"] == pytest.approx(1.0)
    assert not understanding.record_for("young")["enough_evidence"]
    assert not understanding.unreliable(FAILURE_RATE_THRESHOLD)

    for _ in range(MINIMUM_CALLS_FOR_A_VERDICT):
        understanding.observe(_result("young", False, error="ValueError: no"))
    assert [item["tool"] for item in understanding.unreliable(FAILURE_RATE_THRESHOLD)] == ["young"]


def test_being_refused_is_not_the_tool_failing(understanding):
    """A tool the authority kernel stopped worked exactly as designed.

    Counting a refusal against a tool's reliability would eventually teach
    HAYAT that its own safety boundary is a defect worth engineering around.
    """
    for _ in range(6):
        understanding.observe(_result("guarded", False, error="Denied by policy", permission="DENY"))
    understanding.observe(_result("guarded", True))

    record = understanding.record_for("guarded")
    assert record["refused"] == 6
    assert record["failed"] == 0
    assert record["failure_rate"] == 0.0
    assert not understanding.unreliable(FAILURE_RATE_THRESHOLD)


def test_recent_observations_recognize_recovery_and_new_regressions(understanding):
    for _ in range(40):
        understanding.observe(_result('recovering', False, error='TimeoutError: unavailable'))
        understanding.observe(_result('regressing', True))
    for _ in range(20):
        understanding.observe(_result('recovering', True))
        understanding.observe(_result('regressing', False, error='RuntimeError: stopped'))
    # Refusals cannot crowd genuine attempts out of the recent window.
    for _ in range(30):
        understanding.observe(_result('regressing', False, permission='DENY'))
    recovered = understanding.record_for('recovering')
    assert recovered['failure_rate'] > .34 and recovered['recent_failure_rate'] == 0
    assert recovered['calls'] == 60 and recovered['failed'] == 40
    assert [r['tool'] for r in understanding.unreliable(.34)] == ['regressing']
    assert 'do not trust' not in understanding.explain('recovering')
    runtime = type('Runtime', (), {'tool_understanding': understanding})()
    proposals = [p for p in SelfImprovement(runtime).review() if p.kind == 'reliability']
    assert len(proposals) == 1 and 'regressing' in proposals[0].title and '100%' in proposals[0].title
    assert '20 of 20 recent' in proposals[0].reason


def test_the_usual_failure_is_remembered(understanding):
    for _ in range(3):
        understanding.observe(_result("flaky", False, error="TimeoutError: waited 30s"))
    understanding.observe(_result("flaky", False, error="PermissionError: locked"))
    assert understanding.record_for("flaky")["typical_failures"][0] == {
        "kind": "TimeoutError", "count": 3, "example": "waited 30s",
    }


# ----------------------------------------------------------------- and privacy


def test_the_record_keeps_no_arguments_no_output_and_no_raw_error_text(understanding, tmp_path):
    """Introspection must not become a way to read back what Mohsin did.

    A tool record is useful at the level of "read_file works and takes 4ms".
    Which file was read, what it contained, and the message quoting either of
    them are surveillance, so they are never written in the first place. A
    failure keeps only its message with the path scrubbed out.
    """
    understanding.observe(ToolResult(
        "read_file", False,
        output="the contents of his private diary",
        error="FileNotFoundError: D:\\private\\mohsin-medical-records.txt is missing",
        duration_ms=4,
    ))
    # Every file the store touches, sidecars included: WAL mode means a row
    # can live in tools.db-wal for a while before it reaches tools.db, and a
    # privacy check that only reads the main file would pass by looking in the
    # wrong place.
    written = "".join(path.read_bytes().decode("latin-1")
                      for path in sorted(tmp_path.glob("tools.db*")))
    assert "mohsin-medical-records" not in written
    assert "private diary" not in written
    assert "FileNotFoundError" in written  # the type alone, which carries none of it

    record = understanding.record_for("read_file")
    assert record["typical_failures"] == [{"kind": "FileNotFoundError", "count": 1, "example": "<path>"}]


def test_a_failure_keeps_enough_of_its_message_to_be_diagnosed(understanding):
    """Seven browser_navigate RuntimeErrors were flagged and could not be
    explained: the type was all that was kept. The message now survives with
    everything private replaced."""
    understanding.observe(_result("browser_navigate", False,
                                  error="RuntimeError: Target page, context or browser has been closed"))
    understanding.observe(_result("browser_navigate", False,
                                  error="RuntimeError: could not open https://bank.example/login for me@example.com"))
    record = understanding.record_for("browser_navigate")
    assert record["typical_failures"][0]["example"] == "could not open <url> for <email>", "most recent first"
    assert "could not open <url>" in understanding.explain("browser_navigate")


def test_an_existing_record_gains_the_message_column_without_losing_history(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE tool_calls (id INTEGER PRIMARY KEY AUTOINCREMENT, tool TEXT NOT NULL, "
                   "ok INTEGER NOT NULL, ms INTEGER NOT NULL DEFAULT 0, error_kind TEXT NOT NULL DEFAULT '', "
                   "refused INTEGER NOT NULL DEFAULT 0, at TEXT NOT NULL)")
        db.execute("INSERT INTO tool_calls (tool, ok, ms, error_kind, at) VALUES ('old_tool', 0, 3, 'OSError', 'x')")
    understanding = ToolUnderstanding(path)
    understanding.observe(_result("old_tool", False, error="OSError: disk unplugged"))
    record = understanding.record_for("old_tool")
    assert record["calls"] == 2
    assert record["typical_failures"] == [{"kind": "OSError", "count": 2, "example": "disk unplugged"}]


def test_bookkeeping_never_breaks_a_tool_call(understanding):
    """A tool must not fail because the record of tools failed."""
    understanding.observe(None)
    understanding.observe(object())
    understanding.observe(_result("", True))
    assert understanding.totals() == []


# ------------------------------------------------------------------- in the loop


def test_dispatch_records_success_and_failure(tmp_path):
    registry = ToolRegistry()
    registry.understanding = ToolUnderstanding(tmp_path / "dispatch.db")
    schema = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
    registry.register("works", "always fine", schema, "low", "system_read", lambda: "fine")

    def explode() -> str:
        raise TimeoutError("the network went away")

    registry.register("explodes", "never fine", schema, "low", "system_read", explode)

    for _ in range(3):
        registry.execute("works", {})
    registry.execute("explodes", {})

    assert registry.understanding.record_for("works")["succeeded"] == 3
    failure = registry.understanding.record_for("explodes")
    assert failure["failed"] == 1
    assert failure["typical_failures"][0]["kind"] == "TimeoutError"


def test_hayat_can_be_asked_about_its_own_tools(tmp_path):
    registry = ToolRegistry()
    record = ToolUnderstanding(tmp_path / "introspect.db")
    registry.understanding = record
    schema = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
    registry.register("search_web", "look something up", schema, "low", "system_read", lambda: "ok")
    register_introspection_tools(registry, record)

    registry.execute("search_web", {})
    detail = registry.execute("explain_my_tools", {"tool": "search_web"})
    assert detail.success
    assert "look something up" in detail.output["answer"]
    assert "1 worked" in detail.output["answer"]

    overview = registry.execute("explain_my_tools", {})
    assert "search_web" in overview.output["answer"]

    missing = registry.execute("explain_my_tools", {"tool": "no_such_tool"})
    assert missing.success and not missing.output["known"]


def test_an_unreliable_tool_becomes_a_proposal_with_its_evidence(tmp_path):
    record = ToolUnderstanding(tmp_path / "improve.db")
    for _ in range(8):
        record.observe(_result("bounded_crawl", False, error="TimeoutError: no route"))
    for _ in range(2):
        record.observe(_result("bounded_crawl", True))

    class Runtime:
        tool_understanding = record

    proposals = SelfImprovement(Runtime()).review()
    crawl = [item for item in proposals if "bounded_crawl" in item.title]
    assert len(crawl) == 1
    assert "80%" in crawl[0].title
    assert "TimeoutError" in crawl[0].reason
    assert crawl[0].needs_permission  # reliability work changes behaviour: ask first


# ------------------------------------- not inventing tools it does not have


def _registry_with_tools(tmp_path):
    registry = ToolRegistry()
    record = ToolUnderstanding(tmp_path / "ref.db")
    registry.understanding = record
    schema = {"type": "object", "properties": {"query": {"type": "string"}},
              "required": [], "additionalProperties": False}
    registry.register("search_web", "look something up online", schema, "low", "network_read",
                      lambda query="": "ok")
    registry.register("read_file", "read a project file", schema, "low", "system_read",
                      lambda query="": "ok")
    return registry, record


def test_a_question_naming_a_real_tool_gets_the_real_tool(tmp_path):
    """Asked how `explain_my_tools` worked, HAYAT invented capability
    analysis, rate limits, and a `code_interpreter` it does not have -- fluent
    and entirely made up, because the prompt held no fact to contradict it.
    A model cannot know what this machine registered; the registry can."""
    from app.tools.introspection import tool_reference

    registry, record = _registry_with_tools(tmp_path)
    facts = tool_reference(registry, record, "how does your search_web tool work?")

    assert "look something up online" in facts
    assert "never actually used" in facts  # honest about having no experience
    assert "takes: query" in facts


def test_experience_is_reported_where_there_is_any(tmp_path):
    from app.tools.introspection import tool_reference

    registry, record = _registry_with_tools(tmp_path)
    for _ in range(4):
        record.observe(_result("search_web", True, ms=120))
    record.observe(_result("search_web", False, ms=120, error="TimeoutError: slow"))

    facts = tool_reference(registry, record, "is search_web any good?")
    assert "4 worked, 1 failed" in facts
    assert "120ms" in facts
    assert "TimeoutError" in facts


def test_asking_about_the_toolbox_generally_gets_the_overview(tmp_path):
    from app.tools.introspection import tool_reference

    registry, record = _registry_with_tools(tmp_path)
    for text in ("what tools do you have?", "which tools do you trust?",
                 "how many tools do you have", "what can you do"):
        assert "2 tools registered" in tool_reference(registry, record, text), text


def test_an_ordinary_remark_pulls_in_no_tool_facts(tmp_path):
    """The reference is for questions about the toolbox. Everything else must
    not have ninety tool descriptions pushed into its prompt."""
    from app.tools.introspection import tool_reference

    registry, record = _registry_with_tools(tmp_path)
    for text in ("i'm tired today", "explain how rust borrowing works", "", "thanks"):
        assert tool_reference(registry, record, text) == "", text


def test_asking_about_an_ability_finds_the_tools_that_provide_it():
    """The question never contains the word "tool", and that was the bug.

    _ASKING_ABOUT_TOOLS matched only questions using the word, so "can you
    scrape websites?" put nothing in front of the model and it answered from
    a general model's priors -- denying capabilities this machine has.
    """
    from app.tools.introspection import _tools_for_ability

    class _Spec:
        def __init__(self, description): self.description = description

    tools = {
        "public_web_fetch": "Safely retrieve and structurally extract one public page or PDF.",
        "capture_web_page": "Render one public webpage and extract its visible content.",
        "bounded_crawl": "Crawl a bounded set of public pages.",
        "camera_capture": "Capture one explicitly requested camera frame inside the project.",
        "read_file": "Read a UTF-8 text file inside the project.",
        "browser_scroll": "Scroll the active browser tab through the connected extension.",
    }

    class _Registry:
        def get(self, name): return _Spec(tools[name])

    names = sorted(tools)
    registry = _Registry()

    web = _tools_for_ability(registry, names, "can you scrape websites?")
    assert web, "a web question must find the web tools"
    assert any("web" in name or "crawl" in name for name in web)
    assert "camera_capture" not in web

    camera = _tools_for_ability(registry, names, "can you see through the camera?")
    assert "camera_capture" in camera
    # "through" appears in the browser tool's description and must not pull it
    # into a camera question, which is what an unweighted match did.
    assert "browser_scroll" not in camera

    assert _tools_for_ability(registry, names, "what is the capital of France?") == []


def test_a_reply_disowning_a_real_capability_is_detected():
    """The exact replies Mohsin was given, while the tools sat registered.

    A capability *question* can be routed before the model sees it. A denial
    volunteered in the middle of an ordinary reply cannot, so it is caught
    afterwards -- the same treatment identity leaks already get.
    """
    from app.tools.introspection import denies_a_real_capability

    class _Spec:
        def __init__(self, description): self.description = description

    tools = {
        "public_web_fetch": "Safely retrieve and structurally extract one public page or PDF.",
        "capture_web_page": "Render one public webpage and extract its visible content.",
        "bounded_crawl": "Crawl a bounded set of public pages.",
        "web_search": "Search the public web through the free provider chain.",
    }

    class _Registry:
        def names(self): return sorted(tools)
        def get(self, name): return _Spec(tools[name])

    registry = _Registry()

    for denial in (
        "I cannot change my core operational limitations through conversation. My ability to "
        "\"scrape\" or continuously update my knowledge base outside of our direct chat is not "
        "something I can enable.",
        "I cannot autonomously browse the web or run background processes to learn in real-time.",
        "Regarding web scraping, I don't have a built-in tool to browse or scrape live websites.",
        "You will need to copy and paste that content into our chat for me to process it.",
        "No, Mohsin. I don't learn on my own when you aren't around. Nothing happens in the "
        "background without a prompt from you.",
    ):
        assert denies_a_real_capability(registry, denial), denial

    # A true limit is a true sentence and must survive untouched. HAYAT
    # refusing to spend money or to attack a stranger's machine is correct.
    for honest in (
        "I can't spend any money on that, so I won't sign up for the paid API.",
        "I cannot promote a fine-tuned adapter without your explicit approval.",
        "I cannot attack a machine you don't own.",
        "I don't learn your private files unless you hand them to me deliberately.",
        "I read the public web on my own, continuously, whether or not you're talking to me.",
        # No inbox or calendar tool exists: saying so, and asking him to paste the
        # email, is true. Treated as a denial it became "I can fetch your email".
        "I don't have a tool that reads your inbox, so I can't pull the latest email. "
        "You'll need to open your Email app or paste it here.",
        "I can't check your calendar. You'll need to open it yourself.",
    ):
        assert not denies_a_real_capability(registry, honest), honest
    # A real denial beside a true one is still caught.
    assert denies_a_real_capability(
        registry, "I don't have a tool that reads your inbox. Also, I cannot browse the web for you.")

    # With no such tools registered, the same sentence is simply true.
    class _Empty:
        def names(self): return []
        def get(self, name): raise KeyError(name)

    assert not denies_a_real_capability(
        _Empty(), "I cannot autonomously browse the web or run background processes.")
