from pathlib import Path

import pytest

from scripts.verify_pending_checks import check_result, selected_checks


def test_selects_only_the_requested_skipped_checks(tmp_path):
    report = tmp_path / "baseline.xml"
    report.write_text('''<testsuites><testsuite>
      <testcase classname="tests.test_jobs" name="test_a"><skipped message="screenshots on request"/></testcase>
      <testcase classname="tests.test_live_brain" name="test_b"><skipped message="live check"/></testcase>
      <testcase classname="tests.test_jobs" name="test_pass"/>
      </testsuite></testsuites>''')
    assert selected_checks(report, "screenshots") == ["tests/test_jobs.py::test_a"]
    assert selected_checks(report, "live") == ["tests/test_live_brain.py::test_b"]
    assert len(selected_checks(report, "all")) == 2


def test_a_foreign_module_cannot_be_selected_as_a_pending_check(tmp_path):
    report = tmp_path / "baseline.xml"
    report.write_text('<testsuite><testcase classname="app.runtime" name="execute"><skipped/></testcase></testsuite>')
    with pytest.raises(ValueError, match="local test module"):
        selected_checks(report, "all")


@pytest.mark.parametrize("child,complete", [("", True), ('<skipped message="still not enabled"/>', False),
                                           ('<failure message="real failure"/>', False), ('<error message="fixture error"/>', False)])
def test_a_rerun_cannot_claim_success_for_checks_that_failed_or_stayed_skipped(tmp_path, child, complete):
    report = tmp_path / "result.xml"
    report.write_text(f'<testsuite><testcase classname="tests.test_jobs" name="example">{child}</testcase></testsuite>')
    assert check_result(report, 1)["complete"] is complete
    assert not check_result(report, 2)["complete"]


def test_missing_results_do_not_pass(tmp_path):
    assert not check_result(Path(tmp_path / "missing.xml"), 1)["complete"]


def test_failed_reruns_select_both_failures_and_fixture_errors_only(tmp_path):
    report = tmp_path / "failed.xml"
    report.write_text('''<testsuite>
      <testcase classname="tests.test_live_brain" name="test_failed"><failure/></testcase>
      <testcase classname="tests.test_live_brain" name="test_error"><error/></testcase>
      <testcase classname="tests.test_live_brain" name="test_skipped"><skipped/></testcase>
      <testcase classname="tests.test_live_brain" name="test_passed"/>
    </testsuite>''')
    assert selected_checks(report, "live", "failed") == [
        "tests/test_live_brain.py::test_failed", "tests/test_live_brain.py::test_error"]


@pytest.mark.parametrize("reply,correct", [("Five cents", True), ("5 cents", True), ("The ball costs $0.05.", True),
                                          ("Ten cents", False), ("The ball costs $0.10.", False)])
def test_the_live_price_curveball_accepts_equivalent_correct_amounts(reply, correct):
    from tests.test_live_chat_hard import CASES
    check = next(check for name, _prompt, check, _meaning in CASES if name == "trick question")
    assert bool(check(reply)) is correct
