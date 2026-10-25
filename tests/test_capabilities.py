"""HAYAT knows every power it has, explains it in plain words, and can reach all of it."""
import pytest

from app import capabilities
from app.agent.planner import Planner
from app.intent.router import IntentRouter


@pytest.fixture(scope="module")
def runtime():
    from app.runtime import build_runtime

    built = build_runtime(use_model=False, start_background_services=False)
    yield built
    built.close()


def test_every_registered_tool_belongs_to_a_plain_language_area(runtime):
    grouped, unplaced = capabilities.classify(runtime.registry.names())
    assert unplaced == [], f"Tools with no area (add them to app/capabilities.py): {unplaced}"
    # And no area points at tools that do not exist (a typo would hide a power).
    names = set(runtime.registry.names()) | {"describe_screen", "describe_image"}  # registered only when a vision model is configured
    for capability in capabilities.CATALOG:
        for pattern in capability.tools:
            if not pattern.endswith("*"):
                assert pattern in names, f"{capability.key}: {pattern} is not a registered tool"
            else:
                assert any(name.startswith(pattern[:-1]) for name in names), f"{capability.key}: {pattern}"


def test_each_area_is_explained_for_a_beginner():
    for capability in capabilities.CATALOG:
        assert capability.title and capability.summary.endswith(".") and capability.how
        assert len(capability.examples) >= 3 and capability.keywords
        assert len(capability.summary) < 220, capability.key


@pytest.mark.parametrize("capability", [c for c in capabilities.CATALOG if c.tools], ids=lambda c: c.key)
def test_every_example_reaches_the_tools_behind_its_area(runtime, capability):
    planner = Planner(runtime.registry)
    area_tools = set(capabilities.tools_in(capability, runtime.registry.names()))
    for example in capability.examples:
        offered = set(planner._candidate_tools(example))
        assert offered & area_tools, f"{example!r} does not reach any {capability.key} tool"


def test_the_examples_are_understood_as_their_own_area():
    for capability in capabilities.CATALOG:
        hits = sum(capability in capabilities.match(example, limit=3) for example in capability.examples)
        assert hits >= 2, capability.key


def test_what_can_you_do_lists_every_area_in_plain_words(runtime):
    answer = runtime.assistant.handle("what can you do?").display_response
    for capability in capabilities.CATALOG:
        assert capability.title in answer
    assert "Try:" in answer and "_" not in answer.split("\n")[2]  # plain words, not tool names


@pytest.mark.parametrize("text", ["help", "I'm new here", "how do I use you?", "what are your capabilities",
                                  "getting started"])
def test_beginner_questions_get_the_overview(runtime, text):
    intent = IntentRouter().route(text)
    assert intent.control in {"CAPABILITY", "HELP"}
    assert "Earn and manage money" in runtime.assistant.handle(text).display_response


def test_can_you_answers_with_status_how_and_examples(runtime):
    answer = capabilities.can_you("can you make invoices?", runtime)
    assert answer.startswith("Yes.") and "Earn and manage money" in answer
    assert "Try:" in answer and "invoice_create" in answer
    assert capabilities.can_you("can you juggle?", runtime) is None


def test_readiness_tells_a_beginner_what_to_do(runtime):
    state, note = capabilities.readiness(capabilities.BY_KEY["chat"], runtime)
    assert state == "setup" and "Ollama" in note  # this runtime was built without a model
    runtime.authority.set_privacy_mode(True)
    try:
        state, note = capabilities.readiness(capabilities.BY_KEY["senses"], runtime)
    finally:
        runtime.authority.set_privacy_mode(False)
    assert state == "off" and "Privacy mode" in note


def test_hayat_can_read_its_own_catalog_while_planning(runtime):
    result = runtime.registry.execute("my_capabilities", {})
    assert result.success and len(result.output["areas"]) == len(capabilities.CATALOG)
    one = runtime.registry.execute("my_capabilities", {"area": "send an email"}).output
    assert one["key"] == "messages" and "email_send" in one["tools"]


def test_a_general_can_you_is_a_question_and_a_specific_one_is_work():
    router = IntentRouter()
    assert router.route("can you make invoices?").control == "CAPABILITY"
    assert router.route("can you create an invoice for Acme for 3 hours at 40").requires_tools


def test_the_getting_started_guide_matches_the_catalog():
    from app.config import PROJECT_ROOT
    from app.gui_help import GETTING_STARTED

    guide = (PROJECT_ROOT / "docs" / "getting-started.md").read_text(encoding="utf-8")
    for capability in capabilities.CATALOG:
        assert capability.title in guide and capability.examples[0] in guide, capability.key
    for step in GETTING_STARTED:
        assert step in guide
