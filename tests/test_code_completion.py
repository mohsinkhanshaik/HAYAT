"""Generated programs must finish structurally before becoming chat answers."""
from __future__ import annotations

import pytest

from app.agent.conversation import ConversationService
from app.answers import assess
from app.code_completion import code_issue, merge_code_continuation
from app.memory.database import MemoryDatabase
from app.models.base import ModelError, ModelProvider, ModelResponse
from app.models.streaming import GenerationCancelled


REQUEST = (
    "Write exactly one complete Python program. Run ollama ps with subprocess.run "
    "and a 5-second timeout. Print stdout on success and print stderr on failure. "
    "Handle FileNotFoundError and subprocess.TimeoutExpired. Maximum 35 lines. No explanations. "
    "No markdown except one Python code block."
)
SIMPLE_REQUEST = "Write one complete Python program. No explanations. No markdown except one Python code block."


@pytest.mark.parametrize("answer", ["s[::-1]", "`s[::-1]`", "```python\ns[::-1]\n```"])
def test_one_liner_can_be_bare_inline_or_fenced(answer):
    assert code_issue(answer, "Give me a Python one-liner that reverses a string s.") == ("", "")


@pytest.mark.parametrize("answer", ['"hello"[::-1]', "Use reverse slicing.", "s[", '```python\ns = "hello"[::-1]\nprint(s)\n```',
                                     's = "hello"[::-1]; print(s)'])
def test_one_liner_still_checks_syntax_and_requested_variable(answer):
    assert code_issue(answer, "Give me a Python one-liner that reverses a string s.")[0]


class SequenceModel(ModelProvider):
    name = "sequence"

    def __init__(self, answers: list[str]):
        self.answers = iter(answers)
        self.calls = 0

    def list_models(self):
        return [self.name]

    def generate(self, messages, tools=None, temperature=None, response_schema=None, max_tokens=None):
        self.calls += 1
        value = next(self.answers)
        if isinstance(value, Exception):
            raise value
        return ModelResponse(value, self.name, self.name, finish_reason="stop")


def _chat(tmp_path, answers):
    memory = MemoryDatabase(tmp_path / "conversation.db")
    memory.initialize()
    model = SequenceModel(answers)
    return ConversationService(model, memory, "session-test"), memory, model


def test_open_or_invalid_python_block_is_not_complete():
    open_code = '```python\noutput = run_cmd("nvidia-smi'
    assert code_issue(open_code, REQUEST)[0] == "open_fence"
    assert not assess(open_code, question=REQUEST, finish_reason="stop").complete
    assert code_issue('```python\nprint(\n```', REQUEST)[0] == "invalid_python"
    assert code_issue("Ask me about those directly and I will take them one at a time.", REQUEST)[0] == "missing_program"


def test_explicit_subprocess_requirements_are_checked_without_execution():
    observed = '''```python
import subprocess, time
try:
    result = subprocess.run(["ollama", "ps"], timeout=5)
    if result.returncode == 0: print(result.stdout.decode())
except FileNotFoundError: print("missing")
except subprocess.TimeoutExpired: print("timeout")
```'''
    assert code_issue(observed, REQUEST)[0] == "uncaptured_output"
    wrong_stderr = '''```python
import subprocess, sys
try:
    result = subprocess.run(["ollama", "ps"], capture_output=True, text=True, timeout=5)
    if result.returncode == 0: print(result.stdout)
except FileNotFoundError as e: print(e, file=sys.stderr)
except subprocess.TimeoutExpired as e: print(e, file=sys.stderr)
```'''
    assert code_issue(wrong_stderr, REQUEST)[0] == "missing_stderr"
    wrong_branch = '''```python
import subprocess, sys
try:
    res = subprocess.run(["ollama", "ps"], capture_output=True, text=True, timeout=5)
    if res.returncode == 0: print(res.stdout); exit(0)
except FileNotFoundError as e: print(e)
except subprocess.TimeoutExpired as e: print(e.stderr)
```'''
    assert code_issue(wrong_branch, REQUEST)[0] == "missing_stderr"
    unguarded = '''```python
import subprocess
try:
    res = subprocess.run(["ollama", "ps"], capture_output=True, text=True, timeout=5)
    print(res.stdout)
    print(res.stderr)
except FileNotFoundError: print("missing")
except subprocess.TimeoutExpired: print("timeout")
```'''
    assert code_issue(unguarded, REQUEST)[0] == "missing_failure_branch"
    swapped = '''```python
import subprocess
try:
    res = subprocess.run(["ollama", "ps"], capture_output=True, text=True, timeout=5)
    if res.returncode == 0:
        print(res.stderr)
    else:
        print(res.stdout)
except FileNotFoundError: print("missing")
except subprocess.TimeoutExpired: print("timeout")
```'''
    assert code_issue(swapped, REQUEST)[0] == "missing_failure_branch"
    corrected = '''```python
import subprocess
try:
    result = subprocess.run(["ollama", "ps"], capture_output=True, text=True, timeout=5)
    if result.returncode == 0:
        print(result.stdout)
    else:
        print(result.stderr)
except FileNotFoundError:
    print("ollama missing")
except subprocess.TimeoutExpired:
    print("ollama timed out")
```'''
    assert code_issue(corrected, REQUEST) == ("", "")


def test_code_continuation_preserves_the_partial_line():
    first = '```python\noutput = run_cmd("nvidia-smi'
    addition = '")\nprint(output)\n```'
    assert merge_code_continuation(first, addition) == '```python\noutput = run_cmd("nvidia-smi")\nprint(output)\n```'


def test_short_code_continuation_can_complete_the_program(tmp_path):
    first = '```python\noutput = run_cmd("nvidia-smi'
    chat, memory, model = _chat(tmp_path, [first, '")\nprint(output)\n```'])
    answer = chat.reply(SIMPLE_REQUEST)
    assert 'run_cmd("nvidia-smi")' in answer
    assert answer.endswith("```")
    assert model.calls == 2
    assert memory.conversation_history("session-test")[-1]["content"] == answer


def test_bad_continuation_gets_one_fresh_program_attempt(tmp_path):
    first = '```python\noutput = run_cmd("nvidia-smi'
    fallback = "Ask me about those directly and I will take them one at a time."
    repaired = '```python\nimport subprocess\nprint(subprocess.run(["ollama", "ps"], capture_output=True).stdout)\n```'
    # Every bounded continuation wanders into prose; the one fresh attempt then succeeds.
    continuations = ConversationService.MAX_CONTINUATIONS
    chat, _, model = _chat(tmp_path, [first, *[fallback] * continuations, repaired])
    assert chat.reply(SIMPLE_REQUEST) == repaired
    assert model.calls == continuations + 2


def test_unrepaired_partial_code_is_a_failed_turn_not_a_saved_answer(tmp_path):
    first = '```python\noutput = run_cmd("nvidia-smi'
    fallback = "Ask me about those directly and I will take them one at a time."
    chat, memory, _ = _chat(tmp_path, [first, fallback, fallback, fallback, fallback])
    with pytest.raises(ModelError, match="could not complete"):
        chat.reply(SIMPLE_REQUEST)
    assert [row["role"] for row in memory.conversation_history("session-test")] == ["user"]


def test_cancellation_during_code_repair_stays_a_cancellation(tmp_path):
    chat, memory, _ = _chat(tmp_path, [
        '```python\nprint("unfinished', GenerationCancelled("cancelled"),
    ])
    with pytest.raises(GenerationCancelled):
        chat.reply(SIMPLE_REQUEST)
    assert [row["role"] for row in memory.conversation_history("session-test")] == ["user"]


def test_assistant_sends_program_request_through_completion_path(monkeypatch):
    from app.runtime import build_runtime

    runtime = build_runtime(use_model=False, start_background_services=False)
    calls = []
    try:
        monkeypatch.setattr(runtime.research, "recall_verified",
                            lambda *_args: pytest.fail("code request was answered from research recall"))
        monkeypatch.setattr(runtime.conversation, "reply",
                            lambda text, modes=None, *, capability="conversation":
                            (calls.append(capability), '```python\nprint("done")\n```')[1])
        reply = runtime.assistant.handle(REQUEST)
        assert calls == ["coding"]
        assert 'print("done")' in reply.display_response
    finally:
        runtime.close()
