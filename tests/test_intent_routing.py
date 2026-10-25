from __future__ import annotations

from app.agent.planner import Planner
from app.agent.verifier import Verifier
from app.intent.models import IntentKind, TaskComplexity
from app.intent.router import IntentRouter
from app.models.base import Message, ModelProvider, ModelResponse
from app.safety.permissions import PermissionManager
from app.session import SessionState
from app.tools.applications import register_application_tools
from app.tools.registry import ToolRegistry, ToolResult


def app_registry(tmp_path):
    registry = ToolRegistry(PermissionManager({"default": "ALLOW", "permissions": {}}, tmp_path))
    register_application_tools(registry)
    return registry


def test_reported_failures_route_by_meaning_without_false_actions():
    router = IntentRouter()
    session = SessionState()
    greeting = router.route("HI", session)
    feedback = router.route("IT NOT WORKING I JUST OPENS NOTEPAD", session)
    voice = router.route("VOICE MODE ACTIVATE", session)
    assert greeting.intent == IntentKind.CONVERSATION and not greeting.requires_tools
    assert feedback.intent == IntentKind.DIAGNOSTIC and not feedback.requires_tools
    assert voice.intent == IntentKind.CONTROL and voice.control == "VOICE_ON" and not voice.requires_tools


def test_notepad_goal_has_text_level_criteria_and_missing_text_clarifies():
    router = IntentRouter()
    intent = router.route("Write a note in Notepad saying: Hello from my AI.")
    assert intent.intent == IntentKind.COMPUTER_CONTROL
    assert intent.entities["text"] == "Hello from my AI."
    assert {item.type for item in intent.success_criteria} == {"application_window_exists", "target_window_focused", "ui_text_contains"}
    vague = router.route("Write a note in Notepad")
    assert vague.needs_clarification and not vague.requires_tools


def test_program_in_reply_routes_to_answer_but_file_edit_uses_agent():
    router = IntentRouter()
    requested = router.route(
        "Write exactly one complete Python program. Run ollama ps with a five-second timeout. "
        "No explanations. No markdown except one Python code block."
    )
    assert requested.intent == IntentKind.QUESTION
    assert not requested.requires_tools
    assert requested.classification_source == "code_answer"
    edit = router.route("Write a Python program to D:\\AI\\diagnostic.py and save it")
    assert edit.intent == IntentKind.CODING and edit.requires_tools


def test_computer_references_resolve_across_turns():
    router = IntentRouter()
    session = SessionState(active_application="notepad", last_opened_application="notepad")
    write = router.route("Write hello in it.", session)
    append = router.route("Add: second line", session)
    close = router.route("Close it.", session)
    assert write.entities["application"] == append.entities["application"] == close.entities["application"] == "notepad"
    assert write.entities["text"] == "hello"
    assert append.entities["text"] == "second line"
    assert close.success_criteria[0].type == "target_window_absent"


def test_notepad_plans_edit_and_close_instead_of_launch_only(tmp_path):
    registry = app_registry(tmp_path)
    router = IntentRouter()
    planner = Planner(registry)
    write = router.route("Write a note in Notepad saying: Hello from my AI.")
    assert [step.tool for step in planner.create_plan(write.goal, intent=write, session=SessionState())] == ["launch_application", "notepad_edit"]
    close = router.route("Close Notepad")
    close_plan = planner.create_plan(close.goal, intent=close, session=SessionState(active_application="notepad"))
    assert [step.tool for step in close_plan] == ["close_application"]
    assert close_plan[0].expected["type"] == "target_window_absent"


def test_close_verifier_rejects_running_or_existing_target():
    verifier = Verifier()
    contradictory = ToolResult("close_application", True, {"application": "notepad", "closed": False, "target_window_exists": True, "running": True})
    assert not verifier.check({"type": "target_window_absent", "application": "notepad"}, contradictory).passed
    closed = ToolResult("close_application", True, {"application": "notepad", "closed": True, "target_window_exists": False, "remaining_windows": [{"handle": 2}]})
    assert verifier.check({"type": "target_window_absent", "application": "notepad"}, closed).passed


def test_goal_verifier_requires_observed_text_not_launch_success():
    criteria = [{"type": "ui_text_contains", "description": "text exists", "target": "notepad", "value": "hello"}]
    launch_only = [{"result": {"success": True, "output": {"application": "notepad", "running": True, "window": {"visible": True}}}}]
    assert not Verifier().check_goal(criteria, launch_only).passed
    observed = launch_only + [{"result": {"success": True, "output": {"application": "notepad", "focused": True, "text": "hello", "text_present": True}}}]
    assert Verifier().check_goal(criteria, observed).passed


def test_unapproved_application_gets_honest_clarification_not_false_success():
    """Regression test for a live-session bug: "Open Microsoft Word." used to
    reach the model planner with zero success criteria (since "Microsoft
    Word" matched no known application entity), so a single informational
    discover_applications call satisfied a trivially-empty check_goal() and
    the assistant said "Done. It's open." without ever opening anything.
    An unrecognized application must now short-circuit with an honest,
    immediate answer instead of entering planning at all."""
    intent = IntentRouter().route("Open Adobe Photoshop.")
    assert intent.needs_clarification
    assert not intent.requires_tools
    assert "Adobe Photoshop" in intent.response
    assert "approved application list" in intent.response
    assert intent.entities["application_recognized"] is False


def test_approved_application_still_opens_normally():
    intent = IntentRouter().route("Open Notepad.")
    assert intent.intent == IntentKind.COMPUTER_CONTROL
    assert intent.requires_tools
    assert not intent.needs_clarification
    assert intent.entities["application"] == "notepad"


def test_internet_search_phrasings_get_a_clean_research_query():
    """Regression test: "Look on the internet for X" and "Search the
    internet for X" both used to fall through to the model-based planner
    (requires_research was hardcoded False in the deterministic ACTION path),
    which free-generates a multi-step JSON plan for what is really one tool
    call -- observed JSONDecodeErrors on exactly this request shape."""
    for phrase in ("Look on the internet for Mohsin Khan Shaik.", "Search the internet for Mohsin Khan Shaik."):
        intent = IntentRouter().route(phrase)
        assert intent.intent == IntentKind.RESEARCH
        assert intent.requires_research
        assert intent.requires_tools
        assert intent.entities.get("query") == "Mohsin Khan Shaik"
        assert intent.complexity == TaskComplexity.RESEARCH
        assert {item.type for item in intent.success_criteria} == {"research_complete"}


def test_public_domain_scrape_phrasings_cannot_fall_through_to_chat():
    prompts = (
        "GREAT AND NOW TRY ACESS TO MOHSINKHANSHAIK.COM AND WEB SCRAP",
        "MOHSINKHANSHAIK.COM SUMMARIZE IT",
        "https://mohsinkhanshaik.com/ BROWSE IT",
    )
    for prompt in prompts:
        intent = IntentRouter().route(prompt)
        assert intent.intent == IntentKind.BROWSER
        assert intent.requires_tools and intent.requires_research
        assert intent.entities["url"].rstrip("/").lower() == "https://mohsinkhanshaik.com"
        assert {item.type for item in intent.success_criteria} == {"browser_destination", "research_complete"}


def test_whats_in_it_reuses_the_last_rendered_website():
    from app.session import SessionState

    session = SessionState(last_browser_tab={"url": "https://www.example.com/page", "title": "Example"})
    intent = IntentRouter().route("WHAT'S IN IT?", session)
    assert intent.intent == IntentKind.BROWSER
    assert intent.entities["url"] == "https://www.example.com/page"
    assert intent.entities["reference_resolved"] is True


class _StubModel(ModelProvider):
    name = "stub"

    def __init__(self, content: str) -> None:
        self._content = content

    def list_models(self) -> list[str]:
        return ["stub"]

    def generate(self, messages: list[Message], tools=None, temperature=None, response_schema=None, max_tokens=None) -> ModelResponse:
        return ModelResponse(self._content, "stub", self.name)


def test_model_route_discards_fabricated_action_narration():
    """Regression test for the false-action-claim bug: a live session had
    the model classifier return intent=ACTION, requires_tools=False, and a
    freely generated response like "Opening Microsoft Word." for pure
    conversational feedback ("You sound like AI."). Since requires_tools was
    False, the assistant echoed that response directly as if a real action
    had happened, with no tool ever called. An action-shaped intent with no
    tool request is now treated as a failed classification and its response
    text is discarded rather than trusted."""
    import json

    payload = json.dumps({
        "intent": "ACTION", "confidence": 1.0, "goal": "You sound like AI.",
        "requires_tools": False, "requires_research": False, "requires_planning": False,
        "complexity": "SIMPLE", "entities": {"Microsoft Word": "FILE"}, "response": "Opening Microsoft Word.",
    })
    router = IntentRouter(_StubModel(payload))
    intent = router._model_route("You sound like AI.", SessionState())
    assert intent.intent == IntentKind.CONVERSATION
    assert intent.response is None
    assert not intent.requires_tools


# ------------------------------------- the classifier classifies, it does not answer


def test_a_model_classification_never_carries_a_reply():
    """The bug this exists to stop.

    Asked to explain how Rust's ownership, borrowing and lifetimes fit
    together, the classifier returned `"Hello John Doe! How are you doing
    today?"` in its `response` field -- and because that matched none of the
    hollow shapes the guard knew about, it was handed straight back as the
    answer. Wrong content, wrong register, a name belonging to nobody, and the
    entire conversation path skipped: identity, context budget, recalled
    memory and tool facts along with it.

    Guarding it with a wider regex is a losing game. The rule is structural:
    a classifier classifies, and everything real goes through the
    conversation service, which has the history and knows who it is talking to.
    """
    import inspect

    from app.intent.router import IntentRouter

    source = inspect.getsource(IntentRouter._model_route)
    assert "response = None" in source
    assert 'response=_prose_only' not in source


def test_the_deterministic_greetings_still_answer_directly():
    """Turning off the model's replies must not turn off HAYAT's own.

    A hello does not need a model round-trip, and these are written here
    rather than generated, so they carry the right name and the right tone.
    """
    from app.identity import IDENTITY
    from app.intent.models import IntentKind
    from app.intent.router import IntentRouter
    from app.session import SessionState

    router = IntentRouter()
    for text in ("hey", "thanks", "who are you"):
        intent = router.route(text, SessionState())
        assert intent.intent == IntentKind.CONVERSATION
        assert intent.response, f"{text!r} lost its deterministic reply"

    identity = router.route("who are you", SessionState())
    assert IDENTITY.name in identity.response


def test_knowledge_conflict_questions_use_the_evidence_tool_path():
    from app.intent.models import IntentKind
    from app.intent.router import IntentRouter

    intent = IntentRouter().route("What knowledge conflicts do you have about Widget?")
    assert intent.intent == IntentKind.RESEARCH
    assert intent.requires_tools and intent.requires_planning
    assert intent.entities["mode"] == "knowledge_conflicts"


def test_external_questions_never_fall_into_authoritative_self_state():
    router = IntentRouter()
    external = (
        "What model architecture does Qwen use?",
        "Is voice available in Windows 11?",
        "How does memory management work in Python?",
        "What tools does Google provide for website analytics?",
        "Is research available in Microsoft Copilot?",
    )
    for question in external:
        assert router.route(question).intent == IntentKind.QUESTION, question

    assert router.route("What model are you using?").intent == IntentKind.SYSTEM_STATE
    assert router.route("Is your voice working?").intent == IntentKind.SYSTEM_STATE


def test_how_are_you_is_answered_as_a_greeting_not_as_an_identity_question():
    """Mohsin asked how HAYAT was and was told what it was.

    The reply began "Hello! I am Gemma 4, a large language model developed by
    Google DeepMind" -- wrong question, wrong identity, wrong maker. The
    branch existed but was anchored to `^how are you$`, so every other shape
    of the question fell through to the model.
    """
    router = IntentRouter()
    session = SessionState()
    for phrasing in ("how are you", "how are you?", "How are you doing", "how r u",
                     "how are u", "hey how are you", "how's it going",
                     "how are you doing today", "you good?"):
        intent = router.route(phrasing, session)
        assert intent.intent == IntentKind.CONVERSATION, phrasing
        assert intent.response, phrasing
        assert "gemma" not in intent.response.lower(), phrasing

    # A question that merely starts the same way is still a real question.
    assert router.route("how are you going to fix this", session).response is None


def test_asking_what_it_learned_is_routed_to_the_record_not_to_the_model():
    """"What did you learn today?" was answered from the model's imagination.

    It said "Nothing new today. My knowledge base is stable" on a day with 7
    research runs, 110 new claims and 36 sources behind it, then explained it
    was recalling its training data. There was no intent for the question at
    all, so nothing ever consulted what had actually been stored.
    """
    router = IntentRouter()
    session = SessionState()
    for phrasing, days in (("what did u learn today?", 1),
                           ("what did you learn today", 1),
                           ("did you learn anything today", 1),
                           ("what have you been learning lately", 7),
                           ("what did you research this week", 7)):
        intent = router.route(phrasing, session)
        assert intent.intent == IntentKind.CONTROL, phrasing
        assert intent.control == "LEARNING_REPORT", phrasing
        assert intent.entities["days"] == days, phrasing

    # Asking to be taught is a different request and must not be captured.
    for other in ("teach me about rust", "what did you learn about rust borrowing"):
        assert router.route(other, session).control != "LEARNING_REPORT", other


def test_asking_hayat_what_to_improve_reaches_its_own_review():
    """Typed, this question went to the model and answered a different one.

    The GUI button reads the evidence-backed review. The same words typed as
    a question produced 65 seconds of general advice about how Mohsin might
    improve himself, from a system holding a concrete list of its own defects.
    """
    router = IntentRouter()
    session = SessionState()
    for phrasing in ("what would make you better", "what should you improve",
                     "how can you improve", "what are your weaknesses",
                     "how do you get better"):
        intent = router.route(phrasing, session)
        assert intent.intent == IntentKind.CONTROL, phrasing
        assert intent.control == "SELF_REVIEW", phrasing

    # Mohsin asking about Mohsin is a different question and stays one.
    assert router.route("what would make me better", session).control != "SELF_REVIEW"
    assert router.route("how can i improve at rust", session).control != "SELF_REVIEW"


def test_asking_whether_it_can_scrape_is_a_question_not_an_instruction():
    """"Can you scrape websites?" was answered two wrong ways.

    From the model: "I don't have a built-in tool to browse or scrape live
    websites. If you provide me with specific text, I can analyze it deeply."
    There are thirty-four such tools and they work. Routed as an action
    instead, it launched a browser and reported "Done -- 2 steps completed",
    which is not an answer either.
    """
    router = IntentRouter()
    session = SessionState()
    for question in ("can you scrape websites?", "can you browse the internet?",
                     "do you have access to the web?", "are you able to read pdfs?",
                     "can you see through the camera?", "is it possible for you to search the web?"):
        intent = router.route(question, session)
        assert intent.intent == IntentKind.CONTROL, question
        assert intent.control == "CAPABILITY", question


def test_a_polite_instruction_is_still_carried_out():
    """"Can you open notepad" is a request. The difference is the target.

    normalize() strips "can you" as politeness, which is right for an
    instruction and wrong for a question, so the capability check reads the
    raw text -- the wrapper is the entire distinction.
    """
    router = IntentRouter()
    session = SessionState()
    for instruction in ("can you open notepad", "open notepad",
                        "can you scrape example.com", "take a screenshot"):
        assert router.route(instruction, session).control != "CAPABILITY", instruction


def test_asking_whether_it_learns_unattended_is_answered_from_the_learner():
    """"Do you learn on your own when I'm not talking to you?"

    Answered "No, Mohsin. Nothing happens in the background without a prompt
    from you" -- ninety seconds after the learner had added seventeen claims
    off the live web. A running thread is a matter of fact, not something to
    have an impression about.
    """
    router = IntentRouter()
    session = SessionState()
    for question in ("do you learn on your own when i am not talking to you?",
                     "do you learn in the background?",
                     "will you keep researching while i'm away?",
                     "do you update yourself automatically?",
                     "are you learning right now?"):
        intent = router.route(question, session)
        assert intent.intent == IntentKind.CONTROL, question
        assert intent.control == "BACKGROUND_LEARNING", question


def test_image_requests_route_to_generation_in_the_many_ways_people_ask():
    """HAYAT told Mohsin "I don't see the generated image yet, share it with me".

    The image route needed the word image/picture/artwork/wallpaper/illustration
    right after the verb, so "draw a cat", "generate a photo of a sunset", and
    "a picture of a lake" fell through to the model -- which answered as if
    Mohsin was supposed to supply the picture he had just asked HAYAT to make.
    """
    router = IntentRouter()
    session = SessionState()
    for phrasing in ("draw a cat", "paint a dragon", "sketch a face",
                     "generate a photo of a sunset", "make a picture of a dog",
                     "create an image of a robot", "a picture of a mountain lake",
                     "design a logo for my coffee shop", "create art of a warrior",
                     "render an illustration of a dragon", "make me a wallpaper of space"):
        intent = router.route(phrasing, session)
        assert intent.entities.get("modality") == "image", phrasing
        assert intent.entities.get("prompt"), phrasing


def test_video_requests_route_to_video_generation():
    router = IntentRouter()
    session = SessionState()
    for phrasing in ("make a video of a waterfall", "generate a clip of a running horse",
                     "animate a spinning globe", "create an animation of falling leaves"):
        intent = router.route(phrasing, session)
        assert intent.entities.get("modality") == "video", phrasing
        assert intent.entities.get("prompt"), phrasing


def test_ordinary_make_and_render_requests_are_not_mistaken_for_pictures():
    """A generation net that catches "generate a report" or "render the markdown
    to html" would break real work. Only image/video nouns and the unambiguous
    draw/paint/sketch/illustrate verbs count."""
    router = IntentRouter()
    session = SessionState()
    for phrasing in ("generate a report", "create a new file", "make a folder",
                     "render the markdown to html", "render the report",
                     "what is a photograph"):
        intent = router.route(phrasing, session)
        assert intent.entities.get("modality") not in {"image", "video"}, phrasing


def test_asking_it_to_explain_gets_the_explanation_not_a_yes():
    """"Can you explain recursion?" was routed as a capability question, so the
    reply was whether HAYAT can explain things rather than the explanation."""
    router = IntentRouter()
    for request in ("Can you explain recursion?", "could you describe how DNS works?",
                    "can you summarize the French revolution?", "Can you please define entropy?"):
        intent = router.route(request, SessionState())
        assert intent.control != "CAPABILITY", request
        assert intent.intent == IntentKind.QUESTION, request
    # Asking what it can do is still a capability question.
    assert router.route("can you scrape websites?", SessionState()).control == "CAPABILITY"


def test_opening_a_website_or_repository_is_not_an_unknown_application():
    """"Open YouTube" was answered "YouTube isn't on my approved application list"."""
    router = IntentRouter()
    for request, kind in (("Open YouTube", IntentKind.BROWSER), ("Open the website", IntentKind.BROWSER),
                          ("Open the repository and inspect code", IntentKind.CODING)):
        intent = router.route(request, SessionState())
        assert intent.intent == kind, request
        assert not intent.needs_clarification, request
    unknown = router.route("open frobnicator", SessionState())
    assert unknown.needs_clarification and "approved application list" in (unknown.response or "")


def test_look_it_up_means_research_unless_there_is_something_local_to_look_at():
    router = IntentRouter()
    for request in ("Look this up quickly: current Ollama documentation.",
                    "Look up the current Ollama documentation", "look up how tls handshakes work"):
        assert router.route(request, SessionState()).intent == IntentKind.RESEARCH, request
    assert router.route("look up youtube.com", SessionState()).intent == IntentKind.BROWSER
    assert router.route("look at the screen", SessionState()).intent == IntentKind.COMPUTER_CONTROL


def test_look_requests_from_real_use_reach_the_right_place():
    """From the owner's own history: "look in tech and business world" and
    "look for this and give a list" both replied "Done -- system status
    completed and verified", and "look again at me" checked whether the
    camera was on instead of looking."""
    router = IntentRouter()
    world = router.route("look in tech and business world", SessionState())
    assert world.intent == IntentKind.RESEARCH and world.goal == "tech and business world"
    for request in ("look into quantum computing", "look for rust tutorials", "look around the job market"):
        assert router.route(request, SessionState()).intent == IntentKind.RESEARCH, request

    earlier = SessionState()
    earlier.last_user_input = "all things about jobs in market and upcoming"
    follow_up = router.route("look for this and give a list", earlier)
    assert follow_up.intent == IntentKind.RESEARCH
    assert follow_up.goal.startswith("jobs in market and upcoming") and "give a list" in follow_up.goal

    again = router.route("look again at me", SessionState())
    assert again.entities.get("modality") == "camera" and again.entities.get("observation") is True

    # Local things stay local.
    assert router.route("look in the app/voice folder", SessionState()).entities == {"file_operation": "list", "path": "app/voice"}
    assert router.route("look for report.pdf", SessionState()).entities["file_operation"] == "find"
    assert router.route("look for notepad", SessionState()).intent == IntentKind.COMPUTER_CONTROL
    assert router.route("look at the screen", SessionState()).intent == IntentKind.COMPUTER_CONTROL


def test_creative_writing_is_answered_in_chat_not_written_to_a_file():
    r"""Live chat 2026-10-10: 'Write a haiku about rain.' wrote D:\AI\rain_haiku.txt."""
    from app.intent.router import IntentRouter
    from app.intent.models import IntentKind

    router = IntentRouter()
    for text in ("Write a haiku about rain.", "write me a poem about my mom", "Write a short story about a dragon"):
        intent = router.route(text)
        assert intent.intent is IntentKind.QUESTION and intent.classification_source == "creative_writing", text
    assert router.route("write a haiku about rain and save it to a file").intent is not IntentKind.QUESTION
    assert router.route("Write a note in Notepad saying hello").intent is not IntentKind.QUESTION
