from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.gui import HayatGUI
from app.gui_video import VideoEditorCard
from app.session import SessionState
from app.video_workflow import VIDEO_TOOL_NAMES, direct_video_plan


@pytest.mark.parametrize('utterance,tool,arguments', [
    ('trim the second clip from 5 to 20 seconds', 'edit_video_clip',
     {'path': 'D:/AI/second.mp4', 'start_seconds': 5, 'end_seconds': 20, 'enhance': False}),
    ('improve that video from 1:05 to 1:20 in square', 'edit_video_clip',
     {'path': 'D:/AI/first.mp4', 'start_seconds': 65, 'end_seconds': 80, 'aspect': 'square', 'enhance': True}),
    ('share the second clip', 'share_video_result', {'path': 'D:/AI/second.mp4'}),
    ('create a link for it', 'share_video_result', {'path': 'D:/AI/first.mp4'}),
])
def test_fast_editor_and_link_followups_resolve_verified_clip(utterance, tool, arguments):
    session = SessionState()
    session.remember_video_result('make_video_clips', {'path': 'D:/AI/source.mp4',
         'clips': [{'path': 'D:/AI/first.mp4'}, {'path': 'D:/AI/second.mp4'}]})
    plan = direct_video_plan(utterance, VIDEO_TOOL_NAMES, session)
    assert plan['tool'] == tool
    assert plan['arguments'] == arguments


@pytest.mark.parametrize('utterance', ['do not share that video', 'do not edit the clip',
                                     'share this video https://youtu.be/x', 'trim the second clip'])
def test_no_edits_or_shares_without_a_verified_file(utterance):
    assert direct_video_plan(utterance, VIDEO_TOOL_NAMES, SessionState()) is None


def test_editor_widget_exports_controls_and_links_latest_success(shared_tk):
    gui = SimpleNamespace(**{name: getattr(HayatGUI, name) for name in ('PANEL_2', 'WHITE', 'MUTED', 'LINE')})
    gui.runtime = SimpleNamespace(session=SessionState())
    editor = VideoEditorCard(shared_tk, gui, 'D:/AI/source.mp4', 12, 25)
    try:
        editor._run = Mock()
        editor.speed.set('1.5')
        editor.enhance.set(True)
        editor.export()
        tool, arguments = editor._run.call_args.args
        assert tool == 'edit_video_clip'
        assert arguments['start_seconds'] == 12
        assert arguments['end_seconds'] == 25
        assert arguments['speed'] == 1.5 and arguments['enhance']
        editor.result_path = 'D:/AI/export.mp4'
        editor.share()
        assert editor._run.call_args.args == ('share_video_result', {'path': 'D:/AI/export.mp4', 'title': ''})
        editor.start.set('bad text')
        editor._run.reset_mock()
        editor.export()
        editor._run.assert_not_called()
        assert 'Enter start' in editor.status.get()
    finally:
        editor.destroy()

