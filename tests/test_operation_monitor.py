"""Real lifecycle-driven observation, bounded memory and cancellation races."""
import threading
import time

from PIL import Image

from app.operation_monitor import OperationMonitor, desktop_operation


def event(kind, tool='computer_click', **extra):
    return {'event_type': kind, 'timestamp': '2026-10-24T04:00:00Z', 'sequence': 1,
            'task_id': 't', 'turn_id': 'r', 'payload': {'tool': tool, 'arguments': {'text': 'private canary'}}, **extra}


def wait_for(predicate):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    assert predicate()


def test_only_actual_desktop_operations_trigger_bounded_frames_and_no_arguments_retained():
    captures = []
    monitor = OperationMonitor(capture=lambda: captures.append(True) or Image.new('RGB', (1920, 1080)), interval=.1)
    try:
        monitor.visible(True)
        time.sleep(.15)
        assert not captures
        assert not monitor.receive(event('TOOL_STARTED', 'networking_save'))
        monitor.receive(event('TOOL_STARTED'))
        wait_for(lambda: monitor.snapshot()['has_frame'])
        state = monitor.snapshot()
        assert state['frame'].size == (1280, 720)
        assert state['active'] == 1 and 'private canary' not in str(state['events'])
        assert monitor.snapshot(state['frame_id'])['frame'] is None
        monitor.receive(event('TOOL_COMPLETED'))
        wait_for(lambda: monitor.snapshot()['active'] == 0)
        for _ in range(150):
            monitor.receive(event('TOOL_FAILED'))
        assert len(monitor.snapshot()['events']) == 100
    finally:
        monitor.close()
    assert not monitor._worker.is_alive() and monitor.snapshot()['frame'] is None


def test_pause_discards_capture_that_finishes_after_pause():
    entered, release = threading.Event(), threading.Event()

    def capture():
        entered.set()
        assert release.wait(2)
        return Image.new('RGB', (200, 100))

    monitor = OperationMonitor(capture=capture, interval=.1)
    try:
        monitor.visible(True)
        monitor.receive(event('TOOL_STARTED'))
        assert entered.wait(2)
        monitor.pause(True)
        release.set()
        time.sleep(.15)
        assert not monitor.snapshot()['has_frame']
        assert not monitor.observe_once()
    finally:
        release.set()
        monitor.close()


def test_privacy_and_emergency_clear_frames_and_do_not_resume_old_work():
    allowed = [True]
    monitor = OperationMonitor(capture=lambda: Image.new('RGB', (200, 100)), allowed=lambda: allowed[0], interval=.1)
    try:
        monitor.visible(True)
        monitor.receive(event('TOOL_STARTED'))
        wait_for(lambda: monitor.snapshot()['has_frame'])
        allowed[0] = False
        state = monitor.snapshot()
        assert state['blocked'] and not state['has_frame'] and not state['active']
        allowed[0] = True
        monitor.receive(event('TOOL_COMPLETED'))
        time.sleep(.15)
        assert not monitor.snapshot()['has_frame']
        assert monitor.observe_once()
        wait_for(lambda: monitor.snapshot()['has_frame'])
    finally:
        monitor.close()


def test_same_tool_concurrency_and_hidden_view_capture_are_handled():
    calls = []
    monitor = OperationMonitor(capture=lambda: calls.append(True) or Image.new('RGB', (200, 100)), interval=.1)
    try:
        monitor.receive(event('TOOL_STARTED'))
        monitor.receive(event('TOOL_STARTED'))
        monitor.receive(event('TOOL_COMPLETED'))
        assert monitor.snapshot()['active'] == 1
        time.sleep(.15)
        assert not calls
        monitor.visible(True)
        wait_for(lambda: bool(calls))
        monitor.visible(False)
        assert not monitor.snapshot()['has_frame']
        assert desktop_operation('ui_click') and desktop_operation('browser_read_page')
    finally:
        monitor.close()


def test_failed_capture_removes_old_image_and_omits_sensitive_error_text():
    def capture():
        raise OSError('private window title and password canary')
    monitor = OperationMonitor(capture=capture, interval=.1)
    try:
        monitor.visible(True)
        assert monitor.observe_once()
        wait_for(lambda: bool(monitor.snapshot()['error']))
        state = monitor.snapshot()
        assert state['error'] == 'OSError' and not state['has_frame']
        assert 'canary' not in str(state)
    finally:
        monitor.close()


def test_stop_resume_during_capture_rejects_old_frame_even_after_permission_returns():
    entered, release = threading.Event(), threading.Event()
    epoch = [0]

    def capture():
        entered.set()
        assert release.wait(2)
        return Image.new('RGB', (200, 100))

    monitor = OperationMonitor(capture=capture, version=lambda: epoch[0], interval=.1)
    try:
        monitor.visible(True)
        monitor.receive(event('TOOL_STARTED'))
        assert entered.wait(2)
        epoch[0] += 2  # Both halt and resume happened during the blocking native call.
        release.set()
        time.sleep(.2)
        assert not monitor.snapshot()['has_frame']
        assert monitor.snapshot()['active'] == 0
        assert monitor.observe_once()
        wait_for(lambda: monitor.snapshot()['has_frame'])
    finally:
        release.set()
        monitor.close()


def test_lost_completion_is_bounded_and_correlated_calls_complete_independently():
    monitor = OperationMonitor(interval=.1, active_ttl=.12)
    try:
        for number in range(70):
            item = event('TOOL_STARTED')
            item['payload']['call_id'] = f'call-{number}'
            monitor.receive(item)
        assert monitor.snapshot()['active'] == 64
        assert monitor.snapshot()['dropped_starts'] == 6
        item = event('TOOL_COMPLETED')
        item['payload']['call_id'] = 'call-2'
        monitor.receive(item)
        monitor.receive(item)  # Duplicate completion must not finish another invocation.
        assert monitor.snapshot()['active'] == 63
        wait_for(lambda: monitor.snapshot()['active'] == 0)
        assert monitor.snapshot()['expired'] == 63
    finally:
        monitor.close()


def test_privacy_transition_event_discards_inflight_frame_even_after_unblocking():
    entered, release = threading.Event(), threading.Event()
    def capture():
        entered.set()
        assert release.wait(2)
        return Image.new('RGB', (200, 100))
    monitor = OperationMonitor(capture=capture, interval=.1)
    try:
        monitor.visible(True)
        monitor.receive(event('TOOL_STARTED'))
        assert entered.wait(2)
        monitor.invalidate()
        release.set()
        time.sleep(.2)
        assert not monitor.snapshot()['has_frame']
        assert monitor.snapshot()['active'] == 0
    finally:
        release.set()
        monitor.close()
