"""Messages about HAYAT's own last answer (app/intent/revisit.py) and how they are answered.

Live, 2026-11-07: "how did you come to this conclusion so make it again but better look up" was searched word for
word ("I'm searching for \"how did you come to this conclusion ...\" now"), and "make it again but better" was
taken for a computer action.
"""
from types import SimpleNamespace

import pytest

from app.assistant import AssistantService
from app.intent.models import IntentKind
from app.intent.revisit import original_question, parse, related
from app.intent.router import IntentRouter
from app.session import SessionState

ASKED = "how did you come to this conclusion so make it again but better look up"


def route(text, source='brain'):
    state = SessionState()
    state.last_answer_source = source
    return IntentRouter().route(text, state)


def test_the_live_message_is_about_the_last_answer_not_a_search_subject():
    intent = route(ASKED)
    assert intent.intent == IntentKind.CONTROL and intent.control == 'REVISIT_ANSWER'
    assert intent.entities['explain'] and intent.entities['redo'] and intent.entities['research']
    assert intent.entities['previous_source'] == 'brain'


@pytest.mark.parametrize('text', ['how did you come to this conclusion', 'how did you reach that answer', 'why do you say that',
                                  'what is your source for that', 'where did you get that from', 'explain how you got that',
                                  'are you sure? check it', 'how sure are you', 'redo that answer but better and look it up'])
def test_questions_about_the_answer_are_revisits(text):
    assert route(text).control == 'REVISIT_ANSWER', text


@pytest.mark.parametrize('text', ['make it again but better', 'do it again but better', 'is that the right answer? redo it properly'])
def test_a_plain_redo_goes_back_to_the_conversation_with_its_history(text):
    intent = route(text)
    assert intent.intent == IntentKind.CONVERSATION and intent.classification_source == 'contextual_followup'


@pytest.mark.parametrize('text', ['redo the kitchen layout plan', 'make a better logo for my bakery', 'how did the romans build roads',
                                  'why do cats purr', 'what is the source of the Nile', 'look up the weather in Lahore',
                                  'how do you make bread', 'do it again', 'make the logo better', 'check my router'])
def test_requests_with_their_own_subject_are_left_alone(text):
    assert parse(text) is None or route(text).control != 'REVISIT_ANSWER', text


HISTORY = [{'role': 'user', 'content': 'What is the tallest building in Europe?'},
           {'role': 'assistant', 'content': 'The Lakhta Center in Saint Petersburg.'},
           {'role': 'user', 'content': ASKED}]


def test_the_original_question_skips_the_revisit_messages():
    history = HISTORY + [{'role': 'assistant', 'content': 'How I reached ...'}, {'role': 'user', 'content': 'why do you say that'}]
    question, answer = original_question(history, 'why do you say that')
    assert question == 'What is the tallest building in Europe?' and answer.startswith('How I reached')
    assert related('tallest building Europe', question) and not related('python tutorials', question)


class Stub:
    """The parts of AssistantService that _revisit_answer uses, recording what it would search."""

    def __init__(self, history, audit=None):
        self.runtime = SimpleNamespace(session=SimpleNamespace(last_user_input=ASKED, research_audit=audit or {},
                                                                standing_instruction=None),
                                       conversation=None, model=None, thoughts=None)
        self.history, self.searched, self.asked = history, [], []

    def _history(self):
        return self.history

    def _research_query(self, text, query=None):
        return text

    def _research_in_background(self, text, *, query=None, level=None, **_):
        self.searched.append((text, query, level))
        return f'I\'m searching for "{query}" now.'

    def _wants_current_facts(self, _text):
        return False

    def _conversation_reply(self, text, capability=''):
        self.asked.append(text)
        return 'A more careful answer.'

    def _question_model_capability(self, _text):
        return 'conversation'


def revisit(stub, **entities):
    intent = SimpleNamespace(entities={'explain': False, 'redo': False, 'research': False, 'previous_source': 'brain', **entities})
    return AssistantService._revisit_answer(stub, intent)


def test_explain_and_look_up_researches_the_original_question_deeper():
    stub = Stub(HISTORY)
    reply = revisit(stub, explain=True, redo=True, research=True)
    assert stub.searched == [('What is the tallest building in Europe?', 'What is the tallest building in Europe?', 'deep')]
    assert 'how did you come' not in stub.searched[0][1]
    assert 'did not look anything up' in reply and "model's own knowledge" in reply
    assert 'Looking it up again' in reply


def test_an_answer_that_was_searched_lists_the_pages_it_read():
    audit = {'query': 'tallest building in Europe', 'status': 'completed', 'errors': [],
             'pages_checked': [{'title': 'Lakhta Center', 'url': 'https://example.org/lakhta', 'retrieved_at': 'x'}]}
    reply = revisit(Stub(HISTORY, audit), explain=True)
    assert 'I searched for “tallest building in Europe”' in reply and 'https://example.org/lakhta' in reply
    assert 'did not look anything up' not in reply


def test_a_search_about_something_else_is_not_presented_as_the_basis():
    audit = {'query': 'python tutorials', 'status': 'completed', 'pages_checked': [{'url': 'https://example.org/py'}]}
    reply = revisit(Stub(HISTORY, audit), explain=True)
    assert 'example.org/py' not in reply and 'did not look anything up' in reply


def test_a_redo_without_research_asks_the_model_again_with_the_original_question():
    stub = Stub(HISTORY)
    reply = revisit(stub, redo=True, explain=True)
    assert not stub.searched and stub.asked[0].startswith('What is the tallest building in Europe?')
    assert 'A more careful answer.' in reply


def test_nothing_to_revisit_is_said_plainly():
    reply = revisit(Stub([{'role': 'user', 'content': ASKED}]), explain=True, research=True)
    assert "haven't answered anything" in reply


@pytest.mark.parametrize('text', ['make it again but better', 'do it again but better', 'redo it but better',
                                  'is that the right answer? redo it properly'])
def test_after_a_search_a_better_redo_searches_again(text):
    state = SessionState()
    state.research_audit = {'query': 'tallest building in Europe', 'status': 'completed'}
    state.last_answer_source = 'research'
    intent = IntentRouter().route(text, state)
    assert intent.intent == IntentKind.CONTROL and intent.control == 'RESEARCH_AGAIN', text


def test_explaining_a_researched_redo_cites_the_search_not_a_rule():
    audit = {'query': 'tallest building in Europe', 'status': 'completed', 'errors': [],
             'pages_checked': [{'title': 'Lakhta Center', 'url': 'https://example.org/lakhta', 'retrieved_at': 'x'}]}
    stub = Stub(HISTORY, audit)
    stub.runtime.thoughts = SimpleNamespace(report=lambda: 'understood: QUESTION\nunderstood: CONTROL')
    reply = revisit(stub, explain=True, previous_source='revisit_answer')
    assert 'recorded research audit' in reply and "HAYAT's own rules" not in reply
    assert 'understood:' not in reply and 'Recorded steps' not in reply


@pytest.mark.parametrize('text', ['Which planet in our solar system has the most moons?', 'how many moons does Saturn have',
                                  'who is the richest person in the world?', 'what is the tallest building in the world',
                                  'how many confirmed exoplanets are there?', 'what is the world record for the 100m'])
def test_records_and_counts_that_change_are_looked_up(text):
    """Live 2026-11-07: the brain said Saturn (146 moons) in one run and Jupiter (95) in the next."""
    from app.intent.live_web import needs_live_web
    need = needs_live_web(text)
    assert need is not None and need.reason == 'changing_record', text


@pytest.mark.parametrize('text', ['what is the largest planet?', 'how many legs does a spider have?', 'why do cats purr',
                                  'I think richest people should pay more tax', 'how many moons should I draw in my picture'])
def test_settled_facts_and_personal_questions_are_not(text):
    from app.intent.live_web import needs_live_web
    need = needs_live_web(text)
    assert need is None or need.reason != 'changing_record', text


def test_a_researched_answer_is_not_called_a_fixed_rule():
    audit = {'query': 'tallest building in Europe', 'status': 'completed', 'errors': [],
             'pages_checked': [{'title': 'Lakhta Center', 'url': 'https://example.org/lakhta', 'retrieved_at': 'x'}]}
    reply = revisit(Stub(HISTORY, audit), explain=True, previous_source='deterministic')
    assert 'recorded research audit' in reply and "HAYAT's own rules" not in reply
