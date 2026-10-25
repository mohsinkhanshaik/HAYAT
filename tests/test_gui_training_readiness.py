import time
from types import SimpleNamespace

from app.gui_theme import Palette
from app.gui_training import TrainingReview
from app.training.pipeline import TrainingDataPipeline
from app.training.lifecycle import PrivateFineTuneLifecycle


def settle(root, pane):
    deadline = time.monotonic() + 5
    while pane._pending and time.monotonic() < deadline:
        root.update()
        pane.drain()
        time.sleep(.01)
    root.update()
    assert not pane._pending


def test_training_workspace_reflows_and_reports_actual_blockers(shared_tk, tmp_path):
    from tests.conftest import open_window, close_window
    training = TrainingDataPipeline(tmp_path / "training", config={})
    training.ingest("conversation", {"input": "question", "ideal_response": "unverified draft"},
                    provenance={"source": "isolated test"})
    runtime = SimpleNamespace(training=training, training_lifecycle=PrivateFineTuneLifecycle(
        training, tmp_path, config={"minimum_validated_samples": 1000}))
    root = open_window(shared_tk, "380x640-4000-4000")
    pane = TrainingReview(root, Palette, runtime, lambda _text: None)
    pane.pack(fill="both", expand=True)
    try:
        pane.refresh()
        deadline = time.monotonic() + 5
        while pane._pending and time.monotonic() < deadline:
            root.update()
            pane.drain()
            time.sleep(.01)
        assert not pane._pending
        assert "blocked" in pane.readiness.get() and "0 / 1000" in pane.readiness.get()
        assert "checkpoint missing" in pane.readiness.get()
        assert pane.current and pane.current["category"] == "conversation"
        root.update()
        for control in pane.filter_controls:
            assert control.winfo_x() + control.winfo_width() <= control.master.winfo_width()
        assert pane.filter_controls[-1].winfo_y() > pane.filter_controls[0].winfo_y()
        assert "unverified draft" in pane.text.get("1.0", "end")
        for button in pane.review_actions.items:
            assert button.winfo_x() + button.winfo_width() <= pane.review_actions.winfo_width()
            assert button.winfo_ismapped()
    finally:
        close_window(root)


def test_full_example_is_available_before_approval_and_filters_have_a_recovery_action(shared_tk, tmp_path):
    from tests.conftest import open_window, close_window
    training = TrainingDataPipeline(tmp_path / "training", config={})
    final_line = "End of the complete example: verification still required."
    sample = {"input": "Inspect every part", "ideal_response": "Long content. " * 180 + final_line,
              "messages": [{"role": "assistant", "content": "Nested content. " * 90 + final_line}]}
    training.ingest("conversation", sample, provenance={"source": "test"})
    root = open_window(shared_tk, "380x700-4000-4000")
    pane = TrainingReview(root, Palette, SimpleNamespace(training=training), lambda _: None)
    pane.pack(fill="both", expand=True)
    try:
        assert str(pane.keep.cget("state")) == "disabled"
        pane.refresh()
        assert pane.reload.instate(["disabled"])
        settle(root, pane)
        assert pane.text.get("1.0", "end").count(final_line) == 2
        assert "does not start training" in pane.status.get()
        pane.query.set("no matching record")
        pane.refresh()
        settle(root, pane)
        assert "No examples match" in pane.text.get("1.0", "end")
        assert str(pane.keep.cget("state")) == "disabled"
        pane.clear_filters.invoke()
        settle(root, pane)
        assert pane.current is not None and pane.query.get() == ""
        assert str(pane.keep.cget("state")) == "normal"
        assert training.pending_review()["total"] == 1  # filtering never approves
    finally:
        close_window(root)


def test_review_error_disables_stale_decisions_and_refresh_can_recover(shared_tk, tmp_path):
    from tests.conftest import open_window, close_window
    training = TrainingDataPipeline(tmp_path / "training", config={})
    training.ingest("conversation", {"input": "Request", "ideal_response": "Draft"}, provenance={"source": "test"})
    root = open_window(shared_tk, "480x700-4000-4000")
    pane = TrainingReview(root, Palette, SimpleNamespace(training=training), lambda _: None)
    pane.pack(fill="both", expand=True)
    try:
        pane.refresh()
        settle(root, pane)
        pane._pending = True
        pane._results.put({"error": "Temporary read failure"})
        pane.drain()
        assert pane.current is None and str(pane.keep.cget("state")) == "disabled"
        assert pane.reload.instate(["!disabled"])
        pane.reload.invoke()
        settle(root, pane)
        assert pane.current and str(pane.keep.cget("state")) == "normal"
    finally:
        close_window(root)
