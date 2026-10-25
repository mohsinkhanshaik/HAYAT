"""HAYAT's reverse-engineering integration (REA): routing, read/write governance, reports, and a real run."""
import json
from pathlib import Path

import pytest

from app import reverse_engineer as re_mod
from app.reverse_engineer import (ReverseEngineer, direct_plan, is_write_tool, register_reverse_engineer_tools,
                                  report, reverse_engineer_request)


# ---- a fake REA session so unit tests never spawn Node -----------------------
class FakeSession:
    def __init__(self):
        self.calls = []
        self.running = True
        self.closed = False

    def list_tools(self, timeout=30.0):
        return [{"name": name, "description": desc, "inputSchema": {}} for name, desc in (
            ("binary_overview", "Overview."), ("open_binary", "Open a target."),
            ("set_comment", "Write a note."), ("procedure_pseudo_code", "Decompile."))]

    def call(self, tool, arguments=None, timeout=180.0):
        self.calls.append((tool, dict(arguments or {})))
        if tool == "close_binary":
            return {"content": [{"type": "text", "text": "closed"}]}
        if tool == "boom":
            return {"isError": True, "content": [{"type": "text", "text": "REA says no"}]}
        return {"content": [{"type": "text", "text": f"ran {tool}"}], "structuredContent": {"tool": tool}}

    def close(self):
        self.closed = True
        self.running = False


@pytest.fixture
def engine():
    return ReverseEngineer(FakeSession())


def test_a_link_is_downloaded_locally_then_opened():
    from app.reverse_engineer import is_url
    assert is_url("https://x.com/a.js") and not is_url("/tmp/x") and not is_url(r"C:\a.exe")
    session = FakeSession()
    got = {}

    def fetch(url, max_bytes):
        got["url"], got["max"] = url, max_bytes
        return b"MZ\x90\x00 fake bytes"
    engine = ReverseEngineer(session, fetch=fetch)
    result = engine.act("open_binary", {"path": "https://code.jquery.com/a/jquery.min.js"})
    # REA was handed a LOCAL file under HAYAT's folder, never the URL.
    opened = session.calls[-1][1]["path"]
    assert "rea-home" in opened.replace("\\", "/") and not opened.startswith("http")
    # The owner still sees the link they gave, and the fetch was size-bounded.
    assert result["source_url"] == "https://code.jquery.com/a/jquery.min.js"
    assert result["open_target"] == "https://code.jquery.com/a/jquery.min.js"
    assert engine.status()["open_target"] == "https://code.jquery.com/a/jquery.min.js"
    assert got["url"] == "https://code.jquery.com/a/jquery.min.js" and got["max"] >= 1024 * 1024


def test_a_link_that_returns_nothing_fails_cleanly():
    from app.reverse_engineer import ReaError
    engine = ReverseEngineer(FakeSession(), fetch=lambda url, max_bytes: b"")
    with pytest.raises(ReaError, match="download"):
        engine.act("open_binary", {"path": "https://x.com/missing.js"})


@pytest.mark.parametrize("text, path", [
    ("reverse engineer https://code.jquery.com/jquery-3.7.1.min.js", "https://code.jquery.com/jquery-3.7.1.min.js"),
    ("decompile https://x.com/app.wasm", "https://x.com/app.wasm"),
    ("what's inside https://y.io/bundle.js", "https://y.io/bundle.js")])
def test_a_link_routes_to_open(text, path):
    found = reverse_engineer_request(text)
    if path.endswith('.wasm'):
        assert found["tool"] == "reverse_engineer_act"
        assert found["arguments"] == {"tool": "open_binary", "arguments": {"path": path}}
    else:
        assert found == {'tool': 'reverse_engineer_link', 'arguments': {'url': path}}


@pytest.mark.parametrize("name, writes", [
    ("open_binary", True), ("close_binary", True), ("extract_artifact", True), ("set_comment", True),
    ("set_address_name", True), ("goto_address", True), ("capture_process_scenario", True),
    ("execute_node_characterization", True), ("import_evidence_bundle", True), ("export_evidence_bundle", True),
    ("run_replay_machine", True), ("observe_web_session", True),
    ("binary_overview", False), ("list_strings", False), ("procedure_pseudo_code", False),
    ("search_procedures", False), ("trace_feature", False), ("xrefs", False),
    ("analyze_javascript_application", False), ("inspect_artifact", False), ("compare_functions", False)])
def test_read_write_classification(name, writes):
    assert is_write_tool(name) is writes


def test_a_future_write_tool_is_gated_by_prefix():
    assert is_write_tool("set_something_new") and is_write_tool("capture_new_thing")
    assert not is_write_tool("inspect_new_thing")


def test_read_refuses_a_write_tool(engine):
    with pytest.raises(PermissionError):
        engine.read("open_binary", {"path": "/x"})
    with pytest.raises(PermissionError):
        engine.read("set_comment", {})


def test_act_refuses_a_read_tool(engine):
    with pytest.raises(ValueError):
        engine.act("binary_overview", {})


def test_act_open_is_remembered_for_later_reads(engine):
    engine.act("open_binary", {"path": "/apps/thing"})
    assert engine.status()["open_target"] == "/apps/thing"
    reading = engine.read("binary_overview", {})
    assert reading["ok"] and reading["open_target"] == "/apps/thing"
    engine.act("close_binary", {})
    assert engine.status()["open_target"] is None


def test_non_json_arguments_are_refused_before_reaching_rea():
    from app.reverse_engineer import ReaError
    session = FakeSession()
    engine = ReverseEngineer(session)
    for bad in ({"fn": object()}, {"n": float("nan")}, {"big": "x" * 70_000}):
        with pytest.raises(ReaError):
            engine.read("search_strings", bad)
    assert session.calls == [], "a bad-argument call must never reach the REA process"


def test_a_transport_error_becomes_a_plain_rea_error():
    from app.reverse_engineer import ReaError

    class Broken(FakeSession):
        def call(self, tool, arguments=None, timeout=180.0):
            raise RuntimeError("stdio pipe closed")
    engine = ReverseEngineer(Broken())
    with pytest.raises(ReaError, match="could not run"):
        engine.read("binary_overview", {})


def test_an_oversized_result_is_refused_not_streamed_into_chat():
    from app.reverse_engineer import ReaError

    class Huge(FakeSession):
        def call(self, tool, arguments=None, timeout=180.0):
            return {"content": [{"type": "text", "text": "A" * (9 * 1024 * 1024)}]}
    engine = ReverseEngineer(Huge())
    with pytest.raises(ReaError, match="MiB"):
        engine.read("list_strings", {})


def test_credentials_embedded_in_a_result_are_redacted():
    token = "gh" + "p_" + "abcdefghijklmnopqrstuvwxyz0123456789"  # fake; assembled so secret scanners skip it

    class Leaky(FakeSession):
        def call(self, tool, arguments=None, timeout=180.0):
            return {"content": [{"type": "text", "text": f"found {token} and main()"}]}
    result = ReverseEngineer(Leaky()).read("search_strings", {"query": "ghp"})
    assert token not in result["report"]
    assert "REDACTED" in result["report"] and "main()" in result["report"]


def test_errors_from_rea_are_raised_plainly(engine):
    from app.reverse_engineer import ReaError
    with pytest.raises(ReaError, match="REA says no"):
        engine.read("boom", {})


def test_tools_listing_marks_writes(engine):
    listing = engine.tools()
    writes = {row["name"]: row["writes"] for row in listing["tools"]}
    assert writes == {"binary_overview": False, "open_binary": True, "set_comment": True,
                      "procedure_pseudo_code": False}


def test_sdk_snake_case_schemas_structured_output_and_errors_are_preserved():
    class Session(FakeSession):
        def list_tools(self, timeout=30):
            return [{'name': 'inspect', 'input_schema': {'type': 'object', 'required': ['path']}}]

        def call(self, tool, arguments=None, timeout=180):
            return {'is_error': tool == 'boom', 'structured_content': {'evidence': 1},
                    'content': [{'type': 'text', 'text': 'failed' if tool == 'boom' else 'evidence'}]}
    engine = ReverseEngineer(Session())
    assert engine.tools()['tools'][0]['input_schema']['required'] == ['path']
    assert engine.read('inspect')['structured'] == {'evidence': 1}
    with pytest.raises(re_mod.ReaError, match='failed'):
        engine.read('boom')


def test_shortcut_maps_to_a_read_tool(engine):
    assert engine.shortcut("strings")["tool"] == "list_strings"
    with pytest.raises(ValueError):
        engine.shortcut("not-a-thing")


@pytest.mark.parametrize("text, tool, inner", [
    (r"reverse engineer D:\apps\thing.exe", "reverse_engineer_act", "open_binary"),
    ("decompile /opt/app/bin", "reverse_engineer_act", "open_binary"),
    ("what is inside app.asar", "reverse_engineer_act", "open_binary"),
    ("list the functions", "reverse_engineer_overview", None),
    ("show me the strings", "reverse_engineer_overview", None),
    ("decompile main", "reverse_engineer_read", "procedure_pseudo_code"),
    ("disassemble sub_401000", "reverse_engineer_read", "procedure_assembly"),
    ("reverse engineering tools", "reverse_engineer_tools", None),
    ("reverse engineer status", "reverse_engineer_status", None)])
def test_routing(text, tool, inner):
    found = reverse_engineer_request(text)
    assert found["tool"] == tool
    if inner:
        assert found["arguments"]["tool"] == inner


@pytest.mark.parametrize("text", ["hello there", "analyze the situation", "inspect my feelings", "list my groceries",
                                  "show me the news", "decompile the login function and email it to bob"])
def test_routing_leaves_other_requests_alone(text):
    assert reverse_engineer_request(text) is None


def test_direct_plan_only_when_tool_is_offered():
    plan = direct_plan(r"reverse engineer D:\a\b.exe", re_mod.TOOL_NAMES)
    assert plan["tool"] == "reverse_engineer_act"
    assert direct_plan(r"reverse engineer D:\a\b.exe", ()) is None


class FakeGate:
    def __init__(self):
        self.proposed = []
        self._id = 0

    def pending(self):
        return [p for p in self.proposed]

    def propose(self, kind, title, target, details, executor, *, notes=None, fingerprint_value="", **kw):
        self._id += 1
        self.executor = executor
        summary = {"id": self._id, "kind": kind, "title": title, "target": target,
                   "details": [{"field": n, "value": v} for n, v in details],
                   "how_to_confirm": "Confirm in the window."}
        self.proposed.append(summary)
        return summary


def test_registration_gates_reads_and_writes():
    seen = {}

    class Reg:
        def register(self, name, desc, schema, risk, permission, handler):
            seen[name] = permission
    register_reverse_engineer_tools(Reg(), ReverseEngineer(FakeSession()), gate=FakeGate())
    assert seen["reverse_engineer_read"] == "mcp_read"
    assert seen["reverse_engineer_act"] == "mcp_write"
    assert seen["reverse_engineer_overview"] == "mcp_read"
    assert seen["reverse_engineer_status"] == "system_read"


def test_act_proposes_to_the_gate_and_runs_only_on_confirm():
    from app.reverse_engineer import ReverseEngineerTools
    session = FakeSession()
    engine = ReverseEngineer(session)
    gate = FakeGate()
    tools = ReverseEngineerTools(engine, gate)
    summary = tools.prepare("open_binary", {"path": "/apps/thing"})
    assert summary["kind"] == "reverse_engineer" and summary["how_to_confirm"]
    assert session.calls == [], "nothing may run before the owner confirms"
    # Asking twice does not queue a second identical action.
    tools.prepare("open_binary", {"path": "/apps/thing"})
    assert len(gate.pending()) == 1
    gate.executor({})  # the owner confirms
    assert ("open_binary", {"path": "/apps/thing"}) in session.calls
    with pytest.raises(ValueError):
        tools.prepare("binary_overview", {})  # a read is not a gated action


def test_report_renders_proposal_read_and_status():
    proposal = [{"tool": "reverse_engineer_act",
                 "result": {"success": True, "output": {"kind": "reverse_engineer", "title": "Open a reverse-engineering target",
                                                        "how_to_confirm": "Confirm in the window."}},
                 "verification": {"passed": True}}]
    assert "Prepared" in report(proposal) and "Confirm in the window." in report(proposal)
    reading = [{"tool": "reverse_engineer_read",
                "result": {"success": True, "output": {"reverse_engineer": True, "report": "graph: 3 nodes",
                                                       "open_target": "/a/b"}},
                "verification": {"passed": True}}]
    assert "3 nodes" in report(reading)
    status = [{"tool": "reverse_engineer_status", "result": {"success": True, "output": {
        "reverse_engineer": True, "installed": True, "version": "3.2.1", "open_target": None}},
        "verification": {"passed": True}}]
    assert "installed" in report(status) and "3.2.1" in report(status)


def test_close_is_best_effort():
    session = FakeSession()
    engine = ReverseEngineer(session)
    engine.act("open_binary", {"path": "/x"})
    engine.close()
    assert session.closed and ("close_binary", {}) in session.calls


def test_simultaneous_analysis_cannot_change_the_active_target():
    from concurrent.futures import ThreadPoolExecutor
    import threading
    entered, release = threading.Event(), threading.Event()
    class SlowSession(FakeSession):
        def call(self, tool, arguments=None, timeout=180):
            if tool == 'binary_overview':
                entered.set()
                release.wait(3)
            return super().call(tool, arguments, timeout)
    session = SlowSession()
    engine = ReverseEngineer(session)
    with ThreadPoolExecutor(1) as pool:
        pending = pool.submit(engine.read, 'binary_overview')
        try:
            assert entered.wait(1)
            with pytest.raises(re_mod.ReaError, match='busy'):
                engine.act('open_binary', {'path': 'different.exe'})
            assert not session.calls and engine.status()['open_target'] is None
        finally:
            release.set()
        assert pending.result(3)['ok']
    engine.close()


def test_shutdown_closes_a_session_even_if_it_never_became_ready():
    session = FakeSession()
    session.running = False
    engine = ReverseEngineer(session)
    engine._session_now()
    engine.close()
    assert session.closed
    with pytest.raises(re_mod.ReaError, match='shutting down'):
        engine.read('binary_overview')


def test_a_failed_session_start_can_be_retried_after_the_worker_exits():
    class RetrySession(re_mod.ReaSession):
        attempts = 0
        def _run(self):
            self.attempts += 1
            if self.attempts == 1:
                self._start_error = RuntimeError('temporary startup failure')
                self._ready.set()
            else:
                self._ready.set()
                self._stop.wait(3)
    session = RetrySession(node=Path(__file__), script=Path(__file__))
    try:
        with pytest.raises(re_mod.ReaError, match='temporary startup failure'):
            session.start()
        session._thread.join(1)
        session.start()
        assert session.attempts == 2
    finally:
        session.close()


def test_concurrent_callers_both_wait_for_session_initialization():
    from concurrent.futures import ThreadPoolExecutor
    import threading
    import time
    started, release = threading.Event(), threading.Event()
    class SlowStart(re_mod.ReaSession):
        def _run(self):
            started.set()
            release.wait(3)
            self._ready.set()
            self._stop.wait(3)
    session = SlowStart(node=Path(__file__), script=Path(__file__))
    try:
        with ThreadPoolExecutor(2) as pool:
            first = pool.submit(session.start)
            assert started.wait(1)
            second = pool.submit(session.start)
            time.sleep(.03)
            assert not second.done(), 'a second caller must not use a half-initialized session'
            release.set()
            first.result(2)
            second.result(2)
    finally:
        release.set()
        session.close()


def test_setup_lock_matches_the_engine_pin():
    from app.config import PROJECT_ROOT
    manifest = json.loads((PROJECT_ROOT / 'integrations/rea/package.json').read_text())
    lock = json.loads((PROJECT_ROOT / 'integrations/rea/package-lock.json').read_text())
    assert manifest['dependencies']['rea-agents'] == re_mod.REA_VERSION
    pinned = lock['packages']['node_modules/rea-agents']
    assert pinned['version'] == re_mod.REA_VERSION and pinned['integrity'].startswith('sha512-')
    assert pinned['resolved'].startswith('https://registry.npmjs.org/rea-agents/')


def test_chat_status_flows_through_router_registry_and_report_without_a_model():
    from app.runtime import build_runtime
    runtime = build_runtime(use_model=False, start_background_services=False)
    try:
        reply = runtime.assistant.handle('reverse engineer status')
        assert 'Reverse-engineering engine:' in reply.response.display_text
        assert re_mod.REA_VERSION in reply.response.display_text
        assert not runtime.reverse_engineer.status()['running']
    finally:
        runtime.close()


# ---- a real REA run, only where Node and REA are installed -------------------
pytestmark_real = pytest.mark.skipif(not re_mod.available(), reason="REA is not installed in runtime/rea")


@pytestmark_real
def test_real_rea_analyzes_a_javascript_app(tmp_path):
    from app.reverse_engineer import ReaSession
    from app.safety.sandbox import ProcessSandbox

    app = tmp_path / "sample_app"
    app.mkdir()
    (app / "package.json").write_text(json.dumps({"name": "sample", "version": "1.0.0", "main": "index.js"}))
    (app / "index.js").write_text("function validateLicense(k){return k+'SKU-7731';}\nmodule.exports={validateLicense};\n")
    engine = ReverseEngineer(lambda: ReaSession(env=ProcessSandbox().environment()))
    try:
        assert engine.status()["installed"] is True
        result = engine.read("analyze_javascript_application", {"input_path": str(app), "format": "directory"})
        assert result["ok"]
        assert "JavaScriptApplicationGraph" in result["report"] or "graph" in result["report"].lower()
        listing = engine.tools()
        assert listing["count"] > 50 and any(row["name"] == "open_binary" and row["writes"] for row in listing["tools"])
        js = next(row for row in listing['tools'] if row['name'] == 'analyze_javascript_application')
        assert js['input_schema']['required'] == ['input_path']
        with pytest.raises(re_mod.ReaError):
            engine.read('analyze_javascript_application', {'input_path': str(app / 'absent')})
    finally:
        engine.close()


@pytestmark_real
def test_real_read_still_refuses_a_write_tool():
    from app.reverse_engineer import ReaSession
    from app.safety.sandbox import ProcessSandbox
    engine = ReverseEngineer(lambda: ReaSession(env=ProcessSandbox().environment()))
    try:
        with pytest.raises(PermissionError):
            engine.read("open_binary", {"path": str(Path(__file__))})
    finally:
        engine.close()
