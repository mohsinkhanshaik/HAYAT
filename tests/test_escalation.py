"""The larger model advises worthwhile DEEP turns; HAYAT writes the answer."""
from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from app.agent.conversation import ConversationService
from app.memory.database import MemoryDatabase
from app.models.base import ModelProvider, ModelResponse
from app.models.council import Council
from app.models.intelligent_router import IntelligentModelRouter, RouteDecision
from app.models.base import ModelError
from app.models.escalation import (DEFAULT_MAX_ADVICE_TOKENS, DEFAULT_NUM_CTX, DEFAULT_TIMEOUT_SECONDS,
                                   CappedProvider, Escalation, load_escalation, worth_escalating)
from app.modes import ExecutionMode, ModeManager


class Named(ModelProvider):
    def __init__(self, model: str, fail: bool = False) -> None:
        self.name, self.model, self.fail = "ollama", model, fail
        self.timeout, self.num_ctx, self.context_length = 120.0, 16384, 16384
        self.base_url, self.calls, self.max_tokens = "http://127.0.0.1:11435", 0, []
        self.prompts = []

    def list_models(self):
        return [self.model]

    def generate(self, messages, tools=None, temperature=None, response_schema=None, max_tokens=None):
        self.calls += 1
        self.prompts.append(list(messages))
        self.max_tokens.append(max_tokens)
        if self.fail:
            raise RuntimeError("model not loaded")
        return ModelResponse(f"Answer from {self.model} about the subject.", self.model, self.name)


class Router:
    def __init__(self, config):
        self.config = config

    def get(self, purpose):
        return Named(self.config[purpose]["model"])


def deep(big=None, **kwargs):
    return Escalation(big or Named("big:27b"), frozenset({"deep_mode"}), **kwargs)


def test_no_escalation_block_means_none():
    escalation = load_escalation(Router({}))
    assert escalation.provider is None and escalation.for_modes(["DEEP"]) is None


def test_the_configured_model_gets_its_own_timeout_window_and_cap():
    escalation = load_escalation(Router({"escalation_model": {
        "model": "big:27b", "use_for": ["deep_mode", "something_else"], "timeout_seconds": 900,
        "num_ctx": 4096, "max_advice_tokens": 800}}))
    assert escalation.model == "big:27b" and escalation.uses == frozenset({"deep_mode"})
    assert escalation.provider.timeout == 900
    assert escalation.provider.num_ctx == escalation.provider.context_length == 4096
    assert escalation.max_advice_tokens == 800
    default = load_escalation(Router({"escalation_model": {"model": "big:27b", "use_for": ["deep_mode"]}}))
    assert default.provider.timeout == DEFAULT_TIMEOUT_SECONDS
    assert default.num_ctx == DEFAULT_NUM_CTX and default.max_advice_tokens == DEFAULT_MAX_ADVICE_TOKENS


def test_the_advisor_thinks_only_when_configured_to():
    thinking = load_escalation(Router({"escalation_model": {"model": "big:27b", "use_for": ["deep_mode"],
                                                            "think": True}}))
    assert thinking.provider.think is True and thinking.status()["thinks"] is True
    plain = load_escalation(Router({"escalation_model": {"model": "big:27b", "use_for": ["deep_mode"]}}))
    assert plain.provider.think is False


def test_the_ollama_provider_sends_its_thinking_switch(monkeypatch):
    """HAYAT's replies never think (they must start fast); a provider told to think asks Ollama to."""
    import httpx

    from app.models.local import OllamaProvider

    sent = []

    class Answer:
        def raise_for_status(self):
            return None

        def json(self):
            return {"model": "m", "message": {"content": "advice", "thinking": "private"}, "done": True}

    def post(url, json, timeout, trust_env):
        assert trust_env is False
        sent.append(json)
        return Answer()
    monkeypatch.setattr(httpx, "post", post)
    provider = OllamaProvider("http://127.0.0.1:11435", model="m")
    assert provider.generate([{"role": "user", "content": "q"}]).content == "advice"
    provider.think = True
    assert provider.generate([{"role": "user", "content": "q"}]).content == "advice"
    assert [payload["think"] for payload in sent] == [False, True]


def test_only_the_deep_mode_uses_it():
    escalation = deep()
    assert escalation.for_modes(["DEEP"]) is escalation.provider
    for modes in (["BALANCED"], ["FAST"], ["ADVERSARIAL"], []):
        assert escalation.for_modes(modes) is None
    assert Escalation(Named("big:27b"), frozenset()).for_modes(["DEEP"]) is None


def test_pleasantries_stay_on_hayat_and_real_questions_escalate():
    for text in ("thanks", "Thank you so much!", "ok", "hi hayat", "good night", "", "  "):
        assert not worth_escalating(text), text
    for text in ("why?", "How does TCP congestion control work?", "compare these three database designs"):
        assert worth_escalating(text), text
    escalation = deep()
    assert escalation.advisor("thanks", ["DEEP"]) is None
    assert escalation.advisor("How does a B-tree split?", ["BALANCED"]) is None
    advisor = escalation.advisor("How does a B-tree split?", ["DEEP"])
    assert isinstance(advisor, CappedProvider)
    assert advisor.model == "big:27b" and advisor.cap == DEFAULT_MAX_ADVICE_TOKENS


def test_hayat_answers_when_the_larger_model_fails_and_the_breaker_is_shared():
    big = Named("big:27b", fail=True)
    failures, admitted = [], {"big:27b": True}
    escalation = deep(big, admission=lambda model: admitted.get(model, True),
                      on_failure=lambda model, *_: failures.append(model))
    council = Council(escalation)
    advisors = council.advisors("Explain the CAP theorem in depth", ["DEEP"], "reasoning")
    assert council.consult([], advisors) == []
    assert failures == ["big:27b"]
    # Once the breaker is open, the turn goes straight to HAYAT without advice.
    admitted["big:27b"] = False
    assert escalation.advisor("Explain the CAP theorem in depth", ["DEEP"]) is None


def test_specialist_failure_is_recorded_by_the_shared_breaker():
    failures = []
    council = Council(specialist=lambda capability, modes: Named("expert:latest", fail=True),
                      on_failure=lambda model, *_: failures.append(model))
    advisors = council.advisors("Explain the CAP theorem in depth", ["BALANCED"], "reasoning")
    assert council.consult([], advisors) == []
    assert failures == ["expert:latest"]


def test_answers_are_capped_so_they_finish_inside_the_timeout():
    big = Named("big:27b")
    capped = CappedProvider(big, 1200)
    capped.generate([])
    capped.generate([], max_tokens=4096)
    capped.generate([], max_tokens=300)
    assert big.max_tokens == [1200, 1200, 300]


def test_choosing_deep_preloads_with_the_same_window(tmp_path):
    posted, done = [], threading.Event()

    def poster(url, json, timeout):
        posted.append((url, json))
        done.set()

    escalation = deep(poster=poster)
    modes = ModeManager(path=tmp_path / "profile.json")
    modes.listeners.append(lambda chosen: escalation.warm(mode.value for mode in chosen))
    modes.set("balanced")
    assert not done.wait(0.2) and posted == []
    modes.set("deep")
    assert done.wait(5)
    url, payload = posted[0]
    assert url == "http://127.0.0.1:11435/api/generate"
    assert payload["model"] == "big:27b" and payload["options"]["num_ctx"] == escalation.provider.num_ctx


def test_the_preload_follows_a_resource_mode_resize(tmp_path):
    """Balanced mode resizes every model to 8K after startup; preloading at the configured 16K reloaded the
    shared foundation on each DEEP choice and again on the next chat turn."""
    posted, done = [], threading.Event()

    def poster(url, json, timeout):
        posted.append(json)
        done.set()

    escalation = deep(poster=poster)
    escalation.provider.num_ctx = escalation.provider.context_length = 8192
    assert escalation.warm(["DEEP"]) and done.wait(5)
    assert posted[0]["options"]["num_ctx"] == 8192


def test_a_failing_listener_never_blocks_a_mode_change(tmp_path):
    modes = ModeManager(path=tmp_path / "profile.json")
    modes.listeners.append(lambda chosen: 1 / 0)
    assert modes.set("deep") == [ExecutionMode.DEEP]


def test_a_deep_turn_gets_advice_then_hayat_answers_and_a_greeting_skips_advice(tmp_path):
    memory = MemoryDatabase(tmp_path / "conversation.db")
    memory.initialize()
    hayat, big = Named("hayat:latest"), Named("big:27b")
    chat = ConversationService(hayat, memory, "session-test", state_supplier=lambda: {})
    chat.council = Council(deep(big))
    assert "hayat:latest" in chat.reply("How does photosynthesis turn light into sugar?", ["DEEP"])
    assert big.calls >= 1
    assert any(message.role == "user" and "Second opinion from big:27b" in message.content
               for message in hayat.prompts[0])
    assert all("Second opinion from big:27b" not in message.content for message in big.prompts[0])
    before = big.calls
    assert "hayat:latest" in chat.reply("thanks", ["DEEP"])
    assert big.calls == before


def test_a_pinned_model_answers_that_turn_and_is_not_cached(tmp_path):
    memory = MemoryDatabase(tmp_path / "conversation.db")
    memory.initialize()
    hayat, big = Named("hayat:latest"), Named("big:27b")
    chat = ConversationService(hayat, memory, "session-test", state_supplier=lambda: {})
    question = "How does photosynthesis turn light into sugar?"
    assert "big:27b" in chat.reply(question, ["BALANCED"], model=big)
    assert "hayat:latest" in chat.reply(question, ["BALANCED"])
    # A short answer is followed up once, so each model is asked the same number of times.
    assert big.calls >= 1 and hayat.calls == big.calls


@pytest.mark.parametrize("primary", ["hayat:latest", "expert:latest"])
def test_only_hayat_can_write_even_when_an_expert_ranks_first(tmp_path, primary):
    router = IntelligentModelRouter(SimpleNamespace(model_inventory=lambda: {"models": []}),
                                    SimpleNamespace(providers={"ollama": Named("hayat:latest", fail=True)}),
                                    tmp_path / "routes.db")
    decision = RouteDecision("reasoning", primary, ["expert:latest"], False,
                             [{"model": "expert:latest", "quality": 1},
                              {"model": "hayat:latest", "quality": .8}], "test")
    route = router.hayat_first(decision)
    assert route.primary == "hayat:latest" and route.fallbacks == []
    writer = router.provider(route)
    assert writer.list_models() == ["hayat:latest"]
    with pytest.raises(ModelError):
        writer.generate([])


def test_missing_hayat_does_not_promote_an_advisor(tmp_path):
    router = IntelligentModelRouter(SimpleNamespace(model_inventory=lambda: {"models": []}),
                                    SimpleNamespace(providers={}), tmp_path / "routes.db")
    decision = RouteDecision("reasoning", "expert:latest", [], False,
                             [{"model": "expert:latest", "quality": 1}], "test")
    route = router.hayat_first(decision)
    assert route.primary is None and route.fallbacks == []
    assert router.provider(route) is None


def test_runtime_writer_lock_rejects_a_pinned_advisor(tmp_path):
    memory = MemoryDatabase(tmp_path / "conversation.db")
    memory.initialize()
    chat = ConversationService(Named("hayat:latest"), memory, "locked")
    chat.answer_model_name = "hayat:latest"
    with pytest.raises(ValueError, match="identity model"):
        chat.reply("Explain TCP congestion control", model=Named("expert:latest"))


def test_cancelled_advice_does_not_continue_into_an_answer():
    from app.models.streaming import GenerationCancelled

    advisor = Named("expert:latest")
    advisor.generate = lambda *a, **kw: (_ for _ in ()).throw(GenerationCancelled("cancelled"))
    with pytest.raises(GenerationCancelled):
        Council().consult([], [advisor])


def test_small_talk_never_waits_for_the_thinking_advisor():
    """"tell me a joke" in God mode waited 14 s for the DEEP advisor before HAYAT answered."""
    for text in ("tell me a joke", "lol you're funny", "how was your weekend?", "i'm tired today"):
        assert not worth_escalating(text), text
    assert worth_escalating("my laptop is slow and keeps freezing")


def test_truncated_advice_is_excluded_without_poisoning_provider_health():
    from unittest.mock import Mock
    advisor = Named('expert:latest')
    advisor.generate = lambda *a, **kw: ModelResponse('unfinished analysis', 'expert:latest', 'local', finish_reason='length')
    council = Council()
    council.on_success, council.on_failure = Mock(), Mock()
    assert council.consult([], [advisor]) == []
    council.on_success.assert_called_once_with('expert:latest')
    council.on_failure.assert_not_called()
