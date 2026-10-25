"""Opt-in real-model team check using only the project's README."""
import os

import pytest

pytestmark = pytest.mark.skipif(os.environ.get('HAYAT_LIVE') != '1', reason='Opt-in local model team check')


def test_local_team_reads_hands_off_and_checks_real_content(tmp_path):
    from app.agents.team import WorkerTeam, WorkerSpec
    from app.config import PROJECT_ROOT
    from app.runtime import build_runtime
    runtime = build_runtime(use_model=True, start_background_services=False)
    path = (PROJECT_ROOT / 'README.md').as_posix()
    class LocalTeam(WorkerTeam):
        def plan(self, goal):
            specs = [WorkerSpec('Reader Agent', f'Use read_file on {path}; identify the project name and one GUI feature.', ['read_file']),
                     WorkerSpec('Analysis Agent', f'Use read_file on {path}; check the reader findings and identify the chat actions menu.',
                                ['read_file'], ['Reader Agent'])]
            return self._with_verifier(specs, ['read_file']), 'local test plan'
    try:
        team = LocalTeam(runtime.model, runtime.registry, root=tmp_path)
        report = team.run(f'Read {path} and give the project name and one GUI feature. Verification must re-read that same file.')
        for result in report.results:
            print(f'{result.role}: evidence={result.successful_tools}, ok={result.ok}, gap={result.note}', flush=True)
        assert report.finished and report.complete
        assert all(result.successful_tools for result in report.results)
        assert 'HAYAT' in report.answer
    finally:
        runtime.close()


def test_career_contract_with_real_local_model_and_offline_fixture(tmp_path):
    """Use fictional data to exercise the five real workers, without performing a job hunt."""
    import json
    from app.agents.team import WorkerTeam
    from app.runtime import build_runtime
    fixture = tmp_path / 'fictional-career-fixture.txt'
    fixture.write_text(
        'OFFLINE TEST DATA; fictional company and owner.\n'
        'Company: Acme AI. Category: GPU infrastructure. Product: GPU cloud. Stack: Kubernetes.\n'
        'Job: Platform Engineer. Remote allowed. Requirements: Python and Kubernetes. Status: open in this fixture.\n'
        'Fictional owner profile: Python and Kubernetes experience; remote preference; eligible for this fixture role.\n'
        'Company and job source: https://acme.example/jobs\n', encoding='utf-8')
    runtime = build_runtime(use_model=True, start_background_services=False)
    class OfflineCareerTeam(WorkerTeam):
        def plan(self, goal):
            specs, planned_by = super().plan(goal)
            for spec in specs:
                spec.tools = ['read_file']
                spec.mission += (f' OFFLINE TEST: all required company, job and owner facts are in {fixture.as_posix()}. '
                                 'Use read_file on that exact path. Judge completeness against this fixture only. '
                                 'Do not search any other files or websites. Coverage source must be https://acme.example/jobs. '
                                 'Identify the data as fictional in findings.')
            return specs, planned_by
    try:
        team = OfflineCareerTeam(runtime.model, runtime.registry, root=tmp_path)
        report = team.run('Research 1 AI infrastructure companies and find jobs suitable for me.')
        for result in report.results:
            print(f'{result.role}: complete={result.complete}, coverage={len(result.coverage)}, gaps={result.note}', flush=True)
        assert report.finished and report.complete
        assert len(report.results) == 5
        assert all(result.complete and result.successful_tools for result in report.results)
        saved = json.loads((tmp_path / 'data/artifacts/teams' / f'{report.team_id}.json').read_text())
        assert saved['planned_by'] == 'career workflow'
    finally:
        runtime.close()
