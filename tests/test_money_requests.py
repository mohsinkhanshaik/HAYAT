from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.intent.models import IntentKind
from app.intent.router import IntentRouter
from app.safety.authority import MONEY_REASON
from app.safety.money_requests import describe, money_reply, money_request

STOPPED = [
    'buy airpods on amazon for me', 'pay my electricity bill', 'send 50 dollars to Ali on paypal',
    'make a payment of 200 to my landlord', 'hayat can you buy me a new mouse', 'please purchase the pro plan',
    'transfer 5000 rupees to my brother', 'order a pizza from dominos', 'go ahead and checkout',
    'I want you to pay the invoice', 'send $20 to Sara on venmo',
]
ANSWERED = [
    'how do I buy a house', 'explain how paypal payments work', 'what should I buy for my wife',
    'send the report to Ali', 'order these files by date', 'pay attention to the logs',
    'compare prices of airpods', 'send an email to my landlord about rent', 'make an invoice for 200 dollars',
    'what is the best way to send money abroad?', 'buy or rent, which is better for me?', 'buy, sell or hold?',
    # Bookkeeping in the earnings wallet records these; it never moves money.
    'withdraw 200 dollars to my bank', 'withdraw all my earnings to paypal', 'deposit 50 dollars',
]


@pytest.mark.parametrize('text', STOPPED)
def test_requests_to_move_money_stop_with_a_full_explanation(text):
    intent = IntentRouter().route(text)
    assert intent.classification_source == 'money_guard'
    assert intent.intent is IntentKind.CONVERSATION
    reply = intent.response
    assert reply.startswith('I stopped this. Nothing was paid, bought, sent or transferred.')
    assert f'What you asked: "{text}"' in reply
    for heading in ('What I understood', '- Action:', '- Amount:', '- Recipient or shop:', '- Service:',
                    '- Item or bill:', 'What I did: nothing outside this chat', 'What is recorded:',
                    'Why: ' + MONEY_REASON, 'app/safety/authority.py (tier 4, PROHIBITED)',
                    'What I can do next', 'pay yourself'):
        assert heading in reply


@pytest.mark.parametrize('text', ANSWERED)
def test_questions_about_money_and_ordinary_tasks_are_not_blocked(text):
    assert not money_request(text)
    assert IntentRouter().route(text).classification_source != 'money_guard'


@pytest.mark.parametrize('text, expected', [
    ('send 50 dollars to Ali on paypal',
     {'action': 'money transfer', 'amount': '50 USD', 'recipient': 'Ali', 'service': 'paypal', 'item': 'not stated'}),
    ('transfer ₹5,000 to my brother via UPI',
     {'action': 'money transfer', 'amount': '5,000 INR', 'recipient': 'my brother', 'service': 'UPI', 'item': 'not stated'}),
    ('please pay $120.50 to Comcast for the internet bill',
     {'action': 'payment', 'amount': '120.50 USD', 'recipient': 'Comcast', 'service': 'not stated', 'item': 'internet bill'}),
    ('make a payment of 200 to my landlord',
     {'action': 'payment', 'amount': '200 (currency not stated)', 'recipient': 'my landlord', 'service': 'not stated',
      'item': 'not stated'}),
    ('buy airpods on amazon for me',
     {'action': 'purchase', 'amount': 'not stated', 'recipient': 'not stated', 'service': 'amazon', 'item': 'airpods'}),
    ('go ahead and checkout',
     {'action': 'purchase', 'amount': 'not stated', 'recipient': 'not stated', 'service': 'not stated', 'item': 'not stated'}),
])
def test_details_are_read_literally_and_missing_ones_are_never_guessed(text, expected):
    assert describe(text) == expected
    assert f"- Amount: {expected['amount']}" in money_reply(text)


def test_money_request_never_reaches_a_model_tool_or_the_learner(tmp_path):
    from tests.test_assistant_service import runtime

    rt = runtime(tmp_path)
    observed = []
    rt.learner = SimpleNamespace(observe_request=lambda *args: observed.append(args))
    reply = rt.assistant.handle('send 50 dollars to Ali on paypal')
    assert reply.display_response.startswith('I stopped this. Nothing was paid')
    assert '- Recipient or shop: Ali' in reply.display_response
    assert rt.manager.calls == [] and observed == []
    recorded = rt.memory.conversation_history(rt.session.session_id)
    assert [row['role'] for row in recorded[-2:]] == ['user', 'assistant']

    rt.assistant.handle('explain how paypal payments work for small shops')
    assert observed, 'ordinary questions still reach the learner'
