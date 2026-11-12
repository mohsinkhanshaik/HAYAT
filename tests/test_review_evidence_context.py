"""Critics must see the sources and constraints that the answer writer saw."""
import json
from types import SimpleNamespace

from app.dual_brain import DualBrain
from app.executive import ExecutiveCortex
from app.models.base import ModelResponse
from tests.test_executive_invention import ScriptedModel
from tests.test_inquiry import chat


def test_review_and_revision_receive_same_bounded_reference_data():
    model = ScriptedModel('{"verdict":"revise","issues":["Use the supplied project limit."]}', 'Keep the supplied limit of 17 units.')
    result = DualBrain(ExecutiveCortex()).review('Choose the next step.', ModelResponse('Use 99 units.', 'fixture', 'test'), model,
        reference_context=[{'kind': 'attached_files', 'content': 'Project limit: 17 units. Source confidence: unverified.'}],
        constraints=['Keep the response brief.'], open_questions=['Actual cost is not known.'])
    assert result.report['revised'] and result.report['reference_items'] == 1
    for call in model.calls:
        payload = json.loads(call[-1].content)
        assert payload['reference_context'][0]['content'].endswith('unverified.')
        assert payload['constraints'] == ['Keep the response brief.']
        assert payload['open_questions'] == ['Actual cost is not known.']
    assert result.report['verified'] is False


def test_reference_context_is_bounded_and_not_written_into_review_provenance():
    model = ScriptedModel('{"verdict":"accept","issues":[]}')
    result = DualBrain(ExecutiveCortex()).review('A question', ModelResponse('An answer', 'fixture', 'test'), model,
        reference_context=[{'kind': 'source', 'content': 'private-source-' + 'a' * 9000} for _ in range(10)])
    payload = json.loads(model.calls[0][-1].content)
    assert sum(len(item['content']) for item in payload['reference_context']) <= 16000
    assert 'private-source' not in json.dumps(result.report)


def test_conversation_review_keeps_recalled_files_runtime_and_standing_instruction(tmp_path):
    model = ScriptedModel('The attached project permits seventeen units.', '{"verdict":"accept","issues":[]}')
    service, memory = chat(tmp_path, model, scope='off', review=True)
    service.standing_instruction = 'Use plain English.'
    service.state_supplier = lambda: {'model': 'fixture', 'service_status': 'available'}
    service.attachments = SimpleNamespace(recall=lambda *_args, **_kwargs: [
        {'content': 'The project limit is seventeen units.', 'metadata': {'name': 'project.txt', 'path': 'project.txt'}}])
    service.reply('Which limit does the attached project specify for the next decision?', modes=['DEEP'])
    payload = json.loads(model.calls[1][-1].content)
    assert payload['constraints'] == ['Use plain English.']
    assert any('seventeen units' in item['content'] for item in payload['reference_context'])
    assert any(item['kind'] == 'runtime_snapshot' for item in payload['reference_context'])
    assert memory.conversation_history('inquiry-test')[-1]['metadata']['dual_brain_review']['reference_items'] >= 2
