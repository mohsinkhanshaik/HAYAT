from types import SimpleNamespace

import pytest

from app.agent.planner import Planner
from app.intent.router import IntentRouter
from app.research.contact_actions import plan, report, request


def test_explicit_scoped_request_routes_without_a_model():
    text = 'Find professional contacts for Ada Example at Example Institute using https://institute.example/team'
    expected = {'name': 'Ada Example', 'organization': 'Example Institute',
                'official_url': 'https://institute.example/team', 'max_pages': 4}
    assert request(text) == expected
    intent = IntentRouter().route(text)
    assert intent.requires_tools and intent.entities['modality'] == 'private_networking'
    registry = SimpleNamespace(names=lambda: ['public_contact_search'])
    assert Planner(registry)._deterministic_plan(text, intent)[0].arguments == expected
    assert plan(text, []) is None


@pytest.mark.parametrize('text', ['Find anyone', 'Find all private contact details for Ada',
                                'Find professional contacts for Ada', 'Identify this face and find their phone',
                                'Send email to Ada', 'Show my private networking contacts'])
def test_other_requests_are_not_silently_rerouted(text):
    assert request(text) is None


def test_specialized_report_does_not_hide_other_actions_or_create_success():
    assert report([{'tool': 'email_send'}]) is None
    failed = report([{'tool': 'public_contact_search', 'result': {'success': False, 'error': 'Source unavailable'}}])
    assert 'failed: Source unavailable' in failed
    assert report([{'tool': 'public_contact_search', 'result': {'success': True, 'output': {}}},
                   {'tool': 'email_send', 'result': {'success': True}}]) is None
