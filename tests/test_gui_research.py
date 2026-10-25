"""Research refreshes must leave the owner's reading position and selection alone."""
from types import SimpleNamespace
import os
from tkinter import ttk

import pytest

from app.gui_research import ResearchStudio
from app.gui_theme import Palette, style_controls, style_hayat_buttons
from app.research.jobs import ResearchJob, ResearchJobs
from tests.conftest import open_window


class GUI(Palette):
    def __init__(self, root, jobs):
        self.root = root
        from app.emergency import ExecutionInterlock
        self.runtime = SimpleNamespace(research_jobs=jobs, registry=SimpleNamespace(interlock=ExecutionInterlock()))
        self.images = []
        self.image_added = True

    def _add_files(self, *, kind):
        self.images.append(kind)
        return self.image_added


@pytest.fixture
def studio(shared_tk):
    root = open_window(shared_tk, '960x760-4000-4000')
    jobs = ResearchJobs(lambda: None)
    for index in range(30):
        jobs._jobs.append(ResearchJob(f'job-{index}', f'Topic {index}', f'Topic {index}', 'deep', '',
                                     status='done', answer='\n'.join(f'Evidence {n}: source [1]' for n in range(200)),
                                     sources=5, confidence=.8))
    gui = GUI(root, jobs)
    style = ttk.Style(root)
    previous_theme = style.theme_use()
    style.theme_use('clam')
    style_controls(root, gui)
    style_hayat_buttons(root, gui)
    panel = ResearchStudio(gui)
    root.update()
    try:
        yield panel
    finally:
        panel.close()
        style.theme_use(previous_theme)
        root.destroy()


def select(panel, job_id):
    panel.tree.selection_set(job_id)
    panel.show_selected()
    panel.update()


def test_public_dataset_mode_starts_and_retries_metadata_scope(studio, monkeypatch):
    calls = []
    def start(query, *, asked, level, **options):
        calls.append((query, options))
        job = ResearchJob('data-job', query, asked, level, '', public_data=options['public_data'],
                          status='done', answer='Registry metadata only')
        studio.jobs._jobs.append(job)
        return job
    monkeypatch.setattr(studio.jobs, 'start', start)
    studio.search_kind.set('Public datasets')
    studio._show_person_fields()
    studio.query.insert(0, 'climate')
    studio.search()
    studio.update()
    assert calls == [('climate', {'public_data': {'kind': 'datasets'}})]
    assert 'metadata' in studio.detail_status.get().lower()


def test_onion_search_and_retry_keep_tor_scope_without_starting_on_tk_thread(studio, monkeypatch):
    calls = []
    def start(query, *, asked, level, **options):
        calls.append((query, options))
        job = ResearchJob('onion-job-' + str(len(calls)), query, asked, level, '', onion=options.get('onion', False),
                          status='done', answer='Tor onion evidence, identity still needs checking')
        studio.jobs._jobs.append(job)
        return job
    monkeypatch.setattr(studio.jobs, 'start', start)
    studio.search_kind.set('Onion (Tor)')
    studio._show_person_fields()
    studio.query.insert(0, 'Tor Project')
    studio.search()
    studio.update()
    assert calls == [('Tor Project', {'onion': True})]
    assert 'through Tor' in studio.detail_status.get()
    studio.again()
    assert calls[-1] == ('Tor Project', {'onion': True})


def test_browser_handoff_runs_off_tk_and_reports_failed_launch_honestly(studio, monkeypatch):
    import threading
    import time
    calls = []
    def launch(url, required):
        calls.append((url, required, threading.current_thread().name))
        return {'opened': False, 'reason': 'Browser unavailable in fixture'}
    monkeypatch.setattr(studio.jobs, 'research', lambda: SimpleNamespace(search_browser=launch))
    studio.search_kind.set('Onion (Tor)')
    studio.query.insert(0, 'public topic')
    studio.browse_search()
    deadline = time.monotonic() + 3
    while studio._browser_pending and time.monotonic() < deadline:
        studio.update()
        time.sleep(.02)
    assert calls and calls[0][1] is True and calls[0][2] == 'hayat-search-browser'
    assert 'Browser unavailable' in studio.detail_status.get()
    assert not studio._browser_pending and not studio.browse_button.instate(['disabled'])


def test_long_browser_guidance_wraps_without_hiding_the_answer_at_compact_size(studio):
    studio.geometry('720x560-4000-4000')
    studio.detail_status.set('Search browser opened. Read the source in the browser. Complete any publisher sign-in '
                             'or human check yourself, then provide a selected public source URL for HAYAT to research. '
                             'Opening the browser does not import its contents.')
    studio.update()
    assert int(studio.status_label.cget('wraplength')) <= studio.status_label.winfo_width()
    assert studio.status_label.winfo_height() > 20
    assert studio.result.winfo_height() >= 60


def test_compact_person_form_with_large_headings_keeps_reading_area_and_all_columns(studio):
    from tkinter import font as tkfont
    style = ttk.Style(studio)
    original_font = style.lookup('Treeview.Heading', 'font')
    original_height = style.lookup('Treeview', 'rowheight')
    try:
        style.configure('Treeview.Heading', font=('Segoe UI Semibold', 16))
        style.configure('Treeview', rowheight=32)
        studio.geometry('720x560-4000-4000')
        studio.search_kind.set('Person (public work)')
        studio._show_person_fields()
        studio.update()
        studio._fit_table()
        studio.update()
        assert int(studio.tree['height']) == 1
        assert studio.result.winfo_height() >= 60
        heading = tkfont.Font(root=studio, font=style.lookup('Treeview.Heading', 'font'))
        for column in studio.tree['columns']:
            assert studio.tree.column(column, 'width') >= heading.measure(studio.tree.heading(column, 'text')) + 18
        studio.tree.xview_moveto(1)
        assert studio.tree.xview()[1] >= .99
    finally:
        style.configure('Treeview.Heading', font=original_font)
        style.configure('Treeview', rowheight=original_height)


def test_polling_preserves_answer_scroll_selection_and_job_list(studio):
    select(studio, 'job-12')
    studio.tree.yview_moveto(.4)
    studio.result.yview_moveto(.5)
    studio.result.tag_add('sel', '100.0', '100.8')
    studio.update()
    row_view = studio.tree.yview()
    line = studio.result.index('@0,0')
    ranges = studio.result.tag_ranges('sel')
    for _ in range(3):
        studio.refresh()
        studio.update()
    assert studio.result.index('@0,0') == line
    assert studio.result.tag_ranges('sel') == ranges
    assert studio.tree.selection() == ('job-12',)
    assert studio.tree.yview() == pytest.approx(row_view)
    assert studio.cancel_button.instate(['disabled'])


def test_running_search_updates_status_without_replacing_text_then_shows_answer(studio):
    job = studio.jobs.get('job-29')
    job.status = 'running'
    select(studio, job.job_id)
    studio.result.tag_add('sel', '1.0', '1.5')
    job._started -= 5
    studio.refresh()
    assert studio.result.tag_ranges('sel')
    assert 'elapsed' in studio.detail_status.get()
    assert not studio.cancel_button.instate(['disabled'])
    job.status = 'done'
    studio.refresh()
    assert 'Evidence 199' in studio.result.get('1.0', 'end')
    assert not studio.result.tag_ranges('sel')


def test_depth_search_delivery_image_and_cancellation_controls(studio, monkeypatch):
    def start(query, *, asked, level):
        job = ResearchJob('new-job', query, asked, level, '')
        studio.jobs._jobs.append(job)
        return job
    monkeypatch.setattr(studio.jobs, 'start', start)
    assert studio.depth.get() == 'Deep'
    studio.depth.set('Quick')
    studio.query.insert(0, 'battery recycling')
    studio.search()
    job = studio.jobs.get('new-job')
    assert job.level == 'quick' and job._released
    assert studio.tree.selection() == ('new-job',)
    studio.cancel()
    assert 'Search cancelled' in studio.result.get('1.0', 'end')
    studio.add_image()
    assert studio.gui.images == ['image']
    assert studio.state() == 'withdrawn'


def test_cancelling_image_picker_returns_to_search_and_keeps_query(studio):
    studio.query.insert(0, 'known research topic')
    studio.gui.image_added = False
    studio.add_image()
    studio.update()
    assert studio.state() == 'normal'
    assert studio.query.get() == 'known research topic'


def test_public_person_form_sends_and_retries_the_same_explicit_scope(studio, monkeypatch):
    calls = []
    def start(query, *, asked, level, person):
        calls.append((query, level, dict(person)))
        job = ResearchJob('person-job', query, asked, level, '', person=dict(person))
        studio.jobs._jobs.append(job)
        return job
    monkeypatch.setattr(studio.jobs, 'start', start)
    studio.search_kind.set('Person (public work)')
    studio._show_person_fields()
    studio.query.insert(0, 'Jane Example')
    studio.person_context.set('Example Institute')
    studio.person_url.set('https://example.org/jane')
    studio.geometry('720x560-4000-4000')
    studio.update()
    assert studio.person_fields.winfo_ismapped() and studio.result.winfo_height() >= 60
    studio.search()
    assert calls[0][2] == {'name': 'Jane Example', 'context': 'Example Institute', 'profile_url': 'https://example.org/jane'}
    studio.again()
    assert calls[1][1] == 'deep' and calls[1][2] == calls[0][2]
    studio.query.insert(0, 'Jane Example')
    studio.person_context.set('home address')
    studio.search()
    assert len(calls) == 2 and studio.query.get() == 'Jane Example'
    assert 'outside this search' in studio.detail_status.get()


def test_destroying_search_window_removes_listener_and_poll(studio):
    handle = studio._poll_job
    assert studio.jobs._listeners and handle is not None
    studio.destroy()
    assert not studio.jobs._listeners
    assert handle not in studio.tk.call('after', 'info')


@pytest.mark.parametrize('mode', ['PRIVATE', 'LOCAL', 'LOCAL_ONLY', 'privacy'])
def test_search_controls_respect_privacy_and_keep_the_draft(studio, mode, monkeypatch):
    studio.gui.runtime.session = SimpleNamespace(effective_modes=[mode] if mode != 'privacy' else [])
    studio.gui.runtime.authority = SimpleNamespace(privacy_mode=mode == 'privacy')
    def must_not_start(*args, **kwargs):
        pytest.fail('search must not start while privacy mode is on')
    monkeypatch.setattr(studio.jobs, 'start', must_not_start)
    select(studio, 'job-29')
    studio.query.insert(0, 'a research topic')
    studio.search()
    studio.again()
    assert studio.query.get() == 'a research topic'
    assert 'local-only mode is on' in studio.detail_status.get()


def test_searches_visual_review(studio):
    # The query, depth and start control must fit together at the normal size.
    assert studio.query.winfo_width() > 200
    assert studio.depth_choice.winfo_rootx() + studio.depth_choice.winfo_width() < studio.winfo_rootx() + studio.winfo_width()
    if os.environ.get('HAYAT_SHOTS') != '1':
        return
    from tests.test_gui_screenshots import _grab, _pump
    studio.gui.root.geometry('960x760+0+0')
    studio.geometry('860x700+40+30')
    studio.deiconify()
    studio.lift()
    studio.attributes('-topmost', True)
    select(studio, 'job-29')
    _pump(SimpleNamespace(root=studio), .5)
    _grab(SimpleNamespace(root=studio), '34-searches-deep-research')
    studio.search_kind.set('Onion (Tor)')
    studio._show_person_fields()
    studio.query.insert(0, 'Tor Project')
    job = studio.jobs.get('job-29')
    job.onion = True
    job.query = 'Tor Project — display verification fixture'
    job.answer = ('Public onion research example\n\nSources and citations appear here after Tor-bound retrieval.\n'
                  'Discovery coverage and unavailable indexes stay visible.\n\n'
                  'Local search history and transfer logs can retain request URLs.')
    studio.refresh()
    _pump(SimpleNamespace(root=studio), .3)
    _grab(SimpleNamespace(root=studio), '78-onion-search-controls')
    studio.geometry('720x560+40+30')
    studio.detail_status.set('Search browser opened. Read the source in the browser. Complete any publisher sign-in '
                             'or human check yourself, then provide a selected public source URL for HAYAT to research. '
                             'Opening the browser does not import its contents.')
    _pump(SimpleNamespace(root=studio), .3)
    assert studio.result.winfo_height() >= 60
    _grab(SimpleNamespace(root=studio), '82-search-browser-guidance-compact')
