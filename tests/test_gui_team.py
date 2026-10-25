from types import SimpleNamespace

from app.gui_team import TeamWorkspace
from app.gui_theme import Palette


def test_team_workspace_shows_evidence_gaps_and_stops_selected_team(shared_tk):
    from tests.conftest import open_window, close_window
    parent = open_window(shared_tk, '820x700-4000-4000')
    cancelled = []
    entry = {'team_id': 'team-check', 'goal': 'Compare clipping tools', 'done': 1, 'total': 3,
             'current_worker': 'Verification Agent', 'cancelled': False,
             'worker_specs': [{'role': 'Research Agent', 'mission': 'Find companies', 'depends_on': [],
                               'deliverable': 'Sourced company roster'},
                              {'role': 'Verification Agent', 'mission': 'Check claims',
                               'depends_on': ['Research Agent'], 'deliverable': 'Check sourced coverage'}],
             'results': [{'role': 'Research Agent', 'ok': False, 'successful_tools': [],
                          'sources': [], 'note': 'No successful evidence'}]}
    teams = SimpleNamespace(status=lambda: {'running': [entry], 'recent': []}, cancel=cancelled.append)
    window = TeamWorkspace(parent, Palette, teams, ['researcher', 'reviewer'])
    try:
        window.update()
        assert window.stop_button.winfo_height() >= window.stop_button.winfo_reqheight()
        assert (window.stop_button.winfo_rooty() + window.stop_button.winfo_height()
                <= window.winfo_rooty() + window.winfo_height())
        window.jobs.selection_set('team-check')
        window.show_details()
        assert 'Unverified / incomplete' in window.details.get('1.0', 'end')
        assert 'After: Research Agent' in window.details.get('1.0', 'end')
        assert 'Deliverable: Sourced company roster' in window.details.get('1.0', 'end')
        assert 'Verification Agent · Working' in window.details.get('1.0', 'end')
        assert str(window.stop_button.cget('state')) == 'normal'
        window.stop_selected()
        assert cancelled == ['team-check']
        entry['cancelled'] = True
        window.refresh()
        assert str(window.stop_button.cget('state')) == 'disabled'
        assert window.jobs.item('team-check', 'values')[0] == 'Stopping'
    finally:
        window.destroy()
        close_window(parent)


def test_preview_shows_actual_five_role_plan_without_starting_workers(shared_tk, tmp_path):
    from app.agents.team import WorkerTeam
    from tests.test_worker_team import Registry
    from tests.conftest import open_window, close_window
    parent = open_window(shared_tk, '820x700-4000-4000')
    team = WorkerTeam(None, Registry(), root=tmp_path)
    window = TeamWorkspace(parent, Palette, team)
    try:
        window.preview_job_team()
        text = window.details.get('1.0', 'end')
        assert 'Plan preview · no workers started' in text
        assert 'Company Intelligence Agent' in text and 'Resume Matching Agent' in text
        assert 'After: Job Search Agent, Company Intelligence Agent' in text
        assert team.status() == {'running': [], 'recent': []}
    finally:
        window.destroy()
        close_window(parent)


def _sample_team():
    from app.agents.capacity import TeamCapacity

    capacity = TeamCapacity(16384, 34, 75.0, 3, 4, 'Running on a test machine.',
                            ['My brain\'s context window is 16,384 tokens.', 'Workers share one brain.'])
    entry = {'team_id': 'team-jobs', 'goal': 'Research 5 AI infrastructure companies and find jobs for me',
             'done': 2, 'total': 3, 'current_worker': 'Verification Agent', 'cancelled': False,
             'seconds': 260.0, 'max_seconds': 1200.0, 'tool_calls': 14, 'max_tool_calls': 40, 'planned_by': 'model',
             'worker_specs': [
                 {'role': 'Research Agent', 'mission': 'Find the companies', 'depends_on': [], 'tools': ['web_search'],
                  'deliverable': 'Sourced company roster'},
                 {'role': 'Job Search Agent', 'mission': 'Find openings', 'depends_on': ['Research Agent'],
                  'tools': ['web_search', 'public_web_fetch'], 'deliverable': ''},
                 {'role': 'Verification Agent', 'mission': 'Check claims', 'depends_on': ['Job Search Agent'],
                  'tools': ['public_web_fetch'], 'deliverable': 'Checked claims'}],
             'results': [
                 {'role': 'Research Agent', 'ok': True, 'complete': True, 'tools_used': ['web_search', 'web_search'],
                  'successful_tools': ['web_search'], 'sources': ['https://example.com/a', 'https://example.com/b'],
                  'seconds': 71.0, 'note': '', 'coverage': [{}, {}], 'findings': 'NVIDIA, AMD, Cerebras with sources.'},
                 {'role': 'Job Search Agent', 'ok': False, 'complete': False, 'tools_used': ['public_web_fetch'],
                  'successful_tools': [], 'sources': [], 'seconds': 90.0, 'note': 'robots.txt refused the careers page',
                  'coverage': [], 'findings': ''}]}
    return SimpleNamespace(status=lambda: {'running': [entry], 'recent': []}, cancel=lambda team_id: None,
                           capacity=lambda refresh=False: capacity)


def test_workspace_shows_machine_limits_workers_and_each_workers_evidence(shared_tk):
    from tests.conftest import open_window, close_window
    parent = open_window(shared_tk, '1120x780-4000-4000')
    window = TeamWorkspace(parent, Palette, _sample_team())
    try:
        window.update()
        assert window.capacity_values['workers'].get() == '34'
        assert window.capacity_values['teams'].get() == '4'
        assert window.capacity_values['window'].get() == '16,384 tokens'
        assert 'measured, 3 runs' in window.capacity_values['pace'].get()
        assert 'context window is 16,384 tokens' in window.capacity_reasons.get()
        assert window.jobs.item('team-jobs', 'values')[2:] == ('4 min / 20 min', '14 / 40')
        window.jobs.selection_set('team-jobs')
        window.update()
        assert window.workers.get_children() == ('Research Agent', 'Job Search Agent', 'Verification Agent')
        assert window.workers.item('Research Agent', 'values')[0] == 'Complete'
        assert window.workers.item('Job Search Agent', 'values')[0] == 'Unverified / incomplete'
        assert window.workers.item('Verification Agent', 'values')[0] == 'Working'
        assert 'Time: 4 min of a 20 min budget · Tool calls: 14 of 40' in window.details.get('1.0', 'end')
        window.workers.selection_set('Research Agent')
        window.update()
        text = window.details.get('1.0', 'end')
        assert 'Mission: Find the companies' in text and 'NVIDIA, AMD, Cerebras' in text
        assert '• https://example.com/a' in text and 'Tools used: 2 calls (web_search)' in text
        window.workers.selection_set('Job Search Agent')
        window.update()
        assert 'Gaps: robots.txt refused the careers page' in window.details.get('1.0', 'end')
        window.workers.selection_set('Verification Agent')
        window.update()
        assert 'Not run yet.' in window.details.get('1.0', 'end')
    finally:
        window.destroy()
        close_window(parent)
