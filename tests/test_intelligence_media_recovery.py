import shutil
import subprocess

import pytest

from app.config import PROJECT_ROOT


def test_selected_atlas_media_recovers_and_stop_cancels_stale_retries():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is needed to exercise atlas media controls')
    result = subprocess.run([node, 'tests/intelligence_media_recovery.cjs'], cwd=PROJECT_ROOT,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
