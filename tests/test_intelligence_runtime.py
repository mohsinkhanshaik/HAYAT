"""The complete chat/runtime path uses the independent intelligence service."""
from app.runtime import build_runtime
from app.intelligence.live import LIVESTREAMS
from tests.test_intelligence_resources import ADDED_CAMERAS, ExtendedFetcher


def test_full_runtime_chat_uses_verified_feed_results_and_closes_monitor():
    runtime = build_runtime(use_model=False, start_background_services=False)
    original = runtime.intelligence.fetcher
    original.close()
    runtime.intelligence.fetcher = ExtendedFetcher()
    try:
        assert not runtime.intelligence.status()['running']
        updated = runtime.assistant.handle('Refresh intelligence feeds')
        assert 'earthquakes: ok' in updated.display_response
        answer = runtime.assistant.handle('Show Osiris status')
        assert 'Hayat intelligence' in answer.display_response
        assert f'{82 + len(LIVESTREAMS) + ADDED_CAMERAS} matching source items' in answer.display_response
        assert 'Test space bulletin' in answer.display_response
        aliases = runtime.registry.execute('osiris_status', {})
        assert aliases.success and aliases.output['intelligence']
        assert 'events' in aliases.output and 'books' not in aliases.output
        assessment = runtime.assistant.handle('What is happening around the world?')
        # Events and camera catalog entries are counted apart (same 82 + livestreams in all).
        assert f'11 event reports and {71 + len(LIVESTREAMS) + ADDED_CAMERAS} public camera entries match World' in assessment.display_response
        assert 'World coverage is partial' in assessment.display_response
        event = runtime.assistant.handle('Explain intelligence event earthquakes:q1')
        assert 'Magnitude: 4' in event.display_response
        assert 'distinct retained revisions' in event.display_response
        cameras = runtime.assistant.handle('Find public cameras in London')
        assert 'Public test road' in cameras.display_response
        # The owner's 2026-10-05 sequence: a bare "play" reached the model, which claimed "Playing…".
        followup = runtime.assistant.handle('play')
        assert followup.outcome.history[-1]['tool'] == 'intelligence_camera_play'
        assert followup.outcome.history[-1]['result']['output']['event_id'] == 'cameras_london:JamCams_test'
        assert 'Opening public camera in desktop chat: Public test road' in followup.display_response
        assert runtime.session.active_camera == 'cameras_london:JamCams_test'
        playback = runtime.assistant.handle('Play live camera in Delaware in chat')
        assert 'Opening public camera in desktop chat' in playback.display_response
        assert playback.outcome.complete
        assert playback.outcome.history[-1]['tool'] == 'intelligence_camera_play'
        assert playback.outcome.history[-1]['verification']['passed']
        clip = runtime.assistant.handle('Play camera in London')
        assert 'Video clip' in clip.display_response
        directories = runtime.assistant.handle('Show camera directories')
        assert 'EarthCam' in directories.display_response
        recalled = runtime.assistant.handle('Recall intelligence knowledge about flood')
        assert 'Provider test flood' in recalled.display_response
        assert runtime.research.knowledge.get_cached_source(runtime.intelligence.feeds[4].url)
        atlas = runtime.registry.execute('intelligence_map', {})
        assert atlas.success and atlas.output['map_url'].startswith('http://127.0.0.1:')
        assert runtime.intelligence.live.recorder is runtime.intelligence.history
        assert 'feed' in runtime.registry.get('intelligence_history').input_schema['properties']
        reports = runtime.assistant.handle('Show major events over the last 24 hours')
        assert reports.outcome.history[-1]['tool'] == 'intelligence_objects'
        assert 'merged event reports match' in reports.display_response
    finally:
        runtime.close()
    assert runtime.intelligence.fetcher.closed


def test_runtime_background_disable_prevents_unrequested_feed_io():
    runtime = build_runtime(use_model=False, start_background_services=False)
    try:
        assert not runtime.intelligence.status()['running']
    finally:
        runtime.close()
