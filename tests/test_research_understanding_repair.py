"""Regression cases from the owner's website-retry/camera incident."""
from types import SimpleNamespace

import pytest

from app.business_acquisition import remember_research_job
from app.intent.models import IntentKind
from app.intent.router import IntentRouter
from app.research.jobs import ResearchJobs
from app.research.subject import resolve_query, subject_from_history
from app.session import SessionState
from tests.test_networking_actions import runtime as runtime


@pytest.mark.parametrize('text', [
    'look again', 'look up again', 'look it up again on the web', 'look up the website again',
    'look up that website again', 'look again please', 'look at it again',
    'I meant look up the website again',
    "I didn't mean look at me again, I meant look up the website again",
    'i dint mean to look at me again me i meant that look up on website again',
])
def test_website_retry_beats_even_a_stale_camera_observation(text):
    state = SessionState(last_camera_observation={'at': 'old'}, research_audit={'query': 'NIST AI risk management'})
    intent = IntentRouter().route(text, state)
    assert intent.control == 'RESEARCH_AGAIN', intent
    assert intent.entities.get('modality') != 'camera'


@pytest.mark.parametrize('text', [
    "don't open the camera", 'it started watching through camera when I said look up again',
    'I did not ask you to look at me again', 'search the web for camera lens specifications',
])
def test_negated_narrative_or_website_camera_mentions_are_not_sensor_commands(text):
    intent = IntentRouter().route(text, SessionState(last_camera_observation={'at': 'old'}))
    assert intent.entities.get('modality') != 'camera', intent


@pytest.mark.parametrize('text', [
    'actually search the web for NIST AI risk management',
    'you are just talking around, search NIST instead',
])
def test_complaint_with_a_new_explicit_topic_does_not_repeat_the_old_search(text):
    intent = IntentRouter().route(text, SessionState(research_audit={'query': 'old topic'}))
    assert intent.intent is IntentKind.RESEARCH and 'nist' in intent.goal.lower(), intent


def test_process_feedback_is_never_a_literal_search_subject_even_below_router():
    complaint = 'u are just talking around same things without actually look on web and stuff and dark web'
    assert resolve_query(complaint) == ''
    history = [{'role': 'user', 'content': 'search for NIST AI risk management'},
               {'role': 'user', 'content': complaint}]
    assert 'NIST' in resolve_query(complaint, history)
    assert subject_from_history([{'role': 'user', 'content': 'open my banking application'}], research_only=True) == ''


def test_retrieved_pages_without_supported_claims_cannot_claim_research_success():
    source = SimpleNamespace(final_url='https://www.nist.gov/ai', title='NIST AI', retrieved_at='now', content_hash='abc')
    result = SimpleNamespace(answer='I retrieved pages but could not validate a factual claim.', complete=False,
                             confidence=0, sources=[source], claims=[], errors=[], trace={})
    jobs = ResearchJobs(lambda: SimpleNamespace(research=lambda *a, **k: result))
    job = jobs.start('NIST AI')
    assert job._done.wait(3)
    assert job.status == 'failed'
    report = jobs.result_text(job)
    assert 'I read 1 source page(s)' in report and 'finished looking up' not in report
    assert 'Pages actually read:' in report and source.final_url in report
    state = SessionState(conversation_topic='NIST AI')
    remember_research_job(state, job)
    assert state.research_audit['status'] == 'failed'
    assert state.research_audit['pages_checked'][0]['url'] == source.final_url


def test_actual_assistant_website_retry_never_calls_a_camera(runtime, monkeypatch):
    calls = []
    result = SimpleNamespace(answer='NIST source [1].', complete=True, confidence=.5, sources=[], claims=[], errors=[], trace={})
    monkeypatch.setattr(runtime.research, 'research', lambda q, **k: calls.append(q) or result)
    for name in runtime.registry.names():
        if name.startswith(('camera_', 'face_', 'people_')):
            monkeypatch.setattr(runtime.registry.get(name), 'handler', lambda **k: pytest.fail('sensor unexpectedly invoked'))
    runtime.research_jobs.inline_seconds = 2
    runtime.assistant.handle('Search for NIST AI risk management')
    runtime.session.last_camera_observation = {'at': 'old'}
    corrected = runtime.assistant.handle("I didn't mean look at me again, I meant look up the website again")
    assert corrected.intent.control == 'RESEARCH_AGAIN' and len(calls) == 2
    assert calls[0] == calls[1] and 'NIST' in calls[0]


def test_retry_without_a_prior_subject_requests_clarification_without_tools(runtime, monkeypatch):
    monkeypatch.setattr(runtime.research, 'research', lambda *a, **k: pytest.fail('invented query'))
    runtime.session.last_camera_observation = {'at': 'old'}
    monkeypatch.setattr(runtime.assistant, '_history', lambda: [{'role': 'user', 'content': 'open my banking application'}])
    # The prior application command does not become an internet query.
    runtime.session.research_audit = {}
    reply = runtime.assistant.handle('look again')
    assert 'Which website or topic' in reply.display_response
    assert not runtime.research_jobs.recent()


@pytest.mark.parametrize('scope,method', [('onion', 'research_onion'), ('tor_web', 'research_tor_web')])
def test_retry_from_persisted_audit_preserves_network_scope(runtime, monkeypatch, scope, method):
    calls = []
    result = SimpleNamespace(answer='Supported source.', complete=True, confidence=.5, sources=[], claims=[], errors=[], trace={})
    monkeypatch.setattr(runtime.research, method, lambda q, **k: calls.append(q) or result)
    monkeypatch.setattr(runtime.research, 'research', lambda *a, **k: pytest.fail('scope changed to direct research'))
    runtime.session.research_audit = {'query': 'NIST AI', scope: True}
    runtime.research_jobs.inline_seconds = 2
    runtime.assistant.handle('look up again')
    assert calls == ['NIST AI'] and getattr(runtime.research_jobs.recent()[0], scope)


@pytest.mark.parametrize('phrase,scope', [('search again on tor', 'tor_web'), ('look up again on onion', 'onion'),
                                        ('look again on dark web', 'onion')])
def test_retry_can_explicitly_select_a_new_network_scope(phrase, scope):
    intent = IntentRouter().route(phrase, SessionState(research_audit={'query': 'NIST AI'}))
    assert intent.control == 'RESEARCH_AGAIN' and intent.entities['research_scope'] == scope


def test_search_again_with_a_new_named_topic_replaces_the_previous_query():
    intent = IntentRouter().route('search again for OpenCV face tutorial', SessionState(research_audit={'query': 'NIST AI'}))
    assert intent.intent is IntentKind.RESEARCH and 'OpenCV' in intent.goal


def test_live_publisher_notices_cannot_be_promoted_to_research_claims():
    from app.research.claims import ClaimExtractor, is_assertion
    from app.research.extractor import ContentExtractor
    from tests.test_research import resource
    html = b'''<html><title>Example glossary</title><main>
    <!-- Google Tag Manager (noscript) End Google Tag Manager (noscript) -->
    <p>You are viewing this page in an unauthorized frame window. This is a potential security issue, you are being redirected to https://example.org .</p>
    <p>Send inquiries about terminology to the Source's authors; publications usually include a contact email.</p>
    <p>Artificial intelligence is a field of computing that studies systems performing tasks requiring human intelligence.</p>
    </main></html>'''
    source, _ = ContentExtractor().extract(resource(content=html))
    assert 'Google Tag Manager' not in source.text
    assert not is_assertion('You are viewing this page in an unauthorized frame window.')
    claims = ClaimExtractor().extract('artificial intelligence terminology', source, source.text, 8)
    assert len(claims) == 1 and claims[0].text.startswith('Artificial intelligence is')
