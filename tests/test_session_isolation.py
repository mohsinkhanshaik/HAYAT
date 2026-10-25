from __future__ import annotations

from app.agent.conversation import ConversationService
from app.memory.database import MemoryDatabase
from app.models.base import ModelProvider, ModelResponse


class CapturingModel(ModelProvider):
    """Records the system prompt so we can assert what the model was told."""

    name = "capture"

    def __init__(self) -> None:
        self.system_prompt = ""

    def list_models(self):
        return ["capture"]

    def generate(self, messages, tools=None, temperature=None, response_schema=None, max_tokens=None):
        self.system_prompt = messages[0].content
        self.all_content = "\n".join(message.content for message in messages)
        return ModelResponse("ok", "capture", self.name)


def _database(tmp_path) -> MemoryDatabase:
    memory = MemoryDatabase(tmp_path / "memory.db")
    memory.initialize()
    return memory


def test_memories_record_the_session_that_produced_them(tmp_path):
    memory = _database(tmp_path)
    memory.store_memory("episodic", "Took a screenshot showing Codespaces.", 0.8, "task", session_id="session-old")
    # A screen snapshot is transient, so reaching it from another session now
    # requires explicitly asking for history rather than happening by default.
    row = next(
        item
        for item in memory.retrieve("screenshot", limit=5, include_foreign_transient=True)
        if "Codespaces" in item["content"]
    )
    assert row["session_id"] == "session-old"


def test_a_new_session_cannot_retrieve_prior_transient_observations_at_all(tmp_path):
    """Upgraded from labelling to exclusion.

    This test previously asserted the weaker guarantee: the observation was
    still retrieved and merely tagged "[from an EARLIER session]", leaving the
    model free to misuse it. The retrieval policy now withholds another
    session's transient snapshots outright, so the stronger property is the
    one worth pinning -- the content never reaches the prompt.
    """
    memory = _database(tmp_path)
    memory.store_memory("episodic", "Took a screenshot showing Codespaces.", 0.9, "task", session_id="session-old")

    model = CapturingModel()
    chat = ConversationService(model, memory, "session-new", state_supplier=lambda: {})
    chat.reply("what was on the screenshot")

    assert "Codespaces" not in model.all_content


def test_same_session_memories_are_marked_as_current(tmp_path):
    memory = _database(tmp_path)
    memory.store_memory("episodic", "Opened Notepad for the user.", 0.9, "task", session_id="session-live")

    model = CapturingModel()
    chat = ConversationService(model, memory, "session-live", state_supplier=lambda: {})
    chat.reply("did you open Notepad")

    assert "Notepad" not in model.system_prompt
    assert "Notepad" in model.all_content
    assert "[from this session" in model.all_content


def test_legacy_memories_without_a_session_are_treated_as_earlier(tmp_path):
    """Rows written before session provenance existed have session_id NULL.
    Defaulting those to 'current' would recreate the bug for the entire
    existing database, so they must read as history."""
    memory = _database(tmp_path)
    memory.store_memory("episodic", "Legacy observation with no session recorded.", 0.9, "task")

    model = CapturingModel()
    chat = ConversationService(model, memory, "session-new", state_supplier=lambda: {})
    chat.reply("legacy observation")

    assert "Legacy observation" not in model.system_prompt
    assert "Legacy observation" in model.all_content
    assert "[from an EARLIER session" in model.all_content
