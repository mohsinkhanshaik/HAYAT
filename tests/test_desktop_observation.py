"""Desktop guards reject stale evidence without exercising the owner's desktop."""
import json
import threading
from types import SimpleNamespace

from PIL import Image
import pytest

from app.agent.planner import Planner
from app.agent.planner import PlanStep
from app.agent.executor import Executor
from app.models.base import ModelProvider, ModelResponse
from app.safety.permissions import PermissionManager
from app.tools.computer import ComputerTools, COMPUTER_TOOL_NAMES, direct_computer_plan, register_computer_tools
from app.tools.registry import ToolRegistry


class Backend:
    KEYBOARD_KEYS = ['ctrl', 'shift', 'alt', 'a', 's', 'enter', 'tab']
    FAILSAFE = True

    def __init__(self):
        self.image = Image.new('RGB', (40, 30), 'white')
        self.pointer = (10, 10)
        self.calls = []

    def screenshot(self):
        return self.image.copy()

    def size(self):
        return SimpleNamespace(width=self.image.width, height=self.image.height)

    def position(self):
        return SimpleNamespace(x=self.pointer[0], y=self.pointer[1])

    def moveTo(self, x, y, **kwargs):
        self.calls.append(('move', x, y))
        self.pointer = (x, y)

    def click(self, **kwargs):
        self.calls.append(('click', kwargs))

    def press(self, key):
        self.calls.append(('press', key))

    def hotkey(self, *keys):
        self.calls.append(('hotkey', keys))

    def scroll(self, clicks):
        self.calls.append(('scroll', clicks))


@pytest.fixture
def computer(tmp_path):
    tool = ComputerTools.__new__(ComputerTools)
    tool.permissions = PermissionManager({'default': 'ALLOW'}, tmp_path)
    tool._pyautogui = Backend()
    tool._error = ''
    tool._observations = {}
    tool._observation_lock = threading.RLock()
    tool._active_window = lambda: {'available': True, 'handle': 7, 'title': 'Test window'}
    return tool


def test_observed_click_is_single_use_and_screenshot_evidence_is_distinct(computer):
    first, second = computer.observe(), computer.observe()
    assert first['path'] != second['path'] and first['observation_id'] != second['observation_id']
    assert not first['input_sent'] and computer._pyautogui.calls == []
    result = computer.click_observed(first['observation_id'], 20, 15)
    assert result['observation_checked'] and result['pointer_verified']
    assert [call[0] for call in computer._pyautogui.calls] == ['move', 'click']
    with pytest.raises(ValueError, match='already used'):
        computer.click_observed(first['observation_id'], 20, 15)
    assert len(computer._pyautogui.calls) == 2


@pytest.mark.parametrize('change', ['pixels', 'geometry', 'foreground', 'expired', 'unknown'])
def test_changed_or_stale_observation_never_clicks(computer, change):
    capture = computer.observe()
    token = capture['observation_id']
    if change == 'pixels':
        computer._pyautogui.image.putpixel((1, 1), (255, 0, 0))
    elif change == 'geometry':
        computer._pyautogui.image = Image.new('RGB', (80, 30), 'white')
    elif change == 'foreground':
        computer._active_window = lambda: {'available': True, 'handle': 9, 'title': 'Unexpected popup'}
    elif change == 'expired':
        computer._observations[token]['captured'] -= 31
    else:
        token = 'invented-token'
    with pytest.raises(ValueError):
        computer.click_observed(token, 20, 15)
    assert computer._pyautogui.calls == []


def test_observation_rejects_focus_change_during_capture(computer):
    windows = iter([{'handle': 7}, {'handle': 8}])
    computer._active_window = lambda: next(windows)
    with pytest.raises(ValueError, match='foreground window changed'):
        computer.observe()
    assert not computer._observations and not computer._pyautogui.calls


def test_screenshot_coordinates_must_match_input_geometry(computer):
    computer._pyautogui.size = lambda: SimpleNamespace(width=80, height=30)
    with pytest.raises(ValueError, match='coordinates'):
        computer.observe()
    assert not computer._observations


def test_before_and_after_screenshots_do_not_overwrite(computer):
    from pathlib import Path
    first = computer.screenshot()
    computer._pyautogui.image.putpixel((1, 1), (255, 0, 0))
    second = computer.screenshot()
    assert first['path'] != second['path']
    assert Path(first['path']).read_bytes() != Path(second['path']).read_bytes()


@pytest.mark.parametrize('operation', [
    lambda tool: tool.press_key('not-a-key'),
    lambda tool: tool.hotkey([]),
    lambda tool: tool.hotkey(['ctrl', 'unknown']),
    lambda tool: tool.hotkey(['ctrl'] * 7),
    lambda tool: tool.scroll(True),
    lambda tool: tool.scroll(101),
    lambda tool: tool.scroll(0),
])
def test_invalid_keyboard_and_scroll_requests_send_no_input(computer, operation):
    with pytest.raises(ValueError):
        operation(computer)
    assert computer._pyautogui.calls == []


def test_valid_shortcuts_and_scroll_keep_observation_requirement(computer):
    assert 'observe' in computer.hotkey(['ctrl', 's'])['verification']
    computer.press_key('tab')
    computer.scroll(-3)
    assert computer._pyautogui.calls == [('hotkey', ('ctrl', 's')), ('press', 'tab'), ('scroll', -3)]


@pytest.mark.parametrize('text,tool,args', [
    ('Scroll down 5 clicks', 'computer_scroll', {'clicks': -5}),
    ('Press ctrl+shift+s', 'computer_hotkey', {'keys': ['ctrl', 'shift', 's']}),
    ('Press tab', 'computer_press_key', {'key': 'tab'}),
])
def test_direct_keyboard_actions_observe_before_after_and_never_retry(text, tool, args):
    plan = direct_computer_plan(text, COMPUTER_TOOL_NAMES)
    assert [step['tool'] for step in plan] == ['computer_screenshot', tool, 'computer_screenshot']
    assert plan[1]['arguments'] == args and all(step['max_retries'] == 0 for step in plan)


def test_guarded_input_still_obeys_registry_permission_policy(tmp_path, computer):
    registry = ToolRegistry(PermissionManager({'default': 'ALLOW', 'permissions': {'computer_input': 'DENY'}}, tmp_path))
    tools = register_computer_tools(registry)
    tools._pyautogui = computer._pyautogui
    tools._active_window = computer._active_window
    token = tools.observe()['observation_id']
    result = registry.execute('computer_click_observed', {'observation_id': token, 'x': 20, 'y': 15})
    assert not result.success and computer._pyautogui.calls == []
    assert token in tools._observations


def test_model_desktop_plan_cannot_enable_blind_retries(tmp_path):
    registry = ToolRegistry(PermissionManager({'default': 'ALLOW'}, tmp_path))
    register_computer_tools(registry)

    class Model(ModelProvider):
        name = 'fake'

        def list_models(self):
            return ['fake']

        def generate(self, messages, **kwargs):
            prompt = json.loads(messages[-1].content)
            assert any('no automatic retries' in rule for rule in prompt['rules'])
            return ModelResponse(json.dumps({'steps': [
                {'tool': 'computer_screenshot', 'arguments': {}, 'max_retries': 2},
                {'tool': 'computer_click', 'arguments': {'x': 20, 'y': 15}, 'max_retries': 3},
                {'tool': 'computer_screenshot', 'arguments': {}, 'max_retries': 2},
            ]}), 'fake', 'fake')

    plan = Planner(registry, Model()).create_plan('Click the desktop toolbar and inspect the result')
    assert plan[1].max_retries == 0
    assert plan[0].max_retries == 2


def test_direct_typing_preserves_punctuation_observes_and_cannot_duplicate_input(tmp_path):
    registry = ToolRegistry(PermissionManager({'default': 'ALLOW'}, tmp_path))
    register_computer_tools(registry)
    plan = Planner(registry).create_plan('Type: Hello...')
    assert [step.tool for step in plan] == ['computer_screenshot', 'computer_type', 'computer_screenshot']
    assert plan[1].arguments == {'text': 'Hello...'} and plan[1].max_retries == 0


def test_executor_uses_only_verified_desktop_tokens_for_guarded_click(tmp_path, computer):
    registry = ToolRegistry(PermissionManager({'default': 'ALLOW'}, tmp_path))
    tools = register_computer_tools(registry)
    tools._pyautogui, tools._active_window = computer._pyautogui, computer._active_window
    captured = registry.execute('computer_observe', {})
    assert captured.success
    history = [{'tool': 'computer_observe', 'result': captured.to_dict(), 'verification': {'passed': True}}]
    step = PlanStep('Click observed target', 'computer_click_observed',
                    {'observation_id': '{{desktop_observation_id}}', 'x': 20, 'y': 15}, max_retries=0)
    result = Executor(registry).execute(step, history)
    assert result.success and result.output['observation_id'] == captured.output['observation_id']
    assert computer._pyautogui.calls[-1][0] == 'click'


@pytest.mark.parametrize('history', [
    [],
    [{'tool': 'web_search', 'result': {'success': True, 'output': {'observation_id': 'fake'}}, 'verification': {'passed': True}}],
    [{'tool': 'computer_observe', 'result': {'success': True, 'output': {'observation_id': 'fake'}}, 'verification': {'passed': False}}],
    [{'tool': 'computer_observe', 'result': {'success': False}, 'verification': {'passed': True}}],
])
def test_executor_cannot_invent_tokens_from_missing_failed_or_unrelated_evidence(tmp_path, history):
    with pytest.raises(ValueError):
        Executor(ToolRegistry(PermissionManager({'default': 'ALLOW'}, tmp_path)))._resolve(
            '{{desktop_observation_id}}', history)
