from app.agent.reflection import Reflector
from app.memory.state_policy import eligible_conversation_context
from app.learning.continuous import ContinuousLearner
from app.agent.conversation import recall_query


def test_indexed_recall_does_not_bypass_state_and_evidence_policy():
    stale_screen = {
        "object_type": "memory:episodic", "content": "Yesterday's screenshot showed a document",
        "relevance": 0.99,
    }
    unsupported_claim = {
        "object_type": "research_claim", "content": "An unsupported fact", "relevance": 0.98,
    }
    stored_answer = {
        "object_type": "knowledge_summary", "content": "A cached answer", "relevance": 0.97,
    }
    project_note = {
        "object_type": "project_document", "title": "architecture.md",
        "content": "The project uses Python", "relevance": 0.8,
    }
    assert not eligible_conversation_context(stale_screen, session_id="new")
    assert not eligible_conversation_context(unsupported_claim, session_id="new")
    assert not eligible_conversation_context(stored_answer, session_id="new")
    assert eligible_conversation_context(project_note, session_id="new")
    same_session_screen = {"memory_type": "episodic", "content": "Screen observation",
                           "state_class": "TRANSIENT", "session_id": "new"}
    assert eligible_conversation_context(same_session_screen, session_id="new")
    assert not eligible_conversation_context(same_session_screen, session_id="later")


def test_followup_recall_keeps_the_last_explicit_subject():
    history = [
        {"role": "user", "content": "Explain Python memory management"},
        {"role": "assistant", "content": "Python uses reference counting"},
        {"role": "user", "content": "How does it affect performance?"},
        {"role": "assistant", "content": "There is overhead"},
        {"role": "user", "content": "And why is that?"},
    ]
    assert recall_query("And why is that?", history).startswith("Explain Python memory management")
    assert recall_query("What is a database index?", history) == "What is a database index?"
    assert recall_query("Why is that?", [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi"},
        {"role": "user", "content": "Why is that?"},
    ]) == "Why is that?"


def test_reflection_records_actual_tool_failure_and_expected_check():
    saved = []

    class Memory:
        def store_memory(self, *args, **kwargs):
            saved.append((args, kwargs))

    history = [{
        "tool": "read_file", "arguments": {"path": "missing.txt"},
        "expected": {"type": "tool_success"},
        "result": {"success": False, "error": "FileNotFoundError: missing.txt",
                   "permission": "ALLOW", "output": None},
        "verification": {"passed": False, "reason": "tool failed"},
    }]
    result = Reflector(Memory()).reflect("task-1", "Read missing.txt", history, False)
    assert result["what_failed"] == ["FileNotFoundError: missing.txt"]
    assert result["expected_vs_actual"][0]["expected"] == {"type": "tool_success"}
    assert "FileNotFoundError" in result["next_time"]
    assert saved[0][0][-1]["failed_tool"] == "read_file"


def test_runtime_conversation_filters_unproven_index_hits(monkeypatch):
    from app.runtime import build_runtime

    runtime = build_runtime(use_model=False, start_background_services=False)
    try:
        monkeypatch.setattr(runtime.memory, "retrieve", lambda *_args, **_kwargs: [])
        search_limits = []

        def indexed_search(_query, *, limit):
            search_limits.append(limit)
            return [
            {"object_type": "memory:episodic", "content": "A camera observation yesterday",
             "relevance": 0.99},
            {"object_type": "research_claim", "content": "A claim without citations",
             "relevance": 0.95},
            {"object_type": "project_document", "title": "architecture.md",
             "content": "The project uses Python", "relevance": 0.8},
            ][:limit]

        monkeypatch.setattr(runtime.retrieval, "search", indexed_search)
        recalled = runtime.conversation.context_retriever("What does the project use?", limit=1, session_id="new")
        assert [item["object_type"] for item in recalled] == ["project_document"]
        assert search_limits == [4]
    finally:
        runtime.close()


def test_background_learning_does_not_mark_disputed_only_research_as_learned(tmp_path):
    class Registry:
        def execute(self, _name, _arguments):
            return type("Result", (), {"success": True, "error": "", "output": {
                "complete": True, "answer": "Two sources disagree", "sources": [{"url": "https://example.com"}],
                "citations": ["https://example.com"],
                "metrics": {"claims_supported": 0, "claims_disputed": 1},
            }})()

    runtime = type("Runtime", (), {"registry": Registry()})()
    learner = ContinuousLearner(runtime=runtime, root=tmp_path)
    result = learner._default_cycle("distributed systems consistency models")
    assert not result.ok
    assert "No supported cited claim" in result.detail
