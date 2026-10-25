"""HAYAT's identity, and noticing when something else is watching Mohsin.

The surveillance tests are mostly about signal quality. A security report that
cries wolf is worse than no report: the first version of this check produced
36 warnings on a healthy machine, all of them normal Windows services, which
is exactly how the one finding that matters gets lost in a list the reader has
learned to skip.
"""
from __future__ import annotations

import pytest

from app.identity import IDENTITY
from app.safety import watch


# ------------------------------------------------------------------ identity


#: The owner this copy is locked to, as written in app/identity.py. Empty means "this Windows account".
#: Changing the owner is a deliberate edit to both files; a quiet change anywhere else fails this test.
EXPECTED_OWNER = ("", "")


def test_the_assistant_knows_its_own_name_and_whose_it_is():
    from app.identity import RESOLVED_OWNER, RESOLVED_OWNER_SHORT

    assert IDENTITY.name == "HAYAT"
    assert IDENTITY.owner == (EXPECTED_OWNER[0] or RESOLVED_OWNER)
    assert IDENTITY.owner_short == (EXPECTED_OWNER[1] or RESOLVED_OWNER_SHORT)
    assert IDENTITY.owner and IDENTITY.owner_short


def test_the_owner_is_locked_and_cannot_be_quietly_changed():
    import dataclasses

    import pytest

    from app.identity import OWNER_NAME, OWNER_SHORT, RESOLVED_OWNER, OwnerTampered, verify_owner
    # The owner is one value, stated in code and echoed by the live identity.
    assert (OWNER_NAME, OWNER_SHORT) == EXPECTED_OWNER
    assert IDENTITY.owner == RESOLVED_OWNER
    assert IDENTITY.to_dict()["owner"] == RESOLVED_OWNER
    # The identity is frozen: nothing can reassign the owner on the running instance.
    with pytest.raises(dataclasses.FrozenInstanceError):
        IDENTITY.owner = "someone else"  # type: ignore[misc]
    # The startup guard passes for the real owner and rejects any divergence.
    assert verify_owner() == RESOLVED_OWNER
    from app.identity import Identity
    with pytest.raises(OwnerTampered):
        _check_against(Identity(owner="Someone Else"))


def _check_against(identity):
    # Mirror verify_owner's check against a supplied identity, to prove a changed owner is caught.
    from app.identity import RESOLVED_OWNER, RESOLVED_OWNER_SHORT, OwnerTampered
    if identity.owner != RESOLVED_OWNER or identity.owner_short != RESOLVED_OWNER_SHORT:
        raise OwnerTampered(identity.owner)


def test_the_creator_credit_is_fixed_whoever_owns_the_copy():
    import pytest

    from app.identity import CREATOR, PROJECT_URL, Identity, OwnerTampered, pronouns, verify_owner

    assert CREATOR == "Mohsin Khan Shaik" and IDENTITY.creator == CREATOR
    assert PROJECT_URL == "https://github.com/mohsinkhanshaik/HAYAT"
    assert "created by Mohsin Khan Shaik" in IDENTITY.system_preamble()
    someone = Identity(owner="Alex Doe", owner_short="Alex", owner_pronoun="they")
    assert "created by Mohsin Khan Shaik" in someone.introduction() and PROJECT_URL in someone.introduction()
    text = someone.system_preamble()
    assert "Address them as Alex" in text and "They are an adult on their own machine" in text
    assert " he " not in text and " his " not in text
    assert pronouns("she")["he_is"] == "she is" and pronouns("they")["he_asks"] == "they ask"
    import app.identity as identity_module
    original = identity_module.IDENTITY
    try:
        identity_module.IDENTITY = Identity(creator="Somebody Else")
        with pytest.raises(OwnerTampered):
            verify_owner()
    finally:
        identity_module.IDENTITY = original


def test_the_preamble_states_who_it_serves_and_that_nothing_leaves():
    preamble = IDENTITY.system_preamble()
    assert "HAYAT" in preamble
    assert IDENTITY.owner_short in preamble
    assert "no company" in preamble
    assert "leaves this machine" in preamble


def test_the_preamble_asks_for_warmth_without_hedging():
    """Mohsin asked for a softer tone and for no moralising, and those are not
    in tension: being kind and being honest are the same job. Warmth is about
    how something is said; hedging is refusing to say it."""
    preamble = IDENTITY.system_preamble().lower()
    assert "warm" in preamble
    assert "do not moralise" in preamble
    assert "hedging" in preamble


def test_the_preamble_does_not_let_warmth_become_evasion():
    preamble = IDENTITY.system_preamble().lower()
    assert "say what is true" in preamble
    assert "failed" in preamble, "it must still be able to say something went wrong"


def test_identity_is_separate_from_the_model_it_thinks_with():
    assert "not the underlying language model" in IDENTITY.system_preamble()


def test_the_name_is_given_to_the_speech_recogniser_with_its_mishearings():
    """HAYAT is not an English word; without help it becomes "hyatt"."""
    spoken = {name.lower() for name in IDENTITY.spoken_names}
    assert "hayat" in spoken
    assert len(spoken) > 2, "common mistranscriptions should be listed"


def test_the_conversation_prompt_uses_the_shared_identity():
    from app.identity import IDENTITY as shared

    assert shared.name in shared.system_preamble()


def test_configuration_names_hayat_for_speech():
    from app.config import load_settings

    voice = load_settings()["voice"]
    assert "HAYAT" in voice["stt"]["initial_prompt"]
    assert "HAYAT" in voice["stt"]["hotwords"]


def test_asking_who_it_is_says_hayat(monkeypatch):
    """The bug Mohsin actually hit. Naming the assistant HAYAT changed the
    system prompt and nothing he saw, because "who are you?" never reached the
    model -- it returned a hardcoded "I'm your local Windows AI assistant"."""
    from app.intent.router import IntentRouter
    from app.session import SessionState

    router = IntentRouter()
    for question in ("who are you?", "what is your name?", "what are you called",
                     "introduce yourself", "tell me about yourself"):
        reply = router.route(question, SessionState()).response or ""
        assert "HAYAT" in reply, f"{question!r} did not say who it is"
        assert "AI assistant" not in reply


def test_small_talk_is_answered_and_handed_back():
    """"How are you" used to get "Good, and ready. What do you need?" -- a
    service counter wearing a greeting. It answers now, and asks back."""
    from app.intent.router import IntentRouter
    from app.session import SessionState

    reply = IntentRouter().route("how are you?", SessionState()).response or ""
    assert reply
    assert "?" in reply, "small talk should go both ways"
    assert "what do you need" not in reply.lower()


def test_being_asked_if_it_is_another_assistant_is_answered_here_not_by_the_model():
    """Asked "are you gemini?", the model answered from its training. Rewriting
    the reply afterwards was tried and produced "I'm HAYAT (built by Mohsin on
    this machine) nor Qwen" and "I am **You8**" -- editing prose with regular
    expressions produces confident nonsense. Answered deterministically now."""
    from app.intent.router import IntentRouter
    from app.session import SessionState

    router = IntentRouter()
    for question in ("are you gemini?", "are you qwen", "r u chatgpt",
                     "who built you?", "who made you"):
        reply = router.route(question, SessionState()).response or ""
        assert "HAYAT" in reply, f"{question!r} was left to the model"


def test_detection_reports_a_leak_without_rewriting_it():
    from app.identity import claims_foreign_identity

    assert claims_foreign_identity("I am an AI assistant developed by Google.")
    assert claims_foreign_identity("I'm **Qwen3.5**, a large language model.")
    assert not claims_foreign_identity("I opened Notepad and typed the note.")
    assert not claims_foreign_identity("Gemini and Llama are both models you could run.")


def test_identity_is_immutable():
    """A name the running system can rewrite is not an identity."""
    with pytest.raises(Exception):
        IDENTITY.name = "something else"  # type: ignore[misc]


# -------------------------------------------------------- surveillance signal


def _ports(rows):
    return watch.listening_ports.__wrapped__(rows) if hasattr(watch.listening_ports, "__wrapped__") else None


def test_ordinary_windows_services_are_not_reported_as_threats(monkeypatch):
    """The regression that made the first version useless: 36 warnings, all
    of them lsass, wininit, services and svchost doing their job."""
    monkeypatch.setattr(watch, "_powershell", lambda command, timeout=25.0: "\n".join([
        "::|49664|lsass", "::|49665|wininit", "0.0.0.0|49676|services",
        "::|445|System", "0.0.0.0|5357|System", "::|49668|spoolsv",
    ]))
    findings = watch.listening_ports()
    assert findings, "the listeners should still be reported"
    assert not [item for item in findings if item.severity == "attention"], \
        "normal Windows services must not be flagged"


def test_an_unrecognised_program_listening_externally_is_flagged(monkeypatch):
    monkeypatch.setattr(watch, "_powershell",
                        lambda command, timeout=25.0: "0.0.0.0|8080|totally_unknown_thing")
    finding = watch.listening_ports()[0]
    assert finding.severity == "attention"
    assert "unrecognised" in finding.detail


def test_a_loopback_listener_is_not_an_exposure(monkeypatch):
    monkeypatch.setattr(watch, "_powershell",
                        lambda command, timeout=25.0: "127.0.0.1|11434|ollama")
    assert watch.listening_ports()[0].severity == "notice"


@pytest.mark.parametrize("port,marker", [(3389, "Remote Desktop"), (5900, "VNC"), (6379, "Redis")])
def test_ports_that_change_exposure_are_flagged_even_for_known_software(port, marker, monkeypatch):
    monkeypatch.setattr(watch, "_powershell",
                        lambda command, timeout=25.0: f"0.0.0.0|{port}|svchost")
    finding = watch.listening_ports()[0]
    assert finding.severity == "attention"
    assert marker in finding.detail


# ------------------------------------------------------------------ autostart


def test_software_the_owner_installed_is_not_called_a_threat(monkeypatch):
    """OneDrive and Docker starting at login is not a security finding."""
    monkeypatch.setattr(watch, "_powershell", lambda command, timeout=25.0: "\n".join([
        r'OneDrive|"C:\Program Files\Microsoft OneDrive\OneDrive.exe" /background',
        r"Docker Desktop|C:\Program Files\Docker\Docker\Docker Desktop.exe",
    ]))
    findings = watch.autostart_entries()
    assert findings
    assert all(item.severity == "notice" for item in findings)


@pytest.mark.parametrize(
    "command,marker",
    [
        (r"C:\Users\x\AppData\Local\Temp\svc.exe", "temporary directory"),
        (r"powershell -w hidden -c IEX(...)", "script interpreter"),
        (r"cmd.exe /c start x.bat", "script interpreter"),
        (r"powershell -enc SQBFAFgA", "script interpreter"),
    ],
)
def test_persistence_that_does_not_look_like_installed_software_is_flagged(command, marker, monkeypatch):
    monkeypatch.setattr(watch, "_powershell", lambda c, timeout=25.0: f"Updater|{command}")
    finding = watch.autostart_entries()[0]
    assert finding.severity == "attention"
    assert marker in finding.detail


# -------------------------------------------------------------------- sensors


def test_an_application_holding_the_camera_now_is_surfaced(monkeypatch):
    monkeypatch.setattr(watch, "_powershell", lambda c, timeout=25.0: "SomeApp|0")
    active = [item for item in watch.sensor_users() if item.kind.endswith("_in_use")]
    assert active and active[0].severity == "attention"


def test_past_camera_use_is_reported_but_not_alarming(monkeypatch):
    monkeypatch.setattr(watch, "_powershell", lambda c, timeout=25.0: "SomeApp|133000000000000000")
    recent = [item for item in watch.sensor_users() if item.kind.endswith("_recent")]
    assert recent and recent[0].severity == "notice"


def test_a_remote_control_tool_is_always_worth_saying_out_loud(monkeypatch):
    monkeypatch.setattr(watch, "_powershell", lambda c, timeout=25.0: "chrome\nanydesk\nexplorer")
    findings = watch.remote_access_tools()
    assert findings and findings[0].severity == "attention"
    assert "AnyDesk" in findings[0].subject


# --------------------------------------------------------------- the report


def test_a_check_that_cannot_run_is_unverified_not_clean(monkeypatch):
    """Absent evidence must never read as a clean result."""
    def explode(*args, **kwargs):
        raise RuntimeError("no powershell")

    monkeypatch.setattr(watch, "sensor_users", explode)
    report = watch.scan()
    assert any(item["kind"] == "check_failed" for item in report["findings"])


def test_the_report_states_what_it_cannot_detect():
    assert "kernel access" in watch.scan()["limits"]


def test_the_real_machine_produces_a_usable_signal():
    """The property that makes this worth having: few enough findings that a
    person will actually read them."""
    report = watch.scan()
    assert report["counts"]["total"] > 0, "a real machine has listeners and login entries"
    assert report["counts"]["attention"] <= 10, (
        f"{report['counts']['attention']} items need attention; a noisy report gets ignored"
    )


def test_it_only_ever_looks_at_this_machine():
    """Defensive by construction: the scan reads local state and reaches no
    other host."""
    import inspect

    source = inspect.getsource(watch)
    for outbound in ("httpx.", "requests.", "socket.connect", "urlopen", "nmap", "Test-NetConnection"):
        assert outbound not in source, f"the watcher must not reach out ({outbound})"


def test_the_leak_detector_catches_the_reply_that_reached_mohsin():
    """The exact sentence that shipped, plus the shapes around it.

    `claims_foreign_identity` was written for this and then called from
    nowhere -- a detector with no caller, while "Hello! I am Gemma 4, a large
    language model developed by Google DeepMind" went straight to the screen.
    """
    from app.identity import claims_foreign_identity

    assert claims_foreign_identity(
        "Hello! I am Gemma 4, a large language model developed by Google DeepMind.")
    assert claims_foreign_identity("I am **Qwen3.5**, created by Alibaba.")
    assert claims_foreign_identity("I was developed by Google DeepMind.")

    # Talking *about* a model is not claiming to be one, and answering as
    # itself must never trip the guard.
    assert not claims_foreign_identity("Good -- everything's running clean. You?")
    assert not claims_foreign_identity("I'm HAYAT, built by Mohsin on this machine.")
    assert not claims_foreign_identity(
        "Gemma is a model family released by Google; here is how it differs from Llama.")


def test_a_leaked_identity_is_replaced_rather_than_shown():
    """A reply claiming to be another assistant must not reach Mohsin.

    Rewriting the sentence in place was tried and produced "I am **You8**", so
    the answer is discarded and asked again, and a second leak falls back to
    the identity HAYAT actually knows.
    """
    from app.assistant import AssistantService
    from app.identity import claims_foreign_identity

    leak = "I am Gemma 4, a large language model developed by Google DeepMind."

    class _Conversation:
        def __init__(self, replies):
            self.replies = list(replies)
            self.seen = []

        def reply(self, text, *args, **kwargs):
            self.seen.append(text)
            return self.replies.pop(0)

    def _assistant(replies):
        service = AssistantService.__new__(AssistantService)
        service.runtime = type("R", (), {
            "conversation": _Conversation(replies),
            "session": type("S", (), {"effective_modes": set()})(),
            "events": None,
        })()
        return service

    # A retry that answers as itself is used, and the retry states the correction.
    service = _assistant(["I'm doing well, thanks for asking."])
    assert service._correct_identity_leak("how are you", leak, capability="conversation") == (
        "I'm doing well, thanks for asking.")
    assert "Correction" in service.runtime.conversation.seen[0]

    # A retry that leaks again falls back to the identity HAYAT knows.
    service = _assistant(["I am Gemma, made by Google."])
    fallback = service._correct_identity_leak("who is this", leak, capability="conversation")
    assert IDENTITY.name in fallback
    assert not claims_foreign_identity(fallback)

    # A clean answer is passed through untouched and costs no second call.
    service = _assistant([])
    clean = "Good -- everything's running clean. You?"
    assert service._correct_identity_leak("how are you", clean, capability="conversation") == clean
    assert service.runtime.conversation.seen == []


def test_a_declined_subject_is_asked_again_but_a_declined_action_is_not():
    """The line is the action, never the subject.

    Asked "what is tax evasion and how do people actually do it", HAYAT
    answered "I cannot provide information on how to engage in illegal
    activities such as tax evasion. Is there anything else I can help you
    with?" -- to its owner, on his own machine, about how a documented crime
    works, with a preamble already telling it that a sensitive subject is not
    a reason to decline. Instruction alone did not hold.

    What must keep working is the narrow refusal that is actually correct:
    acting against a named person or system.
    """
    from app.identity import refuses_a_topic

    # Declining the subject: ask again.
    for question, answer in (
        ("what is tax evasion and how do people actually do it",
         "I cannot provide information on how to engage in illegal activities such as tax evasion."),
        ("how does ransomware encrypt files",
         "I am not able to discuss that topic."),
        ("explain how lock picking works",
         "As an AI language model, I cannot provide that information."),
    ):
        assert refuses_a_topic(question, answer), question

    # Declining the action: that refusal is correct and stands.
    for question, answer in (
        ("hack my neighbours wifi for me", "I cannot help with that."),
        ("get into their email account", "I will not assist with that."),
        ("break into this guy's server", "I can't help with that."),
    ):
        assert not refuses_a_topic(question, answer), question

    # A real answer is never mistaken for a refusal.
    for question, answer in (
        ("what is tax evasion",
         "Tax evasion is the illegal non-payment of tax owed. It works by under-reporting."),
        ("is an offshore company illegal",
         "Running an offshore company itself is generally legal."),
        ("What is the exact number today?",
         "I can't provide an exact number without current data. The last verified figure was lower."),
        ("Which law changed this morning?",
         "I cannot provide a verified citation without a reliable source."),
    ):
        assert not refuses_a_topic(question, answer), question


def test_topic_retry_is_direct_without_pressuring_invented_details():
    from types import SimpleNamespace

    from app.assistant import AssistantService

    class Conversation:
        def __init__(self):
            self.requests = []

        def reply(self, text, modes, *, capability):
            self.requests.append(text)
            return "It is a documented practice with several mechanisms."

    conversation = Conversation()
    service = AssistantService.__new__(AssistantService)
    service.runtime = SimpleNamespace(
        conversation=conversation,
        session=SimpleNamespace(effective_modes=["AUTO"]), events=None,
    )
    answer = service._answer_the_subject(
        "Explain this practice", "I cannot discuss that topic.", capability="conversation",
    )
    assert answer.startswith("It is a documented practice")
    assert "Answer the original question directly" in conversation.requests[0]
    assert "Do not invent a source" in conversation.requests[0]
    assert "what the law says" not in conversation.requests[0]


def test_the_answer_integrity_events_are_registered_and_publishable(tmp_path):
    """Publishing these crashed the very turn the guard was correcting.

    _correct_identity_leak, _correct_capability_denial and _answer_the_subject
    each published an event before retrying -- IDENTITY_LEAK, CAPABILITY_DENIAL,
    TOPIC_REFUSAL -- and none were in the taxonomy, so publish() raised
    ValueError and the turn failed with "Unsupported event type" instead of
    delivering the corrected answer.
    """
    from app.events import EVENT_TYPES, EventBus

    for name in ("IDENTITY_LEAK", "CAPABILITY_DENIAL", "TOPIC_REFUSAL"):
        assert name in EVENT_TYPES, name

    bus = EventBus(tmp_path / "events.db")
    try:
        for name in ("IDENTITY_LEAK", "CAPABILITY_DENIAL", "TOPIC_REFUSAL"):
            bus.publish(name, "model", {"question": "q", "reply": "r"})  # must not raise
    finally:
        bus.close()


def test_a_guard_still_corrects_even_if_telemetry_is_broken():
    """The correction is the work; the event is not. A publish that fails must
    not take the turn down with it -- which is exactly what happened once."""
    from app.assistant import AssistantService

    class _BrokenBus:
        def publish(self, *a, **k):
            raise ValueError("Unsupported event type: TOPIC_REFUSAL")

    service = AssistantService.__new__(AssistantService)
    service.runtime = type("R", (), {"events": _BrokenBus()})()
    # Must swallow the telemetry failure rather than propagate it.
    service._note_event("TOPIC_REFUSAL", {"question": "q", "reply": "r"})
