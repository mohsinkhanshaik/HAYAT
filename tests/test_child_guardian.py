
import pytest

from app.learning.guardian import ChildGuardian


def test_child_mode_persists_and_blocks_computer_actions(tmp_path):
    first = ChildGuardian(tmp_path)
    first.enable("child")
    second = ChildGuardian(tmp_path)
    assert second.active
    assert second.check("LEARNING", "Teach me fractions")[0]
    allowed, reason = second.check("COMPUTER_CONTROL", "Open Chrome")
    assert not allowed and "teaching" in reason
    rows = second.report()
    assert rows[-1]["intent"] == "COMPUTER_CONTROL" and not rows[-1]["allowed"]


def test_parent_pin_is_required_when_configured(tmp_path, monkeypatch):
    monkeypatch.setenv("HAYAT_PARENT_PIN", "Correct-PIN")
    guardian = ChildGuardian(tmp_path)
    guardian.enable("teen")
    with pytest.raises(PermissionError):
        guardian.disable("wrong")
    assert guardian.active
    assert guardian.disable("Correct-PIN")["active"] is False


def test_guardian_journal_redacts_secret_shaped_text(tmp_path):
    guardian = ChildGuardian(tmp_path)
    guardian.enable()
    guardian.check("ACTION", "use token=supersecretvalue123456789")
    raw = guardian.journal_path.read_text(encoding="utf-8")
    assert "supersecretvalue123456789" not in raw
