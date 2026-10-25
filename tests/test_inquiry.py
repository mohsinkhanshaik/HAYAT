from __future__ import annotations

import json

import pytest

from app.agent.conversation import ConversationService
from app.context_budget import ContextBudget
from app.dual_brain import DualBrain
from app.executive import ExecutiveCortex
from app.inquiry import SelfInquiry
from app.memory.database import MemoryDatabase
from tests.test_executive_invention import ScriptedModel

HARD = 'Explain why a mutex can still deadlock when every thread locks in the same order.'
NOTES = json.dumps({'questions': [
    {'question': 'Is lock order the only deadlock cause?', 'answer': 'No; waiting on a condition while holding a lock also deadlocks.', 'basis': 'reasoning'},
    {'question': 'Which language and runtime does the user use?', 'answer': 'Not stated.', 'basis': 'needs_user'},
    {'question': 'Are re-entrant locks involved?', 'answer': 'Cannot tell from the request.', 'basis': 'unknown'},
]})
DETAIL = (' A deadlock needs four conditions at once: mutual exclusion, hold and wait, no preemption, and a '
          'circular wait. A global lock order removes the circular wait between locks, which is why it is the '
          'usual cure. It does not remove waits on other things. A thread that holds a lock and then blocks on a '
          'condition variable, a queue, a join, or an external process can still wait forever if the thread that '
          'would wake it needs the same lock first. Re-entrant locks, lock upgrades and callbacks that take locks '
          'in library code also break the ordering silently.')


def chat(tmp_path, model, *, scope='deep_turns', review=False):
    memory = MemoryDatabase(tmp_path / 'conversation.db'); memory.initialize()
    service = ConversationService(model, memory, 'inquiry-test')
    service.executive = ExecutiveCortex()
    service.inquiry = SelfInquiry(service.executive, scope=scope)
    service.dual_brain = DualBrain(service.executive, enabled=review, deep_turns=True)
    return service, memory


def test_only_hard_questions_are_examined():
    inquiry = SelfInquiry(ExecutiveCortex())
    assert inquiry.eligible(HARD, [], 'deep')
    assert inquiry.eligible('What should I name this small project folder today?', ['DEEP'], 'chat')
    assert not inquiry.eligible('hi there', ['DEEP'], 'greeting')
    assert not inquiry.eligible(HARD, ['FAST'], 'deep')
    assert not inquiry.eligible(HARD, ['CHILD_SAFE'], 'deep')
    assert not inquiry.eligible('What time is it in Tokyo right now please?', [], 'chat')
    assert not SelfInquiry(ExecutiveCortex(), scope='deep_modes').eligible(HARD, [], 'deep')
    assert not SelfInquiry(ExecutiveCortex(), scope='off').eligible(HARD, ['DEEP'], 'deep')
    with pytest.raises(ValueError):
        SelfInquiry(ExecutiveCortex(), scope='always')


def test_hard_question_is_examined_privately_before_the_draft(tmp_path):
    model = ScriptedModel(NOTES, 'Same lock order prevents cycles, but waiting while holding a lock still deadlocks. '
                          'Which language are you using?' + DETAIL)
    service, memory = chat(tmp_path, model)
    service.budget = ContextBudget()

    response = service.reply(HARD)

    assert response.startswith('Same lock order prevents cycles')
    assert len(model.calls) == 2
    examined, drafted = model.calls
    assert model.options[0]['response_schema'] == SelfInquiry.SCHEMA
    working = next(item for item in drafted if 'private self-inquiry' in item.content)
    assert working.optional_context and working.role == 'system'
    assert 'A (needs_user): Not stated.' in working.content and 'say plainly' in working.content
    assert drafted[-1].role == 'user' and drafted[-1].content == HARD
    record = memory.conversation_history('inquiry-test')[-1]['metadata']['self_inquiry']
    assert record['state'] == 'examined' and record['questions'] == 3 and record['open_points'] == 2
    assert service.inquiry.status()['answers_are_evidence'] is False


def test_broken_self_inquiry_never_blocks_the_answer(tmp_path):
    model = ScriptedModel('{"questions": [{"question": "x", "answer": "y", "basis": "certain"}]}',
                          'A deadlock needs a cycle of waits; same-order locking removes that cycle.' + DETAIL)
    service, memory = chat(tmp_path, model)

    response = service.reply(HARD, modes=['DEEP'])

    assert response.startswith('A deadlock needs a cycle')
    assert not any('private self-inquiry' in item.content for item in model.calls[1])
    record = memory.conversation_history('inquiry-test')[-1]['metadata']['self_inquiry']
    assert record['state'] == 'unavailable' and 'invalid' in record['error']


def test_greetings_stay_at_reflex_speed(tmp_path):
    model = ScriptedModel('Hey Mohsin, good to hear from you.')
    service, _memory = chat(tmp_path, model, review=True)
    service.budget = ContextBudget()

    service.reply('hey there how are you doing today my friend')

    assert len(model.calls) == 1 and service.last_inquiry is None


def test_hard_question_outside_deep_mode_is_also_reviewed(tmp_path):
    model = ScriptedModel(NOTES, 'Same-order locking cannot deadlock.' + DETAIL,
                          '{"verdict":"revise","issues":["Condition waits while holding a lock can still deadlock."]}',
                          'Same-order locking removes lock cycles, but waiting while holding a lock can still deadlock.' + DETAIL)
    service, memory = chat(tmp_path, model, review=True)
    service.budget = ContextBudget()

    response = service.reply(HARD)

    assert 'can still deadlock' in response
    metadata = memory.conversation_history('inquiry-test')[-1]['metadata']
    assert metadata['dual_brain_review']['revised'] and metadata['self_inquiry']['questions'] == 3
    assert service.executive.status()['last_goal']['calls'] == 4
