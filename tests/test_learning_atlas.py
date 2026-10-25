"""Broad learning plans and tutoring assessments remain distinct from reading activity."""
from types import SimpleNamespace

from app.learning.atlas import DOMAINS, learning_atlas
from app.learning.manager import LearningManager
from app.session import SessionState


def test_roadmap_covers_fields_and_prerequisites_without_inventing_progress(tmp_path):
    learning = LearningManager(tmp_path / 'learning.db')
    snapshot = learning_atlas(learning)
    assert len(snapshot['domains']) == 12 and snapshot['assessed'] == 0
    assert snapshot['total'] == len({topic for domain in DOMAINS for topic in domain.topics})
    assert all(domain['mastery'] is None for domain in snapshot['domains'])
    physics = next(row for domain in snapshot['domains'] for row in domain['topics'] if row['topic'] == 'Physics')
    assert physics['prerequisite_gaps'] == ['Algebra', 'Scientific method'] and not physics['ready']
    session = SessionState()
    assert 'Critical thinking' in learning.handle('what should i learn today', session)
    learning.handle('teach me Physics', session, lambda _: 'Energy changes form.\nQuestion: Give an example?')
    assert learning_atlas(learning)['assessed'] == 0
    assert learning.topic('Physics')['next_review'] is None


def test_only_a_real_answer_with_clear_verdict_updates_assessment(tmp_path):
    learning, session = LearningManager(tmp_path / 'learning.db'), SessionState()
    learning.handle('teach me Physics', session, lambda _: 'Energy changes form.\nQuestion: Give an example?')
    learning.handle('next', session, lambda _: 'Correct. Motion has kinetic energy.\nQuestion: Why?')
    assert learning.topic('Physics')['last_tested'] is None
    learning.handle('a moving ball', session, lambda _: 'Interesting example.\nQuestion: What happens when it stops?')
    assert learning.topic('Physics')['last_tested'] is None
    learning.handle('kinetic energy becomes heat', session, lambda _: 'Correct. Energy transfers.\nQuestion: Where did it go?')
    state = learning.topic('Physics')
    assert state['correct_answers'] == 1 and state['incorrect_answers'] == 0
    assert state['last_tested'] and state['next_review'] and learning_atlas(learning)['assessed'] == 1
    restored = LearningManager(learning.path)
    assert learning_atlas(restored)['assessed'] == 1


def test_hints_retries_and_unavailable_rubrics_do_not_fake_mastery(tmp_path):
    learning, session = LearningManager(tmp_path / 'learning.db'), SessionState()
    learning.handle('teach me Statistics', session, lambda _: 'A mean averages values.\nQuestion: Mean of 2 and 4?')
    original = session.pending_learning['step']
    learning.handle('hint', session, lambda _: 'Add first, then divide by the count.\nQuestion: Mean of 2 and 4?')
    assert session.pending_learning['step'] == original
    assert learning.topic('Statistics')['last_tested'] is None
    learning.handle('8', session, lambda _: 'Not quite. Divide the sum by two.\nQuestion: Mean of 2 and 4?')
    assert session.pending_learning['step'] == original
    assert learning.topic('Statistics')['incorrect_answers'] == 1
    learning.handle('stop lesson', session)
    learning.handle('quiz me on Astronomy', session)
    reply = learning.handle('Astronomy', session)
    assert 'not been scored' in reply
    assert learning.topic('Astronomy')['last_tested'] is None


def test_place_value_hint_and_grading_context_use_local_numeric_evidence(tmp_path):
    from app.learning.checks import place_value_check
    question = 'If you swap the 4 and the 5 in "345", what new number do you get, and how much does its value change?'
    check = place_value_check(question)
    assert '4 is in the tens' in check['hint'] and '5 is in the ones' in check['hint']
    assert 'becomes 354' in check['evidence'] and 'change = 9' in check['evidence']
    assert place_value_check('swap 4 and 5 in 454') is None
    assert place_value_check('swap 4 and 5 in 345.5') is None
    learning, session = LearningManager(tmp_path / 'learning.db'), SessionState()
    session.pending_learning = {'type': 'lesson', 'topic': 'Arithmetic', 'question': question, 'step': 1}
    def broken_teacher(_prompt):
        raise AssertionError('A supported hint must not depend on model arithmetic')
    assert check['hint'] in learning.handle('hint', session, broken_teacher)
    assert check['hint'] in learning.handle('hint', session, None)
    assert learning.topic('Arithmetic')['last_tested'] is None
    prompts = []
    def teacher(prompt):
        prompts.append(prompt)
        return 'Correct.\nQuestion: Why does swapping positions matter?'
    learning.handle('354, an increase of 9', session, teacher)
    assert check['evidence'] in prompts[-1]


def test_new_quizzes_use_model_feedback_and_roadmap_does_not_interrupt_lesson(tmp_path):
    learning, session = LearningManager(tmp_path / 'learning.db'), SessionState()
    learning.handle('quiz me on Biology', session, lambda _: 'Question: Why do cells need a membrane?')
    pending = dict(session.pending_learning)
    assert pending['type'] == 'lesson'
    roadmap = learning.handle('show learning roadmap', session)
    assert 'Mathematics' in roadmap and 'Statistics' in roadmap and 'not all human knowledge' in roadmap
    assert session.pending_learning == pending
    from app.intent.router import IntentRouter
    assert IntentRouter.__new__(IntentRouter).lesson_request('Show learning roadmap') == 'show learning roadmap'


def test_atlas_window_filters_prepares_actions_and_cancels_motion(shared_tk, tmp_path):
    from app.gui_learning_atlas import LearningAtlas
    from app.gui_theme import Palette
    from tests.conftest import open_window, close_window
    root = open_window(shared_tk, '1100x850-4000-4000')
    class GUI(Palette):
        def use_example(self, text):
            self.prepared = text
        def _toggle_depth_motion(self):
            self.depth_nav.motion = not self.depth_nav.motion
    gui = GUI()
    gui.root, gui.runtime = root, SimpleNamespace(learning=LearningManager(tmp_path / 'learning.db'))
    gui.depth_nav = SimpleNamespace(motion=True)
    window = LearningAtlas(gui)
    try:
        window.update()
        assert len(window.rows) == window.atlas['total']
        window.domain.set('Physical science')
        assert set(window.rows) == {'Physics', 'Chemistry', 'Astronomy'}
        window.query.set('Physics')
        assert list(window.rows) == ['Physics']
        assert 'Algebra' in window.detail.get('1.0', 'end')
        window.geometry('760x640-4000-4000')
        window.update()
        assert window._filters_compact
        assert window.motion_button.winfo_x() + window.motion_button.winfo_width() <= window.motion_button.master.winfo_width()
        window.toggle_motion()
        assert not window.sky.find_withtag('pulse')
        window.prepare('teach me ')
        assert gui.prepared == 'teach me Physics' and window._closed and window._timer is None
    finally:
        if window.winfo_exists():
            window.close()
        close_window(root)
