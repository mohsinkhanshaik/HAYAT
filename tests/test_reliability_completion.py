"""Failures and stale success records must remain visible through the real component paths."""
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import time

import pytest

from app.dual_brain import DualBrain
from app.executive import ExecutiveCortex
from app.invention_evidence import audit_project
from app.jobs.center import JobCenter
from app.models.base import ModelResponse
from tests.test_executive_invention import ScriptedModel, accepted, bench, candidate
from tests.test_jobs import posting
from tests.test_live_cameras_upgrade import CAMERA, card


def test_decoder_failure_tries_the_same_cameras_still_before_another_camera(shared_tk):
    preview = card(shared_tk, {**CAMERA, 'has_still': True})
    try:
        preview._publish({'kind': 'error', 'error': 'libVLC could not decode this video'})
        preview._drain()
        assert preview.camera['event_id'] == CAMERA['event_id']
        assert preview._still_fallback and preview._launch_timer is not None
        assert 'latest still image' in preview.detail.get()
    finally:
        preview.destroy()


def test_working_still_fallback_can_retry_original_video(shared_tk, tmp_path):
    from PIL import Image
    picture = tmp_path / 'frame.jpg'
    Image.new('RGB', (80, 60), '#456789').save(picture)
    preview = card(shared_tk, {**CAMERA, 'mode': 'still'})
    try:
        preview._still_fallback = True
        preview._show_still({'path': str(picture)})
        assert str(preview.retry_button['state']) == 'normal'
        assert preview.retry_button['text'] == 'Retry video'
        generation = preview._generation
        preview.retry_button.invoke()
        assert not preview._ready and not preview._still_fallback
        assert preview._generation > generation and preview._retry_generation == preview._generation
        assert preview._launch_timer is not None
    finally:
        preview.destroy()


def test_paused_connection_keeps_its_camera_and_resumes_with_fresh_time_budget(shared_tk):
    from app.gui_video_player import GIVE_UP_SECONDS
    preview = card(shared_tk, {**CAMERA, 'has_still': True})
    try:
        preview._launched_at = time.monotonic() - GIVE_UP_SECONDS - 5
        preview.control('pause')
        preview._publish({'kind': 'state', 'seconds': 0, 'playing': False, 'reconnecting': 0, 'paused': True})
        preview._drain()
        assert not preview._still_fallback and preview._launch_timer is None
        preview.control('play')
        assert time.monotonic() - preview._launched_at < 1
    finally:
        preview.destroy()


def test_ready_without_frames_never_resets_failed_camera_budget_or_claims_live(shared_tk):
    preview = card(shared_tk, CAMERA)
    try:
        preview._auto_advances = 1
        preview._publish({'kind': 'ready', 'audio': False})
        preview._drain()
        assert preview._auto_advances == 1
        assert preview.badge['text'] == 'BUFFERING'
        preview._publish({'kind': 'state', 'seconds': 0, 'playing': True, 'width': 640, 'height': 480, 'frames': 0})
        preview._drain()
        assert not preview._shown_frame and preview.badge['text'] == 'BUFFERING'
        assert 'Playing' not in preview.detail.get()
        preview._publish({'kind': 'state', 'seconds': 1, 'playing': True, 'width': 640, 'height': 480, 'frames': 2})
        preview._drain()
        assert preview._shown_frame and preview._auto_advances == 0
        assert 'LIVE' in preview.badge['text']
    finally:
        preview.destroy()


def test_request_play_resumes_still_refresh_instead_of_sending_a_video_command(shared_tk, monkeypatch):
    preview = card(shared_tk, {**CAMERA, 'mode': 'still'})
    refreshes = []
    try:
        preview._ready = True
        monkeypatch.setattr(preview, '_refresh_still', lambda: refreshes.append(True))
        preview.request_play()
        assert refreshes and preview._playing and preview._commands.empty()
    finally:
        preview.destroy()


def test_camera_picture_queue_keeps_latest_frame_and_paints_it_on_tk(shared_tk):
    import base64
    import io
    from PIL import Image, ImageTk
    preview = card(shared_tk, CAMERA)
    try:
        for colour in ('red', 'green', 'blue'):
            stream = io.BytesIO()
            Image.new('RGB', (32, 24), colour).save(stream, format='JPEG')
            preview._publish({'kind': 'picture', 'jpeg': base64.b64encode(stream.getvalue()).decode()}, preview._generation)
        assert preview._pictures.qsize() == 1
        preview._drain()
        image = ImageTk.getimage(preview._photo)
        pixel = image.getpixel((0, 0))
        assert pixel[2] > 240 and pixel[0] < 10
        assert preview._still_label is not None
        before = preview._photo
        preview._publish({'kind': 'picture', 'jpeg': 'invalid'}, preview._generation - 1)
        preview._drain()
        assert preview._photo is before  # a replaced player's late picture cannot cover the new one
    finally:
        preview.destroy()


def test_player_shutdown_stops_real_launcher_and_decoder_children():
    import subprocess
    import sys
    import psutil
    from app.gui_video_player import kill_player_process
    process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    children = []
    try:
        root = psutil.Process(process.pid)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            children = root.children(recursive=True)
            if children or sys.platform != 'win32':
                break
            time.sleep(.02)
        kill_player_process(process)
        process.wait(timeout=5)
        _gone, alive = psutil.wait_procs(children, timeout=5)
        assert not alive, 'a decoder child survived the closed player'
    finally:
        for child in children:
            if child.is_running():
                child.kill()
        if process.poll() is None:
            process.kill()


@pytest.mark.parametrize('expiry', ['', None, 'unknown', '2020-01-01T00:00:00', '2020-01-01T00:00:00Z'])
def test_camera_expiry_metadata_never_crashes_the_player(expiry):
    from app.gui_video_player import signed_address_expired
    assert signed_address_expired({'expires_at': expiry}) is bool(expiry and expiry.startswith('2020'))


def test_review_keeps_existing_goal_contract_and_supplemental_criteria():
    cortex = ExecutiveCortex()
    frame = cortex.prepare('Choose a sensor', ['Cost <= 17 units'], ['Test noise before deployment'])
    model = ScriptedModel('{"verdict":"revise","issues":["Keep the budget"]}', 'Test the cheaper sensor first.')
    result = DualBrain(cortex).review('Choose a sensor', ModelResponse('Buy the untested sensor.', 'fixture', 'test'), model,
                                     frame=frame, constraints=['No subscription'], criteria=['Give one next test'])
    for call in model.calls:
        payload = json.loads(call[-1].content)
        assert payload['constraints'] == ['Cost <= 17 units', 'No subscription']
        assert payload['criteria'] == ['Test noise before deployment', 'Give one next test']
    assert result.report['outcome'] == 'revised_unverified' and not result.report['verified']


@pytest.mark.parametrize('draft', [ModelResponse('', 'fixture', 'test'),
                                 ModelResponse('An unfinished answer', 'fixture', 'test', finish_reason='length')])
def test_critic_cannot_approve_an_empty_or_truncated_answer(draft):
    model = ScriptedModel(accepted(), 'A complete answer.')
    result = DualBrain(ExecutiveCortex()).review('Explain the result.', draft, model)
    assert result.report['revised'] and result.response.content == 'A complete answer.'
    assert result.report['contract_issues'] and result.report['verdict'] == 'revise'


def test_critic_cannot_approve_broken_json_contract():
    schema = {'type': 'object', 'properties': {'answer': {'type': 'integer'}}, 'required': ['answer']}
    model = ScriptedModel(accepted(), '{"answer":17}')
    result = DualBrain(ExecutiveCortex()).review('Return an answer.', ModelResponse('{"answer":"unknown"}', 'fixture', 'test'),
                                                model, response_schema=schema)
    assert result.report['revised'] and json.loads(result.response.content) == {'answer': 17}
    assert json.loads(model.calls[0][-1].content)['output_contract_issues']


def test_failed_repair_remains_unresolved_despite_critic_approval():
    draft = ModelResponse('Unfinished', 'fixture', 'test', finish_reason='length')
    model = ScriptedModel(accepted(), ModelResponse('Still unfinished', 'fixture', 'test', finish_reason='length'))
    result = DualBrain(ExecutiveCortex()).review('Explain the result.', draft, model)
    assert result.report['state'] == 'revision_failed' and result.report['outcome'] == 'unresolved'
    assert result.response is draft and not result.report['verified']


def test_invention_audit_replays_formula_instead_of_trusting_saved_passes(tmp_path):
    workbench = bench(tmp_path, ScriptedModel(candidate(), accepted()))
    project = workbench.create('Double the inputs', tests=[{'inputs': {'x': 2}, 'expected': 4}])
    done = workbench.run(project['id'], cycles=1, research=False, work_out=False)
    assert audit_project(done)['numeric_cases_passed']
    done['best']['candidate']['formula'] = 'x * 99'
    before = deepcopy(done)
    audit = audit_project(done)
    assert not audit['numeric_cases_passed'] and audit['stage'] == 'NUMERIC_CASES_FAILED'
    assert audit['numeric_replay']['measurement']['passed'] == 0
    assert not audit['numeric_replay']['record_matches']
    assert any('fresh replay' in gap for gap in audit['gaps'])
    assert done == before


@pytest.mark.parametrize('formula', ['', '__import__("os").system("echo invalid")', 'unknown_constant * x'])
def test_invention_audit_never_approves_missing_unsafe_or_unbound_formula(tmp_path, formula):
    workbench = bench(tmp_path, None)
    project = workbench.create('Double inputs', tests=[{'inputs': {'x': 2}, 'expected': 4}])
    project['best'] = {'candidate': {'formula': formula}, 'measurement': {'passed': 1, 'total': 1}}
    audit = audit_project(project)
    assert not audit['numeric_cases_passed']
    assert not audit['physical_validation_verified'] and not audit['novelty_verified']


@pytest.fixture
def jobs(tmp_path):
    center = JobCenter(tmp_path / 'jobs', object())
    center.store.upsert([posting(key='remoteok:expiry', expires_at=(datetime.now(UTC) - timedelta(days=1)).isoformat()),
                         posting(key='remoteok:current')])
    center.rescore()
    # Simulate a deadline passing while HAYAT stays open: the fit cache still says strong.
    for number in (1, 2):
        row = center.store.job(number)
        center.store.set_match(number, row['reading'], {**row['match'], 'score': 95 if number == 1 else 75,
                                                       'verdict': 'strong', 'blocked': []})
    return center


def test_jobs_exclude_expired_cached_match_and_notification_without_research(jobs):
    assert [row['number'] for row in jobs.matches()['matches']] == [2]
    assert [row['number'] for row in jobs.store.unnotified(60)] == [2]
    assert [row['number'] for row in jobs.store.iter_matches(min_score=60, include_blocked=True)] == [2]
    counts = jobs.store.counts()
    assert counts['verdict_strong'] == 1 and counts['verdict_not eligible'] == 1


def test_jobs_explicit_blocked_view_and_detail_explain_current_deadline(jobs):
    matches = jobs.matches(include_blocked=True)['matches']
    assert [row['number'] for row in matches] == [2, 1]
    expired = matches[1]
    assert expired['score'] <= 25 and expired['verdict'] == 'not eligible'
    assert expired['listing_evidence']['state'] == 'expired'
    detail = jobs.detail(1)
    assert detail['listing_evidence']['state'] == 'expired'
    assert detail['match']['score'] == detail['score'] <= 25
    from app.jobs.tools import report_lines
    assert 'Listing evidence: expired' in '\n'.join(report_lines(detail))


def test_job_database_connections_close_and_partial_writes_roll_back(jobs):
    import sqlite3
    with jobs.store._db() as connection:
        connection.execute('SELECT COUNT(*) FROM jobs').fetchone()
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):
        connection.execute('SELECT 1')
    before = jobs.store.counts()
    with pytest.raises(RuntimeError, match='abort'):
        with jobs.store._db() as connection:
            connection.execute('DELETE FROM jobs')
            raise RuntimeError('abort')
    assert jobs.store.counts() == before
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):
        connection.execute('SELECT 1')


@pytest.mark.parametrize('days,state', [(1, 'recently_seen'), (10, 'stale'), (-1, 'unknown')])
def test_listing_evidence_never_calls_recent_or_future_observation_verified_open(days, state):
    from app.jobs.freshness import listing_evidence
    now = datetime.now(UTC)
    evidence = listing_evidence({'last_seen': (now - timedelta(days=days)).isoformat()}, now)
    assert evidence['state'] == state
    assert 'not verified open' in evidence['basis']


def test_doctor_checks_jobs_invention_and_reasoning_without_running_model_or_network(jobs, tmp_path):
    from types import SimpleNamespace
    from app.doctor import _invention_check, _jobs_check, _reasoning_check
    workbench = bench(tmp_path / 'invention', None)
    runtime = SimpleNamespace(jobs=jobs, invention=workbench, executive=workbench.executive, dual_brain=workbench.dual_brain)
    assert _jobs_check(runtime)['available']
    assert _invention_check(runtime)['healthy']
    assert _reasoning_check(runtime)['available']
    runtime.jobs = runtime.invention = runtime.executive = runtime.dual_brain = None
    assert not _jobs_check(runtime)['available']
    assert not _invention_check(runtime)['available']
    assert not _reasoning_check(runtime)['available']


@pytest.mark.skipif(os.environ.get('HAYAT_COMPONENT_SNAPSHOT') != '1', reason='opt-in installed-component check')
def test_installed_component_snapshot_uses_isolated_owner_data(monkeypatch):
    from app.doctor import inspect_runtime
    from app.launcher import folder_runtime
    from app.runtime import build_runtime
    with folder_runtime() as environment:
        for name, value in environment.items():
            monkeypatch.setenv(name, value)
        runtime = build_runtime(use_model=True, start_background_services=False)
        try:
            report = inspect_runtime(runtime)
            Path('.test-runs/components-nov07.json').write_text(json.dumps(report, indent=2, default=str), encoding='utf-8')
            for area in ('camera_playback', 'jobs', 'invention', 'reasoning', 'model', 'memory', 'knowledge', 'cost_policy'):
                assert report['checks'][area]['status'] == 'ready', report['checks'][area]
        finally:
            runtime.close()
