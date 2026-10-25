"""The requested company/job team has real handoffs and honest completion accounting."""
import json

from app.agents.team import VERIFIER, WorkerResult, WorkerSpec, WorkerTeam, _source_urls, wants_team
from tests.test_worker_team import Brain, Registry


GOAL = 'Research 20 AI infrastructure companies and find jobs suitable for me.'


def test_career_request_creates_five_specialists_without_waiting_for_model(tmp_path):
    brain = Brain('not json')
    team = WorkerTeam(brain, Registry(), root=tmp_path)
    workers, planned_by = team.plan(GOAL)
    assert wants_team(GOAL) and planned_by == 'career workflow'
    assert not brain.prompts
    assert [worker.role for worker in workers] == [
        'Research Agent', 'Job Search Agent', 'Company Intelligence Agent', 'Resume Matching Agent', VERIFIER]
    assert workers[1].depends_on == workers[2].depends_on == ['Research Agent']
    assert workers[3].depends_on == ['Job Search Agent', 'Company Intelligence Agent']
    assert workers[4].depends_on == [worker.role for worker in workers[:4]]
    assert [worker.target_count for worker in workers] == [20, 20, 20, None, 20]
    assert all(worker.deliverable for worker in workers)
    assert 'knowledge_search' in workers[3].tools and 'read_file' in workers[3].tools
    assert 'never invent qualifications' in workers[3].mission
    assert all('send_message' not in worker.tools for worker in workers)


def test_count_in_words_and_unavailable_tools_are_respected(tmp_path):
    registry = Registry()
    registry.names = lambda: ['web_search']
    team = WorkerTeam(None, registry, root=tmp_path)
    specs, _ = team.plan('Use a team to research twenty AI companies and find openings for me')
    assert specs[0].target_count == 20
    assert specs[3].tools == []
    assert {tool for spec in specs for tool in spec.tools} == {'web_search'}


def result_report(coverage, *, completion='complete', gaps=None):
    return json.dumps({'findings': 'Acme has a sourced opening: https://acme.ai/jobs',
                       'completion': completion, 'coverage': coverage, 'gaps': gaps or []})


def test_invented_and_duplicate_coverage_cannot_complete_twenty_company_goal():
    spec = WorkerSpec('Research Agent', 'Find companies', ['web_search'], deliverable='roster', target_count=20)
    coverage = [{'company': 'Acme', 'source': 'https://acme.ai/jobs'},
                {'company': ' ACME ', 'source': 'https://acme.ai/jobs'},
                {'company': 'Invented', 'source': 'https://never-observed.example'}]
    result = WorkerResult(spec.role, spec.mission, spec.tools, True, result_report(coverage),
                          sources=['https://acme.ai/jobs'], successful_tools=['web_search'])
    WorkerTeam._check_deliverable(spec, result, [])
    assert result.ok and not result.complete
    assert len(result.coverage) == 1
    assert '1 of 20' in result.note


def test_verifier_must_observe_its_own_coverage_sources():
    spec = WorkerSpec(VERIFIER, 'Check claims', ['public_web_fetch'], deliverable='checks', target_count=1)
    prior = WorkerResult('Research Agent', 'Research', ['web_search'], True, 'Acme',
                         sources=['https://acme.ai/jobs'])
    result = WorkerResult(VERIFIER, spec.mission, spec.tools, True,
                          result_report([{'company': 'Acme', 'source': 'https://acme.ai/jobs'}]),
                          sources=['https://other.example'], successful_tools=['public_web_fetch'])
    WorkerTeam._check_deliverable(spec, result, [prior])
    assert not result.complete and not result.coverage


def test_missing_profile_and_invalid_reports_remain_incomplete():
    spec = WorkerSpec('Resume Matching Agent', 'Match jobs', ['knowledge_search'], deliverable='ranked roles')
    result = WorkerResult(spec.role, spec.mission, spec.tools, True,
                          result_report([], completion='blocked', gaps=['No resume or skills profile found']))
    WorkerTeam._check_deliverable(spec, result, [])
    assert not result.complete and 'No resume' in result.note
    malformed = WorkerResult(spec.role, spec.mission, spec.tools, True, 'Everything is complete')
    WorkerTeam._check_deliverable(spec, malformed, [])
    assert not malformed.complete and 'required completion report' in malformed.note


class ReportingBrain(Brain):
    def generate(self, messages, tools=None, **kwargs):
        reply = super().generate(messages, tools=tools, **kwargs)
        if (tools and not reply.tool_calls) or kwargs.get('response_schema', {}).get('required') == [
                'findings', 'completion', 'coverage', 'gaps']:
            reply.content = result_report([{'company': 'Acme', 'source': 'https://acme.ai/jobs'}])
        return reply


def test_career_workers_execute_handoffs_and_save_partial_coverage(tmp_path):
    brain = ReportingBrain()
    team = WorkerTeam(brain, Registry(), root=tmp_path)
    report = team.run(GOAL)
    assert report.finished and not report.complete
    assert len(report.results) == 5
    assert all(worker.successful_tools for worker in report.results)
    assert all(len(worker.coverage) == 1 for worker in report.results)
    assert any('Findings from Job Search Agent' in prompt and 'Company Intelligence Agent' in prompt
               for prompt in brain.prompts)
    assert '1 of 20' in report.results[-1].note
    saved = json.loads((tmp_path / 'data/artifacts/teams' / f'{report.team_id}.json').read_text())
    assert saved['workers'][0]['target_count'] == 20
    assert saved['results'][0]['complete'] is False
    assert 'goal is incomplete' in team.summary_text(report)


def test_complete_contracts_can_finish_a_fully_covered_team(tmp_path):
    team = WorkerTeam(ReportingBrain(), Registry(), root=tmp_path)
    report = team.run('Use a team to research 1 AI infrastructure companies and find jobs suitable for me.')
    assert report.finished and report.complete
    assert all(result.complete for result in report.results)


def test_model_design_cannot_silently_drop_extra_workers(tmp_path):
    team = WorkerTeam(None, Registry(), root=tmp_path)
    # One more than this machine's measured limit leaves room for (the verifier is added on top).
    limit = team.capacity().max_workers
    rows = [{'role': f'Worker {i} Agent', 'mission': 'Find sources', 'tools': ['web_search']} for i in range(limit)]
    assert team._validated(rows, ['web_search']) is None
    assert len(team._validated(rows[:limit - 1], ['web_search'])) == limit


def test_tool_source_links_are_extracted_before_json_escaping():
    assert _source_urls({'content': 'Source: https://acme.ai/jobs\nNext line',
                         'nested': [{'url': 'https://acme.ai/about'}]}) == [
        'https://acme.ai/jobs', 'https://acme.ai/about']


def test_chat_request_routes_to_the_temporary_team(tmp_path):
    from app.agents.team import TeamReport
    from tests.test_assistant_service import runtime
    rt = runtime(tmp_path)
    started = []
    class CaptureTeam(WorkerTeam):
        def start(self, goal):
            specs, planned_by = self.plan(goal)
            started.append(goal)
            return TeamReport('team-chat-test', goal, '', specs, planned_by=planned_by)
    rt.teams = CaptureTeam(None, Registry(), root=tmp_path)
    answer = rt.assistant.handle(GOAL)
    assert started == [GOAL]
    assert not rt.manager.calls
    assert 'temporary team of 5 workers' in answer.display_response
    assert 'Company Intelligence Agent' in answer.display_response
    assert 'Resume Matching Agent' in answer.display_response
