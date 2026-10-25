from types import SimpleNamespace

from app.gui import HayatGUI
from app.gui_evidence import EvidenceCard, outcome_evidence
from app.modes import ModeManager


def test_evidence_distinguishes_checked_tools_from_verified_goal():
    outcome = SimpleNamespace(complete=True, success_criteria=[], goal_verification={'passed': True},
        history=[{'tool': 'read_file', 'result': {'duration_ms': 40},
                  'verification': {'passed': True, 'reason': 'Read-back contains expected text'}}])
    evidence = outcome_evidence(outcome)
    assert not evidence['goal_verified']
    assert 'no separate check' in evidence['label']
    assert evidence['checked'] == 1 and evidence['tool_ms'] == 40
    outcome.success_criteria = [{'type': 'content_contains'}]
    assert outcome_evidence(outcome)['goal_verified']
    outcome.complete = False
    assert not outcome_evidence(outcome)['goal_verified']
    assert outcome_evidence(outcome)['tone'] == 'attention'


def test_god_preset_uses_existing_reasoning_modes_and_survives_restart(tmp_path):
    path = tmp_path / 'modes.json'
    modes = ModeManager(path)
    modes.set('god mode')
    assert modes.snapshot()['configured'] == ['DEEP', 'ADVERSARIAL']
    assert ModeManager(path).snapshot()['configured'] == ['DEEP', 'ADVERSARIAL']
    modes.set(['PRIVATE', 'LOCAL_ONLY', 'GOD'])
    assert modes.snapshot()['configured'] == ['PRIVATE', 'LOCAL_ONLY', 'DEEP', 'ADVERSARIAL']


def test_checked_customer_tool_does_not_claim_missing_work_is_delivered():
    outcome = SimpleNamespace(complete=True, history=[{'tool': 'customer_work',
        'result': {'success': True, 'output': {'prepared': False}}, 'verification': {'passed': True}}])
    evidence = outcome_evidence(outcome)
    assert evidence['tone'] == 'attention' and not evidence['goal_verified']
    assert 'needs input' in evidence['label']


def test_evidence_card_expands_actual_checks_and_collapses(shared_tk):
    gui = SimpleNamespace(**{name: getattr(HayatGUI, name) for name in
                            ('PANEL_2', 'LINE', 'OLIVE_SOFT', 'GOLD', 'MUTED', 'BLACK', 'WHITE')})
    outcome = SimpleNamespace(complete=False, history=[{'tool': 'read_file',
        'result': {'success': False}, 'verification': {'passed': False, 'reason': 'File was not found'}}])
    card = EvidenceCard(shared_tk, gui, outcome_evidence(outcome))
    try:
        assert not card.details.winfo_manager()
        card.toggle_details()
        assert card.details.winfo_manager() == 'pack'
        assert 'File was not found' in card.details.get('1.0', 'end')
        card.toggle_details()
        assert not card.details.winfo_manager()
    finally:
        card.destroy()
