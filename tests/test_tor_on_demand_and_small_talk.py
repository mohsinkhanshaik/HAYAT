"""Small talk stays conversation, and a remembered Tor route starts Tor instead of refusing every search.

Live 2026-10-25: "how are you today ?" became a web search for those words, and the
search failed with "WinError 10061 ... actively refused" from every provider. Tor was
remembered on, so HAYAT's web reading was routed to 127.0.0.1:9052, but background
autostart is off by default and nothing had started Tor on that port.
"""
from __future__ import annotations

import pytest

from app.intent.live_web import needs_live_web
from app.intent.models import IntentKind
from app.intent.router import IntentRouter
from app.privacy_masks import ROUTE, Route, TorMask
from app.session import SessionState


def final_intent(text):
    """The router's decision plus the assistant's live-web backstop (app/assistant.py)."""
    intent = IntentRouter().route(text, SessionState())
    if intent.intent in {IntentKind.CONVERSATION, IntentKind.QUESTION} and needs_live_web(text) is not None:
        return "LIVE_WEB"
    return intent.intent


@pytest.mark.parametrize("text", [
    "how are you today ?", "how are you today?", "How are you today", "how r u today ?", "hw r u today",
    "how are u today hayat", "how are you doing this morning?", "how's everything today?", "what's up today?",
    "how are things going today?", "are you doing well today?", "how are you feeling right now?",
    "how is your day today?", "good morning! how are you today?", "hey, how are you doing today ?",
    "how have you been lately?", "how's life today", "hope you are doing well today", "how are you today my friend",
    "hi hayat how is it going today", "how are you this evening ?", "you okay today?", "how do you feel today?",
    "are you alright today ?", "how was your night", "how are you holding up today?", "whats up?",
])
def test_small_talk_with_a_time_word_is_conversation_not_a_search(text):
    assert final_intent(text) == IntentKind.CONVERSATION


@pytest.mark.parametrize("text", [
    "what's the news today", "how is the weather today", "what is the bitcoin price today", "who won the match today",
    "what happened in the world today?", "how is the stock market doing today?", "any earthquakes today?",
    "how are the markets today?", "what's the weather like today in Toronto", "how are oil prices today?",
    "what's up with bitcoin today?",
])
def test_questions_about_the_world_today_still_reach_live_data(text):
    assert final_intent(text) not in {IntentKind.CONVERSATION, IntentKind.QUESTION}


class FakeTor:
    def __init__(self, log, *, bootstraps=True):
        self.log, self.bootstraps, self.code = log, bootstraps, None

    def __call__(self, *_args, **_kwargs):
        self.log.parent.mkdir(parents=True, exist_ok=True)
        if self.bootstraps:
            self.log.write_text("[notice] Bootstrapped 100% (done): Done\n", encoding="utf-8")
        else:
            self.code = 1  # exits at once, as a broken or blocked tor.exe does
        return self

    def poll(self):
        return self.code

    def terminate(self):
        self.code = 0

    def wait(self, timeout=None):
        return self.code


def tor_mask(tmp_path, launch, *, installed=True):
    folder = tmp_path / "tor"
    folder.mkdir()
    if installed:
        for name in ("tor.exe", "geoip", "geoip6"):
            (folder / name).write_bytes(b"x")
    route = Route()
    route.set("socks5://127.0.0.1:9052")  # remembered on at launch, before anything started
    mask = TorMask(folder=folder, state=tmp_path / "state", route=route, settings=tmp_path / "masks.json", launch=launch)
    route.set_starter(mask.ensure_started)
    return mask, route


def test_a_masked_read_starts_tor_on_demand_and_keeps_the_route(tmp_path):
    launches = []

    def launch(*args, **kwargs):
        launches.append(args)
        return fake(*args, **kwargs)

    fake = FakeTor(tmp_path / "state" / "tor.log")
    mask, route = tor_mask(tmp_path, launch)
    route.ensure_ready()
    assert mask.ready() and len(launches) == 1
    assert route.proxy == "socks5://127.0.0.1:9052"
    route.ensure_ready()  # already up: no second launch
    assert len(launches) == 1


def test_a_failed_start_fails_closed_with_a_plain_reason_and_pauses_retries(tmp_path):
    launches = []

    def launch(*args, **kwargs):
        launches.append(args)
        return fake(*args, **kwargs)

    fake = FakeTor(tmp_path / "state" / "tor.log", bootstraps=False)
    mask, route = tor_mask(tmp_path, launch)
    with pytest.raises(PermissionError) as first:
        route.ensure_ready()
    message = str(first.value)
    assert "Tor is on for HAYAT" in message and "Nothing was read directly" in message and "unmask" in message
    assert "10061" not in message
    assert route.proxy == "socks5://127.0.0.1:9052"  # never switched to the direct route
    with pytest.raises(PermissionError):
        route.ensure_ready()
    assert len(launches) == 1  # the second request did not wait out another start


def test_tor_not_installed_says_how_to_set_it_up(tmp_path):
    mask, route = tor_mask(tmp_path, FakeTor(tmp_path / "state" / "tor.log"), installed=False)
    with pytest.raises(PermissionError, match="set up tor"):
        route.ensure_ready()


def test_no_starter_or_no_mask_changes_nothing(tmp_path):
    route = Route()
    route.ensure_ready()  # no proxy, no starter
    route.set("socks5://127.0.0.1:9052")
    route.ensure_ready()  # no starter registered (tests, safe mode): the request proceeds as before
    calls = []
    route.set_starter(lambda: calls.append(1))
    route.set(None)
    route.ensure_ready()
    assert not calls


def test_the_public_fetcher_asks_the_route_to_start_tor_before_connecting(monkeypatch):
    from app.research.fetcher import PublicFetcher

    previous = ROUTE.snapshot()[0], ROUTE._starter
    calls = []

    def starter():
        calls.append(1)
        raise PermissionError("Tor is on for HAYAT's web reading but could not connect: test.")

    try:
        ROUTE.set("socks5://127.0.0.1:9052")
        ROUTE.set_starter(starter)
        fetcher = PublicFetcher("HAYAT-test/1.0", respect_robots=False)
        with pytest.raises(PermissionError, match="could not connect"):
            fetcher.fetch("https://example.com/")
        assert calls
    finally:
        ROUTE.set(previous[0])
        ROUTE.set_starter(previous[1])


def test_the_identity_prompt_has_no_sentence_fragments():
    """e3e68af cut the first line of the action boundary and left "specific person or system, ..." dangling."""
    import re

    from app.identity import Identity

    text = Identity().system_preamble()
    assert "What you will not do is narrow and real: help commit a crime against a specific person or system" in text
    # Every sentence starts with a capital letter, a digit or a quote: none begins mid-clause.
    starts = re.findall(r"(?:^|[.!?]\s+)([a-z])\w*", text)
    assert not starts, starts[:5]


def test_the_capability_list_reaches_the_model_as_droppable_context(tmp_path):
    """The area list grows with every feature; on a small window it is dropped, never the request or the rules."""
    from types import SimpleNamespace

    from app.agent.conversation import ConversationService
    from app.context_budget import ContextBudget
    from app.memory.database import MemoryDatabase
    from app.models.base import ModelResponse

    class Model:
        name, model = "test", "bounded"

        def __init__(self, window):
            self.context_length, self.calls = window, []

        def generate(self, messages, **kwargs):
            self.calls.append(list(messages))
            return ModelResponse("Fine, thanks.", self.model, self.name, finish_reason="stop")

    for window, kept in ((16384, True), (4096, None)):
        memory = MemoryDatabase(tmp_path / f"w{window}.db")
        memory.initialize()
        model = Model(window)
        chat = ConversationService(model, memory, "s", state_supplier=lambda: {})
        chat.budget = ContextBudget(SimpleNamespace(hardware=SimpleNamespace(), model=model))
        chat.reply("how are you today?", ["BALANCED"])
        sent = model.calls[0]
        listing = [m for m in sent if m.content.startswith("What you can do on this machine")]
        assert "Never say you cannot start" in sent[0].content  # the rules stay in the core instructions
        assert sent[-1].content == "how are you today?"
        if kept:
            assert listing and listing[0].optional_context
