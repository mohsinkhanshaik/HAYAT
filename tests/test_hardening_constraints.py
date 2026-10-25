"""Regression cases for strict formats and preserving revised structured answers."""
import pytest

from app.agent.conversation import ConversationService
from app.agent.response_guidance import brief_guidance, exceeds_brief_request, inherited_brief_request, word_limit
from app.answers import brief_output_requested
from app.memory.database import MemoryDatabase
from app.models.base import ModelResponse
from app.response_constraints import compact_binary_answer
from tests.test_conversation import RecordingModel


@pytest.mark.parametrize('instruction,maximum', [('In exactly three words: name the colour.', 3),
    ('Answer in under ten words.', 9), ('Use at most twenty words.', 20), ('Within 12 words, explain.', 12),
    ('Exactly two words, please.', 2), ('Answer in under 1 word.', 0)])
def test_numeric_and_spelled_word_contracts(instruction, maximum):
    assert word_limit(instruction) == maximum and brief_output_requested(instruction)
    assert brief_guidance(instruction)


def test_exact_count_rejects_underlength_answers_and_preserves_followup_contract():
    question = 'In exactly three words: what should I call the project?'
    assert exceeds_brief_request('Birch', question)
    assert not exceeds_brief_request('Call it Birch.', question)
    assert exceeds_brief_request('You can call it Birch.', question)
    history = [{'role': 'user', 'content': question}, {'role': 'assistant', 'content': 'Call it Birch.'},
               {'role': 'user', 'content': 'the same without the verb'}]
    assert 'exactly 3 words' in inherited_brief_request(history[-1]['content'], history, followup=True)
    assert word_limit('Discuss the phrase three words and its history.') is None


def test_binary_compaction_respects_explanations_and_conflicting_drafts():
    question = 'Does the conclusion follow? Answer yes or no.'
    assert compact_binary_answer('Yes. The premises imply the conclusion.', question) == 'Yes.'
    assert compact_binary_answer('No, that conclusion is unsupported.', question) == 'No.'
    assert compact_binary_answer('Yes, but the final answer is no.', question).startswith('Yes,')
    assert compact_binary_answer('Not established.', question) == 'Not established.'
    assert compact_binary_answer('No, unless a required condition changes.', question).startswith('No, unless')
    assert compact_binary_answer('No, because the sets can differ.', question + ' Explain why.').startswith('No,')
    assert exceeds_brief_request('No. ' + 'Because ' * 30, question)


def test_a_correct_binary_answer_is_not_padded_or_sent_for_another_model_call(tmp_path):
    memory = MemoryDatabase(tmp_path / 'chat.db')
    memory.initialize()
    model = RecordingModel()
    chat = ConversationService(model, memory, 'binary')
    response = ModelResponse('Yes. ' + 'The inclusion holds transitively. ' * 10, 'recorder', 'recorder', finish_reason='stop')
    answer, _ = chat._finish(response.content, response, 'Does inclusion hold? Answer yes or no.', [], model, set(), None)
    assert answer == 'Yes.' and not model.calls


def test_short_format_repairs_the_complete_answer_without_truncating_it(tmp_path):
    class Rewriter(RecordingModel):
        def generate(self, messages, **kwargs):
            self.calls.append(list(messages))
            return ModelResponse('Call it Alder.', 'recorder', 'recorder', finish_reason='stop')
    memory = MemoryDatabase(tmp_path / 'short.db')
    memory.initialize()
    model = Rewriter()
    chat = ConversationService(model, memory, 'short')
    response = ModelResponse('Alder', 'recorder', 'recorder', finish_reason='stop')
    answer, _ = chat._finish(response.content, response, 'In exactly three words: suggest a project name.', [], model, set(), None)
    assert answer == 'Call it Alder.' and len(model.calls) == 1
    assert 'defining name or value' in model.calls[0][-1].content


@pytest.mark.parametrize('repaired', [True, False])
def test_exact_format_gets_bounded_count_feedback_and_never_a_false_complete_label(tmp_path, repaired):
    class ContractionModel(RecordingModel):
        def generate(self, messages, **kwargs):
            self.calls.append(list(messages))
            answer = 'It is Alder.' if repaired and len(self.calls) == 2 else "It's Alder."
            return ModelResponse(answer, 'recorder', 'recorder', finish_reason='stop')
    memory = MemoryDatabase(tmp_path / 'contraction.db')
    memory.initialize()
    model = ContractionModel()
    chat = ConversationService(model, memory, 'contraction')
    original = ModelResponse("It's Alder.", 'recorder', 'recorder', finish_reason='stop')
    answer, _ = chat._finish(original.content, original, 'In exactly three words: name the project.', [], model, set(), None)
    assert len(model.calls) == 2 and 'draft has 2 words' in model.calls[1][-1].content
    assert chat.last_assessment.complete is repaired
    assert answer == ('It is Alder.' if repaired else "It's Alder.")


@pytest.mark.parametrize('question', ['Remove paint and add glue. What is on the list now?',
                                     'Rewrite that list without paint; add glue.',
                                     'Repeat the complete revised list.'])
def test_revised_lists_retain_unchanged_items_through_the_whole_chat_path(tmp_path, question):
    prior = '- Bolts\n- Nuts\n- Washers\n- Screws\n- Paint'
    revised = '- Bolts\n- Nuts\n- Washers\n- Screws\n- Glue\n\nPaint was removed and glue was added to the list.'
    class RevisedList(RecordingModel):
        def generate(self, messages, **kwargs):
            self.calls.append(list(messages))
            return ModelResponse(revised, 'recorder', 'recorder', finish_reason='stop')
    memory = MemoryDatabase(tmp_path / 'list.db')
    memory.initialize()
    memory.record_message('list', 'user', 'My supplies list: bolts, nuts, washers, screws, paint.')
    memory.record_message('list', 'assistant', prior)
    answer = ConversationService(RevisedList(), memory, 'list').reply(question)
    assert answer == revised
