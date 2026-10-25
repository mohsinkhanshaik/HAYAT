"""Small opt-in real-model check; pytest isolates the owner's stored data."""
import os
import re
import time

import pytest


@pytest.mark.skipif(os.environ.get('HAYAT_LIVE') != '1', reason='Opt-in real local model check')
def test_god_mode_calculations_and_live_reasoning(tmp_path):
    from app.runtime import build_runtime
    runtime = build_runtime(use_model=True, start_background_services=False)
    runtime.modes.path = tmp_path / 'mode.json'
    runtime.modes.set('GOD')
    try:
        for prompt, answer in [
            ('What is 17 multiplied by 23? Give the short calculation.', '391'),
            ('A shop buys a jacket for $80, marks it up 50%, then sells it at 20% off the marked price. '
                 'What is the profit in dollars? Show the short working.', '16'),
                ('All zargs are blue. Some blue objects are cubes. Does it follow that all zargs are cubes? Give a short answer.',
                 '(?i:no|does not follow|cannot conclude|can.t conclude)')]:
            started = time.monotonic()
            reply = runtime.assistant.handle(prompt)
            print(f'LIVE {time.monotonic() - started:.1f}s: {reply.display_response}'.encode('ascii', 'backslashreplace').decode('ascii'), flush=True)
            assert re.search(r'\b' + answer + r'\b', reply.display_response)
            assert len(reply.display_response.split()) < 120
            if answer == '16':
                assert not re.search(r'\$14\b', reply.display_response)
            assert {'DEEP', 'ADVERSARIAL'} <= set(runtime.session.effective_modes)
    finally:
        runtime.close()
