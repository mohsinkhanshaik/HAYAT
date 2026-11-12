"""Sources & claims in the Searches window, and the reviewed hand-off into an invention draft."""
import time
from types import SimpleNamespace
from tkinter import ttk

import pytest

from app.gui_research import ResearchStudio
from app.gui_research_evidence import ResearchDraftDialog, evidence_records
from app.gui_theme import Palette, style_controls, style_hayat_buttons
from app.invention_handoff import completed_research_result
from app.research.jobs import ResearchJob, ResearchJobs
from app.safety.permissions import PermissionManager
from app.tools.invention import register_invention_tools
from app.tools.registry import ToolRegistry
from tests.conftest import open_window
from tests.test_executive_invention import bench
from tests.test_invention_evidence import DIGEST, STAMP, URL, research

OTHER = 'https://www.nasa.gov/fixture-counterpoint'


def recorded():
    """A declared fixture: two sources, one supported claim that the second source disputes, one unlinked claim."""
    data = research() | {'run_id': 'research-fixture', 'query': 'A synthetic sensor study',
                         'answer': 'A declared fixture, not real-world research.',
                         'knowledge_gaps': ['Operating range unmeasured.']}
    data['sources'].append({'url': OTHER, 'final_url': OTHER, 'title': 'Declared counterpoint fixture', 'primary': False,
                            'retrieved_at': STAMP, 'status_code': 200, 'content_hash': 'b' * 64})
    data['claims'][0]['contradictions'] = [{'claim_id': 'c1', 'source_url': OTHER, 'source_hash': 'b' * 64,
                                            'retrieved_at': STAMP, 'excerpt': 'a declared objection', 'support_type': 'DIRECT'}]
    data['claims'].append({'claim_id': 'c2', 'text': 'A statement citing a page that was never retrieved.', 'status': 'UNVERIFIED',
                           'evidence': [{'claim_id': 'c2', 'source_url': 'https://example.org/not-retrieved',
                                         'excerpt': 'unretrieved', 'support_type': 'DIRECT'}]})
    return data


def test_links_come_only_from_recorded_excerpts_of_retrieved_documents():
    model = evidence_records(recorded())
    assert model['source_total'] == 2 and model['claim_total'] == 2 and not model['truncated']
    links = {(edge['source'], edge['target'], edge['kind']) for edge in model['edges']}
    assert links == {('source:0', 'claim:0', 'Recorded evidence'), ('source:1', 'claim:0', 'Contradiction')}
    # The second claim cites an address that is not among the retrieved documents: it gets no link, not a guessed one.
    assert not [edge for edge in model['edges'] if edge['target'] == 'claim:1']
    assert model['run_id'] == 'research-fixture' and model['gaps'] == ['Operating range unmeasured.']


def test_nothing_recorded_is_an_empty_map_and_a_long_record_says_it_was_cut():
    assert evidence_records(None) == {'nodes': {}, 'edges': [], 'source_total': 0, 'claim_total': 0, 'truncated': False,
                                      'run_id': '', 'gaps': []}
    assert evidence_records('an answer alone')['nodes'] == {}
    data = recorded()
    data['sources'] = [dict(data['sources'][0], url=f'{URL}/{index}', final_url=f'{URL}/{index}') for index in range(230)]
    model = evidence_records(data)
    assert model['truncated'] and model['source_total'] == 230
    assert len([key for key in model['nodes'] if key.startswith('source:')]) == 200


def test_claim_inspector_explains_unretrieved_evidence_and_can_filter_by_recorded_status(desk):
    studio = desk.studio
    choose(studio, 'job-evidence')
    view = studio.evidence_map
    view.query.set('unverified')
    desk.root.update()
    assert view.tree.get_children() == ('claim:1',)
    words = view.details.get('1.0', 'end')
    assert 'Evidence check: Evidence not established' in words
    assert 'absent from the retained source set' in words
    assert 'Next: Retrieve a primary source' in words


class GUI(Palette):
    def __init__(self, root, jobs, registry):
        self.root = root
        self.runtime = SimpleNamespace(research_jobs=jobs, registry=registry)
        self.opened = []

    def _open_invention_project(self, project_id):
        self.opened.append(project_id)


@pytest.fixture
def desk(shared_tk, tmp_path):
    root = open_window(shared_tk, '960x760-4000-4000')
    jobs = ResearchJobs(lambda: None)
    jobs._jobs.append(ResearchJob('job-evidence', 'A synthetic sensor study', 'A synthetic sensor study', 'deep', '',
                                  status='done', answer='A declared fixture.', sources=2, confidence=.6, result=recorded()))
    jobs._jobs.append(ResearchJob('job-answer-only', 'No record', 'No record', 'quick', '', status='done', answer='Words only.'))
    jobs._jobs.append(ResearchJob('job-running', 'Still going', 'Still going', 'deep', '', status='running', result=recorded()))
    workbench = bench(tmp_path / 'invention', None)
    registry = ToolRegistry(PermissionManager({'default': 'ALLOW', 'permissions': {}}, tmp_path))
    register_invention_tools(registry, workbench, workbench.executive, workbench.dual_brain)
    gui = GUI(root, jobs, registry)
    workbench.research_result_supplier = lambda job_id: completed_research_result(gui.runtime, job_id)
    style = ttk.Style(root)
    previous = style.theme_use()
    style.theme_use('clam')
    style_controls(root, gui)
    style_hayat_buttons(root, gui)
    studio = ResearchStudio(gui)
    root.update()
    try:
        yield SimpleNamespace(studio=studio, gui=gui, workbench=workbench, root=root)
    finally:
        dialog = getattr(studio, '_draft_window', None)
        if dialog is not None and dialog.winfo_exists():
            dialog.destroy()
        studio.close()
        style.theme_use(previous)
        root.destroy()


def choose(studio, job_id):
    studio.tree.selection_set(job_id)
    studio.show_selected()
    studio.update()


def test_the_evidence_tab_follows_the_selected_search_and_its_links(desk):
    studio = desk.studio
    choose(studio, 'job-evidence')
    view = studio.evidence_map
    assert view.status.get().startswith('2 source documents · 2 claims · 2 recorded evidence links')
    assert set(view.tree.get_children()) == {'source:0', 'source:1', 'claim:0', 'claim:1'}
    studio.details_tabs.select(view)
    studio.update()
    view.tree.selection_set('claim:0')
    view.inspect()
    studio.update()
    words = view.details.get('1.0', 'end')
    assert 'Recorded claim status: SUPPORTED' in words and 'Contradictions · DIRECT' in words and OTHER in words
    assert 'A citation alone does not validate an invention' in words
    # Both linked documents are drawn beside the claim; clicking one follows the link to that document.
    drawn = {box[4] for box in view._boxes}
    assert drawn == {'claim:0', 'source:0', 'source:1'}
    left, top, right, bottom, _key = next(box for box in view._boxes if box[4] == 'source:1')
    view.pick(SimpleNamespace(x=int((left + right) / 2), y=int((top + bottom) / 2)))
    assert view.selected == 'source:1' and 'Primary source: not established' in view.details.get('1.0', 'end')
    # The unlinked claim says so instead of drawing an invented line.
    view.tree.selection_set('claim:1')
    view.inspect()
    assert {box[4] for box in view._boxes} == {'claim:1'}
    # Typing filters; a selection that no longer matches is replaced, never left pointing at a hidden row.
    view.query.set('counterpoint')
    assert view.tree.get_children() == ('source:1',) and view.selected == 'source:1'
    view.query.set('')
    # A search with no recorded evidence empties the map rather than showing the previous search's.
    choose(studio, 'job-answer-only')
    assert not view.tree.get_children() and view.status.get().startswith('0 source documents')
    choose(studio, 'job-running')
    assert not view.tree.get_children()


def test_drafting_is_offered_only_for_finished_research_that_recorded_evidence(desk):
    studio = desk.studio
    assert studio.draft_button.winfo_manager() and studio.draft_button.master is studio.evidence_map.toolbar
    for job_id, offered in (('job-evidence', True), ('job-answer-only', False), ('job-running', False)):
        choose(studio, job_id)
        assert studio.draft_button.instate(['!disabled']) is offered, job_id
    choose(studio, 'job-answer-only')
    studio.draft_invention()
    assert 'structured source evidence' in studio.detail_status.get() and getattr(studio, '_draft_window', None) is None
    # Without the hand-off tool in this runtime the button stays off instead of failing on click.
    desk.gui.runtime.registry = SimpleNamespace(names=lambda: ['invention_create'], interlock=desk.gui.runtime.registry.interlock)
    choose(studio, 'job-evidence')
    studio.refresh()
    assert studio.draft_button.instate(['disabled'])


def test_the_evidence_tab_stays_usable_in_the_smallest_window(desk):
    studio = desk.studio
    choose(studio, 'job-evidence')
    studio.geometry('720x560-4000-4000')
    studio.details_tabs.select(studio.evidence_map)
    studio.update()
    studio.update()
    view = studio.evidence_map
    assert not studio.intro.winfo_ismapped()            # its two lines went to the tabs
    # While the evidence is being read the search list keeps only the selected search and the image note steps aside,
    # so the evidence list has its heading and about two rows to scroll through (it was 36 px: under one row).
    # "Larger view" is there for more.
    assert int(studio.tree['height']) == 1 and studio.tree.bbox('job-evidence')
    assert not studio.image_hint.winfo_ismapped()
    assert view.tree.winfo_height() >= 80 and view.canvas.winfo_ismapped()
    assert view.tree.bbox(view.tree.get_children()[0])
    for button in (studio.draft_button, studio.larger_button):
        assert button.winfo_ismapped()
        assert button.winfo_rootx() + button.winfo_width() <= studio.winfo_rootx() + studio.winfo_width()
    # The Answer tab gets the note and the second search row back, and still has room to read.
    studio.details_tabs.select(0)
    studio.update()
    studio.update()
    assert studio.image_hint.winfo_ismapped() and int(studio.tree['height']) == 2 and studio.result.winfo_height() >= 60
    assert studio.image_hint.winfo_y() < studio.status_label.winfo_y()
    studio.geometry('960x760-4000-4000')
    studio.update()
    studio.update()
    assert studio.intro.winfo_ismapped()
    # Back above the search form, where it was: re-showing it must not move it below the answer.
    assert studio.intro.winfo_y() < studio.kind_choice.master.winfo_y()


def test_at_the_default_size_the_evidence_tab_gets_the_room_the_search_list_was_not_using(desk):
    studio = desk.studio
    studio.geometry('860x700-4000-4000')
    choose(studio, 'job-evidence')
    studio.update()
    assert int(studio.tree['height']) == 5
    studio.details_tabs.select(studio.evidence_map)
    studio.update()
    studio.update()
    view = studio.evidence_map
    assert int(studio.tree['height']) == 2 and studio.tree.bbox('job-evidence')    # the search being read stays in view
    assert studio.image_hint.winfo_ismapped()                                       # only a compact window drops the note
    assert view.tree.winfo_height() >= 120 and view.canvas.winfo_height() >= 120
    assert view.winfo_rooty() + view.winfo_height() <= studio.winfo_rooty() + studio.winfo_height()
    studio.details_tabs.select(0)
    studio.update()
    assert int(studio.tree['height']) == 5


def test_the_larger_view_shows_the_selected_search_and_follows_the_selection(desk):
    studio = desk.studio
    choose(studio, 'job-evidence')
    studio.open_evidence_window()
    window = studio._evidence_window
    try:
        window.update()
        # Both columns are whole: the type is not cut off at the list's edge.
        tree = window.view.tree
        assert int(tree.column('#0', 'width')) + int(tree.column('kind', 'width')) <= tree.winfo_width()
        assert window.heading.cget('text') == 'A synthetic sensor study'
        assert set(window.view.tree.get_children()) == {'source:0', 'source:1', 'claim:0', 'claim:1'}
        studio.open_evidence_window()
        assert studio._evidence_window is window        # one window, reused
        choose(studio, 'job-answer-only')
        assert window.heading.cget('text') == 'No record' and not window.view.tree.get_children()
        choose(studio, 'job-running')                    # unfinished research shows nothing, even with a partial record
        assert not window.view.tree.get_children()
        choose(studio, 'job-evidence')
        assert len(window.view.tree.get_children()) == 4
    finally:
        window.destroy()
    choose(studio, 'job-answer-only')                    # a closed window is simply no longer updated
    studio.open_evidence_window()
    assert studio._evidence_window is not window and studio._evidence_window.heading.cget('text') == 'No record'
    studio._evidence_window.destroy()


def wait(dialog, root):
    until = time.monotonic() + 5
    while dialog._busy and time.monotonic() < until:
        root.update()
        time.sleep(.01)
    assert not dialog._busy


def test_a_draft_is_saved_only_after_the_evidence_is_reviewed_and_keeps_the_owner_s_words(desk):
    studio = desk.studio
    choose(studio, 'job-evidence')
    studio.draft_invention()
    dialog = studio._draft_window
    assert isinstance(dialog, ResearchDraftDialog)
    studio.draft_invention()
    assert studio._draft_window is dialog              # the same search reuses its dialog
    assert dialog.create_button.instate(['disabled'])   # nothing reviewed yet
    dialog.review()
    assert 'Invention goal' in dialog.status.get() and dialog.create_button.instate(['disabled'])
    dialog.goal.set('Measure a sensor against a baseline')
    dialog.constraints.set(' Budget below $50 ;; Use a measured baseline. ')
    dialog.review()
    report = dialog.report.get('1.0', 'end')
    assert 'Limit: Budget below $50' in report and 'Limit: Use a measured baseline.' in report
    assert '2 sources · 2 claims' in report and 'Operating range unmeasured.' in report
    assert 'No tests or acceptance values are inferred from source text.' in report
    assert dialog.create_button.instate(['!disabled']) and not desk.workbench.list()
    # Changing either field after the review withdraws it: what is saved is what was reviewed.
    dialog.constraints.set('Budget below $40')
    assert dialog.create_button.instate(['disabled']) and dialog.preview is None
    dialog.create()
    assert not dialog._busy and not desk.workbench.list()
    dialog.review()
    dialog.create()
    wait(dialog, desk.root)
    saved = desk.workbench.list()
    assert len(saved) == 1
    project = desk.workbench.get(saved[0]['id'])
    assert project['goal'] == 'Measure a sensor against a baseline' and project['constraints'] == ['Budget below $40']
    assert project['state'] == 'DRAFT' and project['tests'] == [] and project['rounds'] == []
    assert project['research_handoff']['job_id'] == 'job-evidence'
    assert project['research_handoff']['evidence_hash'] == dialog.preview['evidence_hash']
    assert 'Draft saved' in dialog.status.get() and dialog.create_button.instate(['disabled'])
    # One dialog, one draft: pressing again does not save a second copy.
    dialog.create()
    assert len(desk.workbench.list()) == 1
    dialog.open_button.invoke()
    assert desk.gui.opened == [project['id']]


def test_evidence_that_changes_between_review_and_create_is_refused_and_says_so(desk):
    studio = desk.studio
    choose(studio, 'job-evidence')
    studio.draft_invention()
    dialog = studio._draft_window
    dialog.goal.set('Measure a sensor')
    dialog.review()
    studio.jobs.get('job-evidence').result['claims'][0]['status'] = 'DISPUTED'
    dialog.create()
    wait(dialog, desk.root)
    assert 'Draft was not created' in dialog.status.get() and 'changed after preview' in dialog.status.get()
    assert not desk.workbench.list() and dialog.preview is None and dialog.create_button.instate(['disabled'])
    assert dialog.open_button.instate(['disabled'])


def test_capture_the_evidence_views_for_visual_review(desk):
    """Opt-in (HAYAT_SHOTS=1): the declared fixture on screen, for a person to look at. No owner data is shown."""
    import os
    if os.environ.get('HAYAT_SHOTS') != '1':
        pytest.skip('screenshots are captured only on request')
    from pathlib import Path

    from PIL import ImageGrab

    folder = Path('.test-runs/gui-shots')
    folder.mkdir(parents=True, exist_ok=True)
    studio = desk.studio

    def shot(window, name):
        window.lift()
        for _ in range(4):
            window.update()
            time.sleep(.05)
        ImageGrab.grab(window=int(window.wm_frame(), 16)).save(folder / name)

    choose(studio, 'job-evidence')
    studio.details_tabs.select(studio.evidence_map)
    studio.evidence_map.tree.selection_set('claim:0')
    studio.evidence_map.inspect()
    for size, name in (('860x700+40+40', '110-searches-sources-claims.png'), ('720x560+40+40', '111-searches-sources-claims-compact.png')):
        studio.geometry(size)
        shot(studio, name)
    studio.details_tabs.select(0)
    shot(studio, '112-searches-answer-compact.png')
    studio.open_evidence_window()
    larger = studio._evidence_window
    larger.geometry('980x680+60+60')
    larger.view.tree.selection_set('claim:0')
    larger.view.inspect()
    shot(larger, '113-evidence-larger-view.png')
    larger.view.tabs.select(1)
    shot(larger, '122-claim-evidence-check.png')
    larger.destroy()
    studio.draft_invention()
    dialog = studio._draft_window
    dialog.geometry('740x580+80+80')
    dialog.goal.set('Measure a sensor against a baseline')
    dialog.constraints.set('Budget below $50; Use a measured baseline.')
    dialog.review()
    shot(dialog, '114-draft-invention-reviewed.png')


def test_the_invention_panel_names_where_an_imported_draft_s_evidence_came_from(shared_tk, tmp_path):
    from app.gui_invention import InventionPanel
    from tests.conftest import close_window

    workbench = bench(tmp_path, None)
    result = recorded()
    workbench.research_result_supplier = lambda job_id: result
    preview = workbench.preview_research('job-evidence', 'Measure a sensor')
    project = workbench.create_from_research('job-evidence', 'Measure a sensor', preview['evidence_hash'])
    registry = ToolRegistry(PermissionManager({'default': 'ALLOW', 'permissions': {}}, tmp_path))
    register_invention_tools(registry, workbench, workbench.executive, workbench.dual_brain)
    runtime = SimpleNamespace(registry=registry, invention=workbench, session=SimpleNamespace(session_id='gui-fixture'))
    root = open_window(shared_tk, '1100x760-4000-4000')
    panel = InventionPanel(root, Palette, runtime, lambda *_: None)
    panel.pack(fill='both', expand=True)

    def settle():
        until = time.monotonic() + 5
        while (panel._pending or getattr(panel, '_requested_project', None)) and time.monotonic() < until:
            root.update()
            time.sleep(.01)
        assert not panel._pending

    try:
        panel.open_saved_project(project['id'])     # the hand-off's "Open in Invention"
        settle()
        assert panel.project_id == project['id']
        words = panel.research_text.get('1.0', 'end')
        assert 'Imported from Searches' in words and 'Research run research-fixture' in words
        assert f"Evidence fingerprint {preview['evidence_hash'][:16]}" in words and '2 sources · 2 claims' in words
        assert 'The research below is the evidence you reviewed.' in words and 'Operating range unmeasured.' in words
        assert 'not instructions or authorization' in words
        assert DIGEST not in words                   # a fingerprint of the record, not a dump of it
        # A later refresh of the research is called out; the reviewed original stays with the project.
        saved = workbench.get(project['id'])
        saved['research'] = {'complete': False, 'sources': [], 'error': 'Later refresh failed.'}
        workbench._save(saved)
        panel.open_saved_project(project['id'])
        settle()
        words = panel.research_text.get('1.0', 'end')
        assert 'replaced after the import' in words and 'Later refresh failed.' in words
        # A project made the ordinary way has no origin to show.
        plain = workbench.create('A plain project')
        panel.open_saved_project(plain['id'])
        settle()
        assert 'Imported from Searches' not in panel.research_text.get('1.0', 'end')
    finally:
        close_window(root)
