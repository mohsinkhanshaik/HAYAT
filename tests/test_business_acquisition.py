from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.business_acquisition import (acquisition_request, audit_report, discover, format_report, reported_financials,
                                     remember_research, remember_research_job, remember_topic, valuation_scenario)
from app.intent.router import IntentRouter
from app.research.models import SearchResult
from app.models.streaming import GenerationCancelled
from app.safety.permissions import PermissionManager
from app.session import SessionState
from app.tools.launch import idea_request
from app.tools.research import register_research_tools
from app.tools.registry import ToolRegistry


REQUEST = "all best business which got dead and for sale at cheap price than actual valuation and mistakes they made"


@pytest.mark.parametrize('text', ['What is business valuation?', 'Research why companies failed in 1929',
                                 'Find a cheap business to start', "Don't research businesses for sale"])
def test_definitions_history_startups_and_negation_are_not_acquisition_commands(text):
    assert not acquisition_request(text)


def research_fixture():
    urls = ['https://broker.example/business/123', 'https://dictionary.example/look-at']
    listing = SimpleNamespace(final_url=urls[0], title='Laundry business for sale',
                              text='Laundry business for sale. Asking price: $120,000. Seller retiring. Annual SDE: $45,000.',
                              retrieved_at='2026-10-08T12:00:00Z', content_hash='hash', status_code=200)
    grammar = SimpleNamespace(final_url=urls[1], title='Look at grammar', text='Look at a business. Past tense of look.',
                              retrieved_at=listing.retrieved_at, content_hash='other', status_code=200)
    return SimpleNamespace(search=SimpleNamespace(search=Mock(return_value=[SearchResult('Listing', urls[0]), SearchResult('Grammar', urls[1])])),
                           fetcher=SimpleNamespace(fetch=Mock(side_effect=lambda url, _: url)),
                           extractor=SimpleNamespace(extract=Mock(side_effect=lambda url: (listing if url == urls[0] else grammar, 1))))


def test_reported_transcript_retains_acquisition_topic():
    router, session = IntentRouter(), SessionState()
    for text in [REQUEST, 'i dint ask u that', 'look deep in it and all sorces and stuff', 'ok?']:
        intent = router.route(text, session)
        if text != 'ok?':
            assert intent.entities.get('business_acquisition') is True
            assert intent.entities['query'] == REQUEST
        remember_topic(session, text, intent)
        session.last_user_input = text
    audit = router.route('what did look at?', session)
    assert audit.classification_source == 'research_audit'
    assert REQUEST in audit.response and 'no recorded completed source checks' in audit.response
    assert idea_request(REQUEST) is None


def test_explicit_correction_changes_target_and_new_topic_does_not_reuse_business():
    router, state = IntentRouter(), SessionState(conversation_topic=REQUEST)
    corrected = router.route('No, I meant find businesses for sale in Ohio', state)
    assert corrected.entities['query'] == 'find businesses for sale in Ohio'
    unrelated = router.route('explain Rust lifetimes', state)
    remember_topic(state, 'explain Rust lifetimes', unrelated)
    assert not acquisition_request(state.conversation_topic)
    assert not router.route('look deep in it', state).entities.get('business_acquisition')


def test_discovery_requires_fetched_relevant_evidence_not_snippets():
    research = research_fixture()
    result = discover(research, REQUEST)
    assert len(result['candidates']) == 1
    assert len(result['pages_checked']) == 2
    assert research.fetcher.fetch.call_count == 2  # duplicate search hits fetched once
    candidate = result['candidates'][0]
    assert candidate['reported_financials']['asking_price']['amount'] == 120000
    assert candidate['reported_financials']['annual_SDE']['amount'] == 45000
    assert candidate['undervalued'] is None and candidate['failure_cause'] is None
    assert candidate['fair_value'] is None and candidate['status'] == 'availability_unconfirmed'
    report = format_report(result)
    assert 'Publisher excerpt (unverified)' in report
    assert 'Annual SDE: $45,000' in report and 'Comparable completed sales' in report
    assert 'grammar' not in report.lower()


def test_unavailable_sources_and_inactive_listings_are_not_bargains():
    research = research_fixture()
    research.fetcher.fetch.side_effect = OSError('blocked')
    result = discover(research, REQUEST)
    assert result['status'] == 'insufficient_evidence' and not result['candidates']
    assert 'coverage is incomplete' in format_report(result)
    research = research_fixture()
    doc = research.extractor.extract('https://broker.example/business/123')[0]
    doc.text += ' Business has been sold.'
    result = discover(research, REQUEST)
    assert result['candidates'][0]['status'] == 'possibly_inactive'


def test_directory_prices_never_become_one_business_and_listing_links_are_followed():
    research = research_fixture()
    directory_url = 'https://broker.example/laundromats-for-sale'
    listing_url = 'https://broker.example/business/123'
    listing = research.extractor.extract(listing_url)[0]
    directory = SimpleNamespace(final_url=directory_url, title='Laundromats for sale',
                                text='Laundromats for sale. Asking price: $500,000. Asking price: $120,000.',
                                retrieved_at=listing.retrieved_at, content_hash='directory', status_code=200,
                                links=[{'url': listing_url, 'text': 'View listing'},
                                       {'url': listing_url + '?utm_source=directory', 'text': 'View listing'}])
    research.search.search.return_value = [SearchResult(directory.title, directory_url)]
    research.extractor.extract.side_effect = lambda url: (directory if url == directory_url else listing, 1)
    result = discover(research, REQUEST, limit=4)
    assert len(result['discovery_pages']) == 1
    assert len(result['candidates']) == 1
    assert result['candidates'][0]['url'] == listing_url
    assert result['candidates'][0]['reported_financials']['asking_price']['amount'] == 120000
    assert research.fetcher.fetch.call_count == 2


def test_listing_navigation_does_not_turn_a_single_business_into_a_directory():
    research = research_fixture()
    doc = research.extractor.extract('https://broker.example/business/123')[0]
    doc.text = 'Businesses for sale | About us | ' + doc.text
    result = discover(research, REQUEST)
    assert len(result['candidates']) == 1
    assert not result['discovery_pages']
    assert result['candidates'][0]['criteria_match_verified'] is False


def test_cancel_does_not_turn_into_a_partial_success():
    research = research_fixture()
    research.search.search.side_effect = GenerationCancelled('cancelled')
    with pytest.raises(GenerationCancelled):
        discover(research, REQUEST)


def test_scenario_arithmetic_explicit_basis_and_adjustments():
    scenario = valuation_scenario(50000, 2, 3, 85000, 'USD', 'annual_SDE', 20000, 5000, 10000, 15000)
    assert scenario['equity_value_low'] == 60000
    assert scenario['equity_value_high'] == 110000
    assert scenario['undervaluation_proven'] is False
    with pytest.raises(ValueError):
        valuation_scenario(50000, 2, 3, 85000, 'USD', 'monthly_MRR')


def test_listing_periods_projections_scaled_amounts_and_conflicts_are_not_conflated():
    fields = reported_financials('Monthly SDE: $4,000. Projected annual revenue: USD 240,000. Asking price: $2 million.')
    assert 'annual_SDE' not in fields and 'asking_price' not in fields
    assert fields['annual_revenue']['projected'] is True
    assert not reported_financials('Asking price: $120,000–$180,000.')
    assert not reported_financials('Asking price: $120,000. Asking price: $140,000.')


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), -1])
def test_scenario_rejects_invalid_financial_inputs(bad):
    with pytest.raises(ValueError):
        valuation_scenario(bad, 2, 3, 85000, 'USD', 'annual_EBITDA')


def test_assistant_executes_sources_and_reports_audit_without_model(tmp_path):
    from tests.test_assistant_service import runtime
    rt = runtime(tmp_path)
    rt.registry = ToolRegistry(PermissionManager({'default': 'ALLOW', 'permissions': {}}, tmp_path))
    register_research_tools(rt.registry, research_fixture())
    first = rt.assistant.handle(REQUEST)
    assert 'Acquisition research' in first.display_response
    assert 'availability_unconfirmed' in first.display_response
    rt.assistant.handle('i dint ask u that')
    rt.assistant.handle('look deep in it and all sorces and stuff')
    rt.assistant.handle('ok?')
    audit = rt.assistant.handle('what did look at?')
    assert 'broker.example/business/123' in audit.display_response
    assert 'retrieved 2026-10-08' in audit.display_response
    assert 'Valuation has not been verified' in audit.display_response
    assert rt.manager.calls == []


def test_privacy_prevents_acquisition_network_calls(tmp_path):
    from tests.test_assistant_service import runtime
    rt = runtime(tmp_path)
    rt.authority = SimpleNamespace(privacy_mode=True)
    rt.registry = Mock()
    reply = rt.assistant.handle(REQUEST)
    assert 'Privacy' in reply.display_response
    rt.registry.execute.assert_not_called()


def test_late_general_research_never_overwrites_new_subject():
    session = SessionState(conversation_topic='new topic')
    remember_research(session, 'old topic', SimpleNamespace(sources=[]))
    assert not session.research_audit
    assert 'no recorded completed source checks' in audit_report({}, 'new topic')


def test_general_deep_followup_and_failed_job_preserve_truthful_status():
    session = SessionState(conversation_topic='Research Rust ownership and lifetimes', last_user_input='ok?')
    intent = IntentRouter().route('look deep in it and all sources and stuff', session)
    assert 'Rust ownership' in intent.entities['query']
    remember_research_job(session, SimpleNamespace(query=session.conversation_topic, status='failed', error='offline'))
    assert 'Status: failed' in audit_report(session.research_audit, session.conversation_topic)
    assert 'No pages were successfully fetched' in audit_report(session.research_audit, session.conversation_topic)
