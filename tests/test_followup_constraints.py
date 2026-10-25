import pytest

from app.agent.response_guidance import exceeds_brief_request, inherited_brief_request


def history(previous):
    return [{'role': 'user', 'content': previous}, {'role': 'assistant', 'content': '1. A. 2. B.'},
            {'role': 'user', 'content': 'do both'}]


@pytest.mark.parametrize('instruction,expected', [
    ('Name the selected tasks in one sentence.', 'Reply in one sentence.'),
    ('Explain the choice in under 10 words.', 'Answer in at most 9 words.'),
    ('Give me a short answer.', 'Give a short answer.')])
def test_continuation_carries_only_the_canonical_length_constraint(instruction, expected):
    resolved = inherited_brief_request('do both', history(instruction), followup=True)
    assert resolved == 'do both\n' + expected
    assert inherited_brief_request('What is gravity?', history(instruction), followup=False) == 'What is gravity?'


def test_a_new_request_or_current_explicit_override_stops_inheritance():
    rows = history('Explain in one sentence.')
    assert inherited_brief_request('explain that in detail', rows, followup=True) == 'explain that in detail'
    assert inherited_brief_request('do both in under 50 words', rows, followup=True) == 'do both in under 50 words'
    rows[-1:-1] = [{'role': 'user', 'content': 'Now compare SQL databases.'},
                   {'role': 'assistant', 'content': 'A different topic.'}]
    assert inherited_brief_request('do both', rows, followup=True) == 'do both'


@pytest.mark.parametrize('answer,instruction,violates', [
    ('CedarNotes costs $40.99 once and works offline.', 'In one sentence.', False),
    ('Dr. Lee lives in the U.S. and uses local notes.', 'In one sentence.', False),
    ('Use CedarNotes. It works offline.', 'In one sentence.', True),
    ('Yes! It works offline.', 'In one sentence.', True),
    ('Use both Markdown export and SQLite backup.', 'In one sentence.', False),
    ('CedarNotes is your selected app. 🌲', 'In one sentence.', False),
    ('Only\none line', 'In one line.', True),
    ('Yes', 'In one word.', False), ('Yes indeed', 'In one word.', True),
    ('one two three', 'Answer in under 3 words.', True)])
def test_output_constraint_check_handles_sentences_lines_words_and_decimals(answer, instruction, violates):
    assert exceeds_brief_request(answer, instruction) is violates


def test_inherited_one_sentence_is_repaired_as_a_whole_answer(tmp_path):
    from app.agent.conversation import ConversationService
    from app.memory.database import MemoryDatabase
    from app.models.base import ModelResponse
    from tests.test_conversation import RecordingModel
    memory = MemoryDatabase(tmp_path / 'inherited.db')
    memory.initialize()
    memory.record_message('topic', 'user', 'Choose local notes and backups in one sentence.')
    memory.record_message('topic', 'assistant', '1. Notes. 2. Backups.')
    class Repaired(RecordingModel):
        def generate(self, messages, **kwargs):
            self.calls.append(list(messages))
            return ModelResponse('Use local notes. Also use backups.' if len(self.calls) == 1 else
                                 'Use local notes with backups.', 'fixture', self.name)
    model = Repaired()
    chat = ConversationService(model, memory, 'topic')
    assert chat.reply('do both') == 'Use local notes with backups.'
    assert len(model.calls) == 2
    assert any('one sentence only' in message.content for message in model.calls[0])


def test_missing_proposal_gets_one_question_without_a_model_or_invented_tasks(tmp_path):
    from app.agent.conversation import ConversationService
    from app.memory.database import MemoryDatabase
    memory = MemoryDatabase(tmp_path / 'missing.db')
    memory.initialize()
    chat = ConversationService(None, memory, 'empty')
    assert chat.reply('do both') == 'Which two tasks do you want me to do?'
    assert chat.last_assessment.complete
    assert [row['role'] for row in memory.conversation_history('empty')] == ['user', 'assistant']
