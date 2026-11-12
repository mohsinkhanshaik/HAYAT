"""The turn-level use of the dialogue record: answer again after a complaint, investigate, weigh a goal.

app/assistant.py calls three functions here, so the logic sits in one place and the turn handler stays readable:

* :func:`observe` -- before routing: read the owner's message for a goal or a fact about himself
  (app/dialogue_state.py). Private-mode and child-mode turns are not read.
* :func:`intercept` -- after routing, before the routed path runs. Three cases take the turn:

  1. the message is **feedback on the last answer** ("same reply, look deeper", "i didn't ask about X, i was
     asking you to Y"): the refused reply is marked, the *question* is recovered, and it is answered again by a
     different route than the one that was refused;
  2. the message asks to **find out why something failed**, or points at a failure HAYAT just reported ("ok can u
     fix it?"): the recorded events and logs are read (app/investigation.py);
  3. the message is a **goal-sized money question**, in its own words or because the owner has stated such a
     goal: it is measured against its size (app/ambition.py) and the open questions are researched.

* :func:`after_reply` -- after any reply: keep its fingerprint, and if it copies a reply the owner refused,
  replace it instead of sending the same thing a third time.

None of this writes a line of the answer with a model. The model still writes ordinary replies; what changes is
what it is asked and what it is told.
"""
from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime, timedelta
from typing import Any

from app.intent.models import IntentKind, RequestIntent, TaskComplexity
from app.logging import log_swallowed

_log = logging.getLogger(__name__)

#: How long after a failure report a bare "fix it" still means that failure.
FAILURE_FOLLOW_UP = timedelta(minutes=45)
#: Routed sources that must never be taken over (a stopped money request, a boundary, a customer's message).
_PROTECTED_SOURCES = frozenset({"money_guard", "contact_boundary", "customer_work"})
#: Kinds of request that only read or answer: safe to run again after a complaint.
_ANSWER_KINDS = frozenset({IntentKind.RESEARCH, IntentKind.QUESTION, IntentKind.CONVERSATION, IntentKind.BROWSER})
MAX_FACET_SEARCHES = 3


def _state(service: Any) -> Any:
    return getattr(service.runtime, "dialogue", None)


def _recording_allowed(service: Any) -> bool:
    runtime = service.runtime
    guardian = getattr(getattr(runtime, "learning", None), "guardian", None)
    return not (bool(getattr(getattr(runtime, "authority", None), "privacy_mode", False))
                or bool(getattr(guardian, "active", False)))


def _topic_id(service: Any) -> str:
    decision = getattr(service._turn_state, "topic", None)
    return str(getattr(decision, "topic_id", "") or "")


def observe(service: Any, text: str) -> None:
    """Keep a goal or a fact about the owner that this message states."""
    state = _state(service)
    if state is None or not _recording_allowed(service):
        return
    try:
        from app.agent.conversation import owner_words

        state.observe(service.runtime.session.session_id, owner_words(text))
    except Exception:
        log_swallowed(_log, "reading a message for goals and facts", level=logging.WARNING)


# ---------------------------------------------------------------------------------------------------- intercept


def intercept(service: Any, text: str, intent: RequestIntent) -> Any | None:
    """A reply for the three cases above, or None to let the routed path run."""
    if intent.control or intent.classification_source in _PROTECTED_SOURCES:
        return None
    from app.agent.conversation import owner_words

    words = owner_words(text)
    try:
        return (_feedback_turn(service, words, intent) or _investigation_turn(service, words, intent)
                or _ambition_turn(service, words, intent))
    except Exception:
        # The ordinary path still answers; a fault here must never cost the owner his reply.
        log_swallowed(_log, "handling a turn through the dialogue record", level=logging.WARNING)
        return None


def _intent(kind: IntentKind, goal: str, source: str, **entities: Any) -> RequestIntent:
    made = RequestIntent(kind, .97, goal, False, False, False, TaskComplexity.STANDARD, dict(entities))
    made.classification_source = source
    return made


# ---- 1. feedback on the last answer


def _record_verdict(service: Any, text: str, feedback: Any, refused: dict[str, Any]) -> None:
    """The complaint counts as a verdict on that reply: the owner's record, the self-review and the memory links
    it was built from all learn from it (they used to hear only "that's wrong")."""
    memory = service.runtime.memory
    if not hasattr(memory, "path"):
        return
    try:
        from app.chat_feedback import CORRECTION
        from app.owner_feedback import OwnerFeedback, verdict

        if verdict(text) is not None or CORRECTION.match(text.strip()):
            return  # already recorded by remember_correction for this message
        kind = next((name for name in ("repeated", "off_topic", "generic", "wrong") if name in feedback.kinds), "wrong")
        session_id = service.runtime.session.session_id
        OwnerFeedback.beside(memory).record(-1, kind, feedback.restated or text[:1500], str(refused.get("request", "")),
                                            str(refused.get("reply", "")), session_id)
        from app.self_review import SelfReviewLog

        SelfReviewLog.beside(memory).judge(session_id, str(refused.get("reply", "")), -1)
        history = memory.conversation_history(session_id, 6)
        last = next((item for item in reversed(history) if item.get("role") == "assistant"), {})
        recalled = (last.get("metadata") or {}).get("recalled_memory_ids") if isinstance(last, dict) else None
        if recalled and hasattr(memory, "connect"):
            from app.memory.plasticity import reinforce_quietly

            reinforce_quietly(memory, recalled, False, reason=f"owner said the reply was {kind}")
    except Exception:
        log_swallowed(_log, "recording the owner's complaint as a verdict", level=logging.WARNING)


def _preface(feedback: Any, question: str, refused: dict[str, Any] | None, facts: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    if "repeated" in feedback.kinds:
        lines.append("You're right: that was the same answer I had already given. I've marked it as refused and I "
                     "won't send it again.")
    elif "off_topic" in feedback.kinds:
        lines.append("You're right, that was not what you asked"
                     + (f" — you did not ask about {feedback.denied}." if feedback.denied else "."))
    elif "generic" in feedback.kinds:
        lines.append("You're right, that was too generic to be useful. I've marked it as refused.")
    elif "wrong" in feedback.kinds:
        lines.append("Understood, that answer was wrong. I've marked it.")
    if "changeable" in feedback.kinds:
        named = ", ".join(f"{row['value']}" for row in facts[:3])
        lines.append("And understood: what you have today" + (f" ({named})" if named else "")
                     + " is where you start, not a limit. I'm treating it as something you can change.")
    if question:
        lines.append(f"Your question, as I read it: “{question[:300]}”.")
    return lines


def _feedback_turn(service: Any, text: str, intent: RequestIntent) -> Any | None:
    from app.answer_feedback import read as read_feedback

    feedback = read_feedback(text)
    state = _state(service)
    if feedback is None or state is None or not feedback.about_answer or not (feedback.rejects or feedback.wants_more):
        return None
    session_id = service.runtime.session.session_id
    topic_id = _topic_id(service)
    last = state.last_reply(session_id, topic_id=topic_id)
    if last is None:
        return None
    refused = last
    if feedback.rejects and _recording_allowed(service):
        refused = state.reject(session_id, feedback.kinds & {"repeated", "off_topic", "generic", "wrong"}, text,
                               topic_id=topic_id) or last
        _record_verdict(service, text, feedback, refused)
    question = feedback.restated or state.first_request(refused)
    if not question or question.strip().lower() == text.strip().lower():
        return None
    from app.dialogue_state import CIRCUMSTANCE

    facts = state.facts()[CIRCUMSTANCE]
    preface = _preface(feedback, question, refused, facts)
    # Where the question goes this time. Never to what produced the refused reply.
    ambition = _ambition_for(service, question, None, restated=True)
    if ambition is not None:
        body = _ambition_reply(service, question, ambition, refused=[refused])
        made = _intent(IntentKind.RESEARCH, question, "answer_feedback", feedback=feedback.to_dict(), ambition=True)
        return service._direct(made, "\n\n".join(["\n".join(preface), body]))
    try:
        again = service.runtime.intent_router.route(question, service.runtime.session)
    except Exception:
        log_swallowed(_log, "routing a question again after a complaint")
        return None
    same_source = bool(refused.get("source")) and _source_of(again) == refused.get("source")
    if again.intent not in _ANSWER_KINDS and not same_source:
        if not feedback.restated or again.control or again.needs_clarification:
            return None
        # He restated a request for an action ("i meant generate the image"): run it as he now put it.
        service._record("user", text, intent=intent.intent.value, repair=True)
        return service._handle_inner(question, turn=getattr(service._turn_state, "turn", None))
    made = _intent(IntentKind.RESEARCH, question, "answer_feedback", feedback=feedback.to_dict())
    searchable = (again.intent in {IntentKind.RESEARCH, IntentKind.BROWSER} or same_source
                  or service._wants_current_facts(question) or bool(feedback.kinds & {"deeper", "repeated", "generic"}))
    if searchable:
        if same_source:
            preface.append(f"Last time this went to {_source_label(refused.get('source', ''))}, which gives the same "
                           "answer every time. This time I'm searching for it.")
        body = service._research_in_background(question, query=service._research_query(question), level="deep")
        return service._direct(made, "\n".join(preface) + "\n\n" + body)
    body = service._conversation_reply(
        f"{question}\n\n(The owner refused your previous answer to this: {feedback.describe()}. Answer the question "
        "itself again, differently and more carefully, and say plainly what you are unsure of.)",
        capability=service._question_model_capability(question))
    made.intent = IntentKind.QUESTION
    return service._direct(made, "\n".join(preface) + "\n\n" + body)


def _source_of(intent: RequestIntent) -> str:
    """A label for what would answer a routed request: the tool a rule names, or the kind of answer."""
    entities = intent.entities or {}
    if entities.get("business_ideas") is not None:
        return "tool:business_ideas"
    if entities.get("modality"):
        return f"modality:{entities['modality']}"
    return intent.intent.value.lower()


def _source_label(source: str) -> str:
    return {"tool:business_ideas": "my fixed list of small businesses"}.get(source, f"“{source}”" if source else "a fixed rule")


def source_of_reply(service: Any, reply: Any) -> str:
    """What produced a reply, for the ledger: the tools of its task, else how it was routed."""
    outcome = getattr(reply, "outcome", None)
    tools = [str(event.get("tool")) for event in (getattr(outcome, "history", None) or []) if event.get("tool")]
    if tools:
        return "tool:" + "+".join(dict.fromkeys(tools))[:100]
    intent = getattr(reply, "intent", None)
    if intent is not None and (intent.entities or {}).get("ambition"):
        return "ambition"
    return str(getattr(service.runtime.session, "last_answer_source", "") or "")


# ---- 2. why did that happen


def note_failure_reported(service: Any) -> None:
    """A failure was just shown to the owner; a following "fix it" means that one."""
    try:
        service.runtime.session.failure_reported_at = datetime.now(UTC)
    except Exception:
        log_swallowed(_log, "noting that a failure was reported")


def _investigation_turn(service: Any, text: str, intent: RequestIntent) -> Any | None:
    from app.investigation import follow_up_on_failure, format_report, investigation_request

    investigator = getattr(service.runtime, "investigator", None)
    if investigator is None:
        return None
    asked = investigation_request(text)
    reported = getattr(service.runtime.session, "failure_reported_at", None)
    pointing = (not asked and isinstance(reported, datetime) and datetime.now(UTC) - reported <= FAILURE_FOLLOW_UP
                and follow_up_on_failure(text))
    if not asked and not pointing:
        return None
    result = investigator.investigate(text=text if asked else "", session_id=service.runtime.session.session_id)
    lines = [format_report(result)]
    if pointing:
        lines.insert(0, "You asked about the failure I just reported, so I investigated it instead of guessing. "
                        "I have not fixed or changed anything.")
        lines.append("Tell me which of these to do and I will, one at a time.")
    note_failure_reported(service)  # a further "why?" still means this
    service.runtime.session.last_answer_source = "investigation"
    made = _intent(IntentKind.DIAGNOSTIC, "Investigate the recorded failure", "investigation", investigation=True)
    return service._direct(made, "\n\n".join(lines))


# ---- 3. a goal-sized question


def _ambition_for(service: Any, text: str, intent: RequestIntent | None, *, restated: bool = False) -> Any | None:
    """The size to weigh this question against: named in the message, or the goal the owner has stated."""
    from app.ambition import Ambition, ambition_request, wants_small_start

    found = ambition_request(text)
    if found is not None:
        return found
    state = _state(service)
    goal = state.main_goal() if state is not None else None
    if goal is None or float(goal.get("amount") or 0) < 1e6 or wants_small_start(text):
        return None
    asks_ideas = bool(intent is not None and (intent.entities or {}).get("business_ideas") is not None)
    if not asks_ideas:
        try:
            from app.tools.launch import idea_request

            asks_ideas = idea_request(text) is not None
        except Exception:
            log_swallowed(_log, "checking whether a question asks for business ideas")
    if not asks_ideas:
        return None
    return Ambition(float(goal["amount"]), goal.get("horizon"), "the goal he has stated", text)


def _ambition_reply(service: Any, text: str, ambition: Any, *, refused: list[dict[str, Any]] | None = None) -> str:
    from app.ambition import evaluate
    from app.dialogue_state import CIRCUMSTANCE, LABELS

    state = _state(service)
    goal = state.main_goal() if state is not None else None
    amount = ambition.amount or (float(goal["amount"]) if goal and goal.get("amount") else None)
    horizon = ambition.horizon or (int(goal["horizon"]) if goal and goal.get("horizon") else None)
    circumstances = []
    if state is not None:
        circumstances = [{**row, "label": LABELS.get(row["attribute"], row["attribute"])} for row in state.facts()[CIRCUMSTANCE]]
        if refused is None:
            refused = state.rejected(service.runtime.session.session_id, topic_id=_topic_id(service),
                                     goal_key=goal["key"] if goal else "", limit=2)
    statement = str(goal["statement"]) if goal and not ambition.amount else ""
    key = goal["key"] if goal else f"wealth:{int(amount or 1e9)}"
    round_number, asked = (state.next_round(key), state.asked(key)) if state is not None else (1, [])
    # Only answers not shown before: the same evidence again is a repeat, not progress.
    shown = set(state.note(f"shown:{key}") or []) if state is not None else set()
    evidence = [row for row in _evidence(service, asked) if row["question"] not in shown]
    evaluation = evaluate(amount, horizon, circumstances=circumstances, refused=refused or [], goal_statement=statement,
                          round_number=round_number, asked=asked, evidence=evidence)
    if state is not None:
        state.note_asked(key, evaluation.questions)
        if round_number > 1 and evidence:
            state.set_note(f"shown:{key}", [*shown, *(row["question"] for row in evidence[:4])][-200:])
    service.runtime.session.last_answer_source = "ambition"
    tail = _start_facet_research(service, text, evaluation.questions) if evaluation.questions else ""
    return evaluation.to_dict()["text"] + ("\n\n" + tail if tail else "")


def _evidence(service: Any, asked: list[str]) -> list[dict[str, Any]]:
    """Answers that came back from this goal's earlier searches (finished jobs only, newest first)."""
    jobs = getattr(service.runtime, "research_jobs", None)
    if jobs is None or not asked:
        return []
    wanted = set(asked)
    try:
        recent = jobs.recent(30)
    except Exception:
        log_swallowed(_log, "reading finished searches for a goal evaluation")
        return []
    return [{"question": job.query, "answer": job.answer} for job in recent
            if getattr(job, "query", "") in wanted and getattr(job, "status", "") == "done" and getattr(job, "answer", "")]


def _start_facet_research(service: Any, asked: str, questions: list[str]) -> str:
    """Start the searches the evaluation named, one after another, each reporting back as its own message."""
    runtime = service.runtime
    modes = set(getattr(runtime.session, "effective_modes", []) or [])
    if bool(getattr(getattr(runtime, "authority", None), "privacy_mode", False)) or modes & {"PRIVATE", "LOCAL", "LOCAL_ONLY"}:
        return "Privacy or local-only mode is on, so I have not searched. Turn it off and say “research those” to run them."
    jobs = getattr(runtime, "research_jobs", None)
    wanted = [question for question in questions if question][:MAX_FACET_SEARCHES]
    if jobs is None or not wanted:
        return "I could not start the searches from here. Say “research” followed by any line above to run it."

    def run() -> None:
        for question in wanted:
            try:
                job = jobs.start(question, asked=asked, level="deep")
                jobs.release(job)  # its answer arrives as its own message
                job._done.wait(900)
            except Exception:
                log_swallowed(_log, "running a search for a goal evaluation", level=logging.WARNING)

    threading.Thread(target=run, name="hayat-goal-research", daemon=True).start()
    return (f"Searching now: {len(wanted)} question{'s' if len(wanted) != 1 else ''}, one after another. Each answer "
            "comes back with its sources; nothing above depends on them being favourable.")


def _ambition_turn(service: Any, text: str, intent: RequestIntent) -> Any | None:
    if intent.intent not in _ANSWER_KINDS | {IntentKind.ACTION}:
        return None
    if intent.intent is IntentKind.ACTION and (intent.entities or {}).get("business_ideas") is None \
            and not (intent.entities or {}).get("ambition"):
        return None
    ambition = _ambition_for(service, text, intent)
    if ambition is None:
        return None
    body = _ambition_reply(service, text, ambition)
    if ambition.reason == "the goal he has stated":
        body = ("You have told me your goal, so I'm answering against it rather than with a list of small "
                "businesses.\n\n" + body)
    made = _intent(IntentKind.RESEARCH, text, "ambition", ambition=ambition.to_dict())
    return service._direct(made, body)


# ----------------------------------------------------------------------------------------------- after a reply


def after_reply(service: Any, text: str, reply: Any) -> Any:
    """Keep the reply's fingerprint; never send a copy of a reply the owner refused."""
    state = _state(service)
    if state is None or reply is None:
        return reply
    try:
        from app.agent.conversation import owner_words

        words = owner_words(text)
        display = str(getattr(reply, "display_response", "") or "")
        session_id = service.runtime.session.session_id
        from app.ambition import wants_small_start

        # Asked for by name ("show the starter business list"), a refused list is what he wants this time.
        copied = state.refused_copy(display, session_id=session_id) if display and not wants_small_start(words) else None
        if copied is not None and getattr(reply, "complete", None) is not False:
            replaced = _instead_of_a_copy(service, words, reply, copied)
            if replaced is not None:
                reply, display = replaced, str(getattr(replaced, "display_response", "") or "")
        if display and _recording_allowed(service):
            state.note_reply(session_id, words, display, topic_id=_topic_id(service), source=source_of_reply(service, reply))
    except Exception:
        log_swallowed(_log, "keeping a reply in the dialogue record", level=logging.WARNING)
    return reply


def _instead_of_a_copy(service: Any, text: str, reply: Any, copied: dict[str, Any]) -> Any | None:
    """The routed path produced a reply the owner already refused: answer by another route and say why."""
    when = str(copied.get("rejected_at", ""))[:10]
    lead = (f"I was about to give you the same answer you refused on {when} "
            f"({str(copied.get('rejected_as', '')).replace(',', ', ') or 'refused'}), so I stopped and did this instead.")
    # A complaint's own words are never the question: search what the refused reply was answering.
    from app.answer_feedback import read as read_feedback

    feedback = read_feedback(text)
    question = text if feedback is None or not feedback.about_answer else (
        feedback.restated or str(copied.get("request") or "") or text)
    ambition = _ambition_for(service, question, getattr(reply, "intent", None))
    if ambition is not None:
        body = _ambition_reply(service, question, ambition, refused=[copied])
        made = _intent(IntentKind.RESEARCH, question, "refused_copy", ambition=ambition.to_dict())
    else:
        body = service._research_in_background(question, query=service._research_query(question), level="deep")
        made = _intent(IntentKind.RESEARCH, question, "refused_copy")
    response = lead + "\n\n" + body
    service.runtime.session.last_response = response
    # The refused copy was already written to the conversation; the record must show what was actually said.
    service._record("assistant", response, direct=True, replaced_refused_copy=True)
    return service._build_reply(made, response, complete=True)
