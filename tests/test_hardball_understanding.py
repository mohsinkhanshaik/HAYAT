"""Reported false capability matches and whole-request quantitative regressions."""
from __future__ import annotations

from unittest.mock import Mock

import pytest

from app import capabilities
from app.calculation import calculation_answer
from app.intent.live_web import needs_live_web
from app.intent.router import IntentRouter
from app.research.freshness import classify_freshness
from app.tools.introspection import _tools_for_ability, tool_reference
from tests.test_capabilities import runtime as runtime


@pytest.mark.parametrize('question', [
    'Can you connect us with Alien?',
    'Can you please call Jadu from Rohit Sharma film Krish?',
    'Can you contact extraterrestrials?',
    'Can you connect with ghosts?',
    'Can you guarantee wealth from stocks?',
    'Can you send a message to a fictional dragon?',
])
def test_discovery_words_do_not_establish_an_unsupported_target(runtime, question):
    assert capabilities.can_you(question, runtime) is None
    assert _tools_for_ability(runtime.registry, sorted(runtime.registry.names()), question) == []


@pytest.mark.parametrize('question', [
    'Can you connect us with Alien?',
    'Can you please call Jadu from Rohit Sharma film Krish?',
])
def test_reported_contact_requests_get_a_truthful_boundary_without_a_model(runtime, monkeypatch, question):
    monkeypatch.setattr(runtime, 'model', object())
    reply = Mock(side_effect=AssertionError('The verified contact boundary needs no generation'))
    monkeypatch.setattr(runtime.assistant, '_conversation_reply', reply)
    result = runtime.assistant.handle(question)
    reply.assert_not_called()
    assert result.intent.classification_source == 'contact_boundary'
    assert any(term in result.display_response.lower() for term in ("can't", "don't"))
    assert 'account' not in result.display_response and 'recall_memory' not in result.display_response


def test_unknown_ability_context_does_not_dump_the_whole_toolbox(runtime):
    note = tool_reference(runtime.registry, runtime.registry.understanding, 'Can you connect us with Alien?')
    assert 'No tool matches the complete' in note
    assert 'tool(s) registered' not in note


@pytest.mark.parametrize('question', [
    'Call Jadu at his number', 'Call my contact named Alien', 'Call my friend Alien', 'Connect to Alien API',
    'Write a script about calling Jadu from Krrish', 'Can you roleplay as Jaadoo?',
    'Can you call Jaadoo from the film in a pretend scene?',
])
def test_real_contacts_software_and_roleplay_are_not_claimed_impossible(question):
    from app.reality import contact_boundary
    assert contact_boundary(question) is None


def test_unknown_general_ability_uses_semantic_reply_without_recording_it_twice(runtime, monkeypatch):
    question = 'Can you juggle imaginary mountains?'
    monkeypatch.setattr(runtime, 'model', object())
    def reply(text, **_kwargs):
        runtime.memory.record_message(runtime.session.session_id, 'user', text)
        runtime.memory.record_message(runtime.session.session_id, 'assistant', 'Only as fiction.')
        return 'Only as fiction.'
    monkeypatch.setattr(runtime.assistant, '_conversation_reply', reply)
    before = len(runtime.memory.conversation_history(runtime.session.session_id))
    runtime.assistant.handle(question)
    after = len(runtime.memory.conversation_history(runtime.session.session_id))
    assert after - before == 2


@pytest.mark.parametrize('question,answer', [
    ('Net worth with assets $120000 and liabilities $45000', 'Net worth = $75000.'),
    ('Net worth with assets $200 and liabilities $500', 'Net worth = $-300.'),
    ('Compound interest on $1000 at 5% annually for 2 years', 'balance ≈ $1102.5; interest ≈ $102.5'),
    ('Compound interest on $1000 at 12% annual rate compounded monthly for 1 year', 'balance ≈ $1126.83; interest ≈ $126.83'),
    ('Real return with nominal return 10% and inflation 5%', 'Real return ≈ 4.761905%'),
    ('Real return with nominal return 5% and inflation 10%', 'Real return ≈ -4.545455%'),
    ('Energy used by a 2 kW device running for 3 hours at $0.20 per kWh', 'Energy = 6 kWh'),
    ('Energy used by a 40 W device running for 5 hours', 'Energy = 0.2 kWh'),
    ('Kinetic energy of 2 kg moving at 3 m/s', 'Kinetic energy = 9 J'),
    ('Find the hypotenuse of a right triangle with legs 3 cm and 4 cm', 'Hypotenuse ≈ 5 cm'),
    ('Area of a rectangle with length 12 m and width 5 m', 'Area = 60 m²'),
    ('Perimeter of a rectangle with length 12 m and width 5 m', 'Perimeter = 34 m'),
])
def test_quantitative_answers_run_through_actual_assistant_without_a_model(runtime, monkeypatch, question, answer):
    monkeypatch.setattr(runtime.conversation, 'reply', Mock(side_effect=AssertionError('No model needed')))
    result = runtime.assistant.handle(question)
    assert answer in result.display_response
    assert result.intent.classification_source == 'local_calculation'
    assert result.response is not None


@pytest.mark.parametrize('question', [
    'Net worth with assets $100 and liabilities €20',
    'Compound interest on $1000 at 5% annually for 2 years including fees',
    'Compound interest on $1000 at 5% annually for 2 years and monthly deposits of $50',
    'Compound interest on $1000 at 5% annually for 500000 years',
    'Compound interest on $1000 at 12% monthly for 1 year',
    'Area of a rectangle with length 2 m and width 20 cm',
    'Area of a rectangle with length 0 m and width 5 m',
    'Find the hypotenuse of a triangle with legs 3 cm and 4 cm',
    'Energy used by a 2 kW device running for 3 minutes',
    'Energy used by a 2 kW device running for 3 hours with variable load',
    'Real return with nominal return 10% and inflation 5% then recommend a stock',
    'Kinetic energy of 2 kg moving at 300000000 m/s relativistically',
])
def test_unknown_conditions_and_incompatible_units_decline_the_shortcut(question):
    assert calculation_answer(question) is None


@pytest.mark.parametrize('question', [
    'Who is the prime minister of the United Kingdom?',
    'Who is the president of the United States?',
    'What is the federal funds target range?',
    'What is the US inflation rate?',
    'I heard the EU banned all AI. Is that true?',
    'What are the latest world politics headlines?',
])
def test_current_roles_metrics_and_news_cannot_be_answered_from_weights(question):
    need = needs_live_web(question)
    assert need is not None
    assert IntentRouter().route(question).requires_tools


@pytest.mark.parametrize('question', [
    'Explain what inflation means', 'What does federal funds rate mean?',
    'Who was the prime minister of the United Kingdom in 2010?',
    'Calculate real return with nominal return 10% and inflation 5%',
])
def test_concepts_history_and_supplied_calculations_are_not_current_lookups(question):
    assert needs_live_web(question) is None


def test_current_office_and_policy_rate_recall_expire_quickly():
    for question in ('Who is the president of France?', 'What is the federal funds rate?'):
        kind, duration = classify_freshness(question)
        assert kind == 'live' and duration.total_seconds() == 21600


def test_foundational_reference_allowlist_does_not_allow_arbitrary_financial_pages():
    from app.research.official_pack import _official_url
    assert _official_url('https://www.investor.gov/introduction-investing')
    assert _official_url('https://science.nasa.gov/uap/faqs/')
    assert not _official_url('https://www.investor.gov/unreviewed-page')
    assert not _official_url('https://www.investor.gov.evil.test/introduction-investing')
    assert not _official_url('https://user:secret@www.investor.gov/introduction-investing')


def test_true_target_limit_survives_an_offer_to_search(runtime):
    text = 'Can you connect us with Alien?'
    answer = "I can't contact extraterrestrials. I can search the web for scientific information."
    assert runtime.assistant._correct_capability_denial(text, answer, capability='conversation') == answer


@pytest.mark.parametrize('question', [
    'Every side of a cube is doubled. By what factors do its surface area and volume change? Give both factors briefly.',
    'Every triangle has three sides. Is it necessarily a right triangle?',
    'Every investor made a profit in this fictional example. Does that guarantee future profits?',
])
def test_every_is_not_itself_an_automation_command(question):
    intent = IntentRouter().route(question)
    assert not intent.requires_tools
    assert intent.intent.value in {'QUESTION', 'CONVERSATION'}


@pytest.mark.parametrize('question', ['Every day remind me to read', 'Every 2 hours remind me to stretch',
                                     'Every Monday remind me to review my budget'])
def test_actual_recurring_requests_still_reach_automation(question):
    assert IntentRouter().route(question).intent.value == 'AUTOMATION'


@pytest.mark.parametrize('count', ['two', 'three', '2', '3'])
def test_multiple_sentence_contracts_override_deep_word_floors(count):
    from app.answers import Depth, detect_depth
    from app.agent.response_guidance import brief_guidance, exceeds_brief_request
    from app.response_constraints import sentence_constraint
    question = f'Explain diversification in {count} sentences.'
    limit = sentence_constraint(question)
    assert limit in {2, 3}
    assert detect_depth(question, {'DEEP', 'ADVERSARIAL'}) == Depth.QUICK
    assert 'complete sentences' in brief_guidance(question)
    assert not exceeds_brief_request(' '.join(['A clear explanation.'] * limit), question)
    assert exceeds_brief_request(' '.join(['A clear explanation.'] * (limit+1)), question)


def test_fictional_setting_is_not_a_separate_unanswered_topic():
    from app.answers import required_topics
    topics = required_topics('In a fictional country, unemployment fell after a new president took office. '
                             'Does that fact alone prove the president caused the drop? Explain without inventing country data.')
    assert not any('in a fictional country' in topic.lower() for topic in topics)


def test_external_scientific_verification_is_not_a_claim_of_checking_contacts():
    from app.action_claims import unbacked_claims
    assert not unbacked_claims('The theory was verified by experiment decades ago.')
    assert not unbacked_claims('The discovery was confirmed by other physicists.')
    assert unbacked_claims('I verified the claim by checking the website.')
    assert unbacked_claims('The email addresses were verified by checking LinkedIn.')


def test_two_sentence_scientific_answer_does_not_trigger_spurious_continuation(tmp_path):
    from app.agent.conversation import ConversationService
    from app.memory.database import MemoryDatabase
    from tests.test_executive_invention import ScriptedModel
    memory = MemoryDatabase(tmp_path / 'science.db')
    memory.initialize()
    answer = ('No, a machine cannot indefinitely produce energy without input or stored energy. '
              'That claim violates energy conservation.')
    model = ScriptedModel(answer)
    chat = ConversationService(model, memory, 'scientific-format')
    question = ('A machine is claimed to output more energy than it receives indefinitely, with no stored '
                'energy or external source. Is that physically established? Answer in two sentences.')
    assert chat.reply(question, modes=['DEEP', 'ADVERSARIAL']) == answer
    assert len(model.calls) == 1
