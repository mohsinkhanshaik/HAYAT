"""Tools matched by meaning with the local embedding model, beside the word-based shortlist."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.tools import relevance
from app.tools.relevance import SemanticTools, semantic_matches, use_embeddings

TOPICS = {"weather": ("windy", "rain", "weather", "forecast"), "install": ("install", "firefox", "program"),
          "aircraft": ("plane", "aircraft", "flying")}


class Embedder:
    """A tiny embedding model: one dimension per topic, set when a topic word appears."""
    model, available = "fake-minilm", True

    def __init__(self):
        self.calls = []

    def embed(self, texts):
        self.calls.append(list(texts))
        return [[1.0 if any(word in text.lower() for word in words) else 0.0 for words in TOPICS.values()] + [0.1]
                for text in texts]


class Registry:
    def __init__(self, tools):
        self.tools = tools

    def describe(self):
        return self.tools

    def names(self):
        return [tool["name"] for tool in self.tools]


TOOLS = [{"name": "intelligence_weather", "description": "Read model forecasts at a coordinate."},
         {"name": "install_software", "description": "Install a Windows program with winget."},
         {"name": "intelligence_aircraft", "description": "Aircraft positions near a place."}]


def test_the_closest_tools_by_meaning_and_vectors_are_kept(tmp_path):
    embedder = Embedder()
    tools = SemanticTools(embedder, tmp_path / "vectors.json")
    assert tools.prepare(TOOLS) == 6  # two views per tool: its name, and its name with the description
    assert tools.nearest("will it be windy in Chicago tomorrow", 2)[0] == "intelligence_weather"
    assert tools.prepare(TOOLS) == 0  # nothing new
    again = SemanticTools(Embedder(), tmp_path / "vectors.json")
    assert again.prepare(TOOLS) == 0  # the cache file spared embedding them again
    changed = [*TOOLS[:2], {"name": "intelligence_aircraft", "description": "Planes over a city."}]
    assert again.prepare(changed) == 1  # only the changed description


def test_without_an_embedding_model_or_when_it_fails_words_work_alone(tmp_path):
    registry = Registry(TOOLS)
    use_embeddings(None)
    assert semantic_matches(registry, "windy tomorrow") == []

    class Broken(Embedder):
        def embed(self, texts):
            raise ConnectionError("Ollama stopped")
    use_embeddings(Broken(), tmp_path / "v.json")
    try:
        assert semantic_matches(registry, "windy tomorrow") == []
    finally:
        use_embeddings(None)


def test_only_registered_tools_and_close_enough_ones_are_suggested(tmp_path):
    registry = Registry(TOOLS)
    use_embeddings(Embedder(), tmp_path / "v.json")
    try:
        assert semantic_matches(registry, "put firefox on this computer")[0] == "install_software"
        assert relevance._semantic["tools"].nearest("", 3) == []
    finally:
        use_embeddings(None)


def test_the_planner_keeps_meaning_matches_past_the_family_cap(monkeypatch):
    from app.agent.planner import Planner

    names = ["intelligence_aircraft", "intelligence_alerts", "intelligence_around", "intelligence_weather", "my_capabilities"]
    registry = SimpleNamespace(names=lambda: names, describe=lambda: [{"name": name, "description": name} for name in names],
                               get=lambda name: SimpleNamespace(name=name))
    planner = Planner.__new__(Planner)
    planner.registry = registry

    class Index:
        def scores(self, goal):
            return {}

        def relevant(self, goal):
            return ["intelligence_aircraft", "intelligence_alerts", "intelligence_around"]
    monkeypatch.setattr("app.tools.relevance.index_for", lambda registry: Index())
    monkeypatch.setattr("app.tools.relevance.semantic_matches", lambda registry, goal: ["intelligence_weather"])
    shortlist = planner._candidate_tools("will it be windy in Chicago tomorrow")
    assert "intelligence_weather" in shortlist and "intelligence_around" not in shortlist  # the cap still holds for words


def test_changed_model_version_rebuilds_vectors_even_with_the_same_name(tmp_path):
    embedder = Embedder()
    tools = SemanticTools(embedder, tmp_path / "vectors.json")
    tools.prepare(TOOLS)
    embedder.version = "new weights"
    assert tools.prepare(TOOLS) == 6


@pytest.mark.parametrize("broken", [[], [[float("nan")]], [[True]], [[0.0]], [[1.0], [1.0]]])
def test_invalid_batch_embeddings_do_not_publish_a_partial_index(broken):
    class Invalid(Embedder):
        def embed(self, texts):
            return broken
    tools = SemanticTools(Invalid())
    with pytest.raises(ValueError):
        tools.prepare(TOOLS)
    assert tools._vectors == {}


def test_query_dimension_change_cannot_silently_compare_partial_vectors():
    embedder = Embedder()
    tools = SemanticTools(embedder)
    tools.prepare(TOOLS)
    embedder.embed = lambda texts: [[1.0, 0.0]]
    with pytest.raises(ValueError, match="dimensions differ"):
        tools.nearest("windy")


def test_corrupt_cache_structure_is_rebuilt(tmp_path):
    path = tmp_path / "vectors.json"
    path.write_text('[1,2,3]', encoding="utf-8")
    assert SemanticTools(Embedder(), path).prepare(TOOLS) == 6


def test_background_warmup_does_not_block_tool_selection():
    from threading import Event, Thread
    entered, release = Event(), Event()
    class Slow(Embedder):
        def embed(self, texts):
            entered.set()
            assert release.wait(3)
            return super().embed(texts)
    tools = use_embeddings(Slow())
    thread = Thread(target=lambda: tools.prepare(TOOLS))
    thread.start()
    try:
        assert entered.wait(1)
        assert semantic_matches(Registry(TOOLS), "windy") == []
    finally:
        release.set()
        thread.join(3)
        use_embeddings(None)
    assert not thread.is_alive()
