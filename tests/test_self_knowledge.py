"""HAYAT answers questions about itself from facts: limits, how it works, its tools and their record."""
import pytest

from app import capabilities, self_knowledge
from app.intent.router import IntentRouter


@pytest.fixture(scope="module")
def runtime():
    from app.runtime import build_runtime

    built = build_runtime(use_model=False, start_background_services=False)
    yield built
    built.close()


@pytest.mark.parametrize("text, control", [
    ("what can't you do?", "SELF_LIMITS"), ("what are your limits?", "SELF_LIMITS"),
    ("is there anything you can't do?", "SELF_LIMITS"),
    ("how do you work?", "SELF_HOW"), ("what happens when I ask you something?", "SELF_HOW"),
    ("what tools do you have?", "SELF_TOOLS"), ("show me all your tools", "SELF_TOOLS"),
    ("how reliable are your tools?", "SELF_RELIABILITY"),
    ("which tool would you use to make an invoice?", "SELF_TOOL_FOR"),
    ("explain the form_fill_prepare tool", "SELF_EXPLAIN_TOOL"), ("what does invoice_create do?", "SELF_EXPLAIN_TOOL"),
])
def test_questions_about_hayat_are_recognised(text, control):
    assert IntentRouter().route(text).control == control


@pytest.mark.parametrize("text", ["explain photosynthesis", "how do I work out my rate", "what can you do?",
                                  "what tools do carpenters use?", "what are your weaknesses"])
def test_other_questions_are_not_taken_as_self_questions(text):
    assert self_knowledge.recognise(text) is None


def test_the_invoice_tool_is_named_not_denied(runtime):
    # The model had said there was no invoice tool and suggested filling a web form instead.
    answer = runtime.assistant.handle("which tool would you use to make an invoice?").display_response
    # invoice_create or the newer invoice_automation (which also creates invoices); never a web form.
    assert answer.startswith("For that I'd use invoice_") and "form_fill_prepare" not in answer.split("\n")[0]


def test_customer_billing_tool_is_recommended_when_the_scope_is_explicit(runtime):
    answer = self_knowledge.tool_for_answer(runtime, 'invoice a saved customer from their quote')
    assert answer.startswith("For that I'd use customer_invoice:")
    generic = self_knowledge.tool_for_answer(runtime, 'make a quote')
    assert generic.startswith("For that I'd use quote_")


def test_limits_are_real_rules_not_almost_none(runtime):
    answer = runtime.assistant.handle("what can't you do?").display_response
    assert "almost none" not in answer.lower()
    for rule in ("never pay", "never type your passwords", "only after you confirm", "cannot change my own permissions"):
        assert rule in answer
    # This runtime has no model, so the answer also says what is unavailable right now.
    assert "Not available right now" in answer and "Talk and get answers" in answer


def test_how_it_works_follows_the_brain_mesh(runtime):
    from app.brain_mesh import BrainMesh

    answer = runtime.assistant.handle("how do you work?").display_response
    labels = {node.label for node in BrainMesh.NODES}
    for name, _text in self_knowledge.STEPS:
        assert name in answer
    assert {"Intent", "Authority", "Truth Gate", "Tool Bus"} <= labels
    assert "cannot approve itself" in answer and "Brain 5D" in answer


def test_every_tool_is_listed_by_area(runtime):
    answer = runtime.assistant.handle("what tools do you have?").display_response
    names = runtime.registry.names()
    assert f"I have {len(names)} tools" in answer
    assert all(name in answer for name in names)
    assert all(capability.title in answer for capability in capabilities.CATALOG
               if capabilities.tools_in(capability, names))


def test_one_tool_explained_with_whether_it_asks(runtime):
    answer = runtime.assistant.handle("explain the form_fill_prepare tool").display_response
    assert "Act for you online" in answer and "only prepares" in answer and "Track record: not used yet" in answer
    email = self_knowledge.explain_tool_answer(runtime, "email_send")
    assert "only when you ask for it in your own words" in email
    unknown = runtime.assistant.handle("explain the magic_tool tool").display_response
    assert unknown.startswith("I have no tool called magic_tool")


def test_reliability_is_from_records_only(runtime, monkeypatch):
    monkeypatch.setattr(self_knowledge, "_records", lambda _runtime: [])
    assert "no record of using any yet" in self_knowledge.reliability_answer(runtime)
    records = [{"tool": "web_search", "calls": 40, "failure_rate": 0.05, "enough_evidence": True},
               {"tool": "browser_navigate", "calls": 20, "failure_rate": 0.5, "enough_evidence": True}]
    monkeypatch.setattr(self_knowledge, "_records", lambda _runtime: records)
    answer = self_knowledge.reliability_answer(runtime)
    assert "web_search: 40 uses, 95% worked, reliable" in answer
    assert "Unreliable so far: browser_navigate" in answer


def test_hayat_can_read_these_facts_while_planning(runtime):
    result = runtime.registry.execute("about_hayat", {"topic": "tool_for", "task": "make an invoice"})
    assert result.success and "invoice_create" in result.output["text"]
    assert "never pay" in runtime.registry.execute("about_hayat", {}).output["text"]


def test_the_guide_explains_how_and_limits(shared_tk):
    from types import SimpleNamespace

    from app.gui_help import CapabilityGuide
    from app.gui_theme import Palette

    class GUI(Palette):
        root = shared_tk
        runtime = SimpleNamespace(registry=SimpleNamespace(names=lambda: []), model=None, voice=None,
                                  authority=SimpleNamespace(privacy_mode=False))

    guide = CapabilityGuide(GUI())
    try:
        guide.withdraw()
        how = guide.show_how()
        limits = guide.show_limits()
        assert how.winfo_exists() and limits.winfo_exists()
        text = limits.body.get("1.0", "end")
        assert "I never pay" in text
        how.destroy()
        limits.destroy()
    finally:
        guide.destroy()
