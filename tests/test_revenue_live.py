"""Opt-in local-model proof: build real reviewable revenue artifacts, without outreach."""
import os
import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.environ.get('HAYAT_LIVE') != '1', reason='Opt-in local model revenue check')


def test_real_local_model_drafts_proposal_and_builds_downloadable_prompt_pack(tmp_path):
    from app.runtime import build_runtime
    from app.revenue import RevenueWorkspace
    runtime = build_runtime(use_model=True, start_background_services=False)
    workspace = RevenueWorkspace(runtime.business, runtime.registry, runtime.model, output=tmp_path / 'revenue')
    try:
        proposal = workspace.execute('generate_proposal', goal='OFFLINE TEST: Write a concise proposal (under 150 words) '
             'for a fictional client needing Python CSV deduplication. Do not send anything.',
             context='Fictional profile: Python developer with CSV parsing experience. No budget or deadline provided.')
        print('Proposal:', proposal['path'], flush=True)
        assert Path(proposal['path']).is_file()
        pack = workspace.execute('prompt_product_builder', goal='OFFLINE TEST: Build exactly two short files: prompts.md '
             'with three CSV cleanup prompts using {{input}} placeholders, and README.md with usage instructions. '
             'Keep each file under 150 words. This is a fictional local test product; do not publish.')
        print('Pack:', pack['bundle'], flush=True)
        with zipfile.ZipFile(pack['bundle']) as bundle:
            assert bundle.testzip() is None and 'prompts.md' in bundle.namelist()
        assert proposal['published'] is False and pack['executed'] is False
    finally:
        runtime.close()
