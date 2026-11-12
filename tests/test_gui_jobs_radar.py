from __future__ import annotations

import os
import threading
from types import SimpleNamespace

import pytest

from tests.test_job_hunting_upgrade import hunting as hunting, jobs_window as jobs_window, pump


def ready(window):
    window.tabs.select(6)
    pump(window, lambda: bool(window.radar.report))
    return window.radar


def test_radar_tab_runs_real_scenario_and_keeps_cv_unchanged(jobs_window):
    window = jobs_window
    panel = ready(window)
    before = window.center.profile.path.read_bytes()
    panel.skills.set("Snowflake")
    panel.hours.set("4")
    panel.run_button.invoke()
    pump(window, lambda: panel.report["scenario"]["skills"] == ["Snowflake"])
    assert window.center.profile.path.read_bytes() == before
    assert panel.metrics["roles"].get() == "2"
    assert panel.tree.get_children()
    row = panel.report["opportunities"][0]
    assert row["scenario_score"] > row["score"]
    assert "Remaining in scenario" in panel.detail.get("1.0", "end")
    assert "Forecast unavailable" in panel.forecast.get("1.0", "end")
    assert "14-day windows" in panel.forecast.get("1.0", "end")


def test_skill_recommendations_drive_inputs_and_open_only_curated_guide(jobs_window):
    panel = ready(jobs_window)
    panel.skills_tree.selection_set("Snowflake")
    panel.add_skill()
    assert panel.skills.get() == "Snowflake"
    opened = []
    jobs_window.center.opener = lambda url: opened.append(url)
    panel.open_guide()
    assert opened == ["https://docs.snowflake.com/en/user-guide-getting-started"]


def test_bad_scenario_retains_labelled_previous_snapshot(jobs_window):
    panel = ready(jobs_window)
    snapshot = panel.report
    panel.skills.set("imaginary guaranteed mastery")
    panel.refresh()
    pump(jobs_window, lambda: "Unknown scenario skill" in panel.note.get())
    assert panel.report is snapshot
    assert "Previous snapshot retained" in panel.note.get()


def test_stop_cancels_worker_without_blocking_tk(jobs_window, monkeypatch):
    panel = ready(jobs_window)
    entered = threading.Event()
    def slow(*args, **kwargs):
        entered.set()
        while kwargs["check"]():
            entered.wait(.01)
            import time
            time.sleep(.01)
        raise RuntimeError("Career analysis cancelled")
    monkeypatch.setattr(jobs_window.center, "career_radar", slow)
    panel.refresh()
    pump(jobs_window, entered.is_set)
    panel.stop_button.invoke()
    pump(jobs_window, lambda: "cancelled" in panel.note.get())


def test_suggested_query_filters_saved_results_without_network(jobs_window, monkeypatch):
    panel = ready(jobs_window)
    def unexpected(**kwargs):
        raise AssertionError("Selecting a suggestion must not start a network search")
    monkeypatch.setattr(jobs_window.center, "search", unexpected)
    panel.search_term.set("Data Analyst")
    panel.use_query()
    pump(jobs_window, lambda: jobs_window.tabs.index("current") == 0)
    assert jobs_window.query.get() == "Data Analyst"


def test_forecast_chart_uses_model_distribution_and_no_fake_empty_bars(jobs_window):
    from app.jobs.outcomes import predictive_counts
    panel = ready(jobs_window)
    assert not panel.forecast_chart.find_withtag("forecast_bar")
    value = {**panel.report, "outcomes": {**panel.report["outcomes"], "forecast": predictive_counts(4, 12)}}
    panel.show(value)
    panel.tabs.select(2)
    jobs_window.update()
    assert len(panel.forecast_chart.find_withtag("forecast_bar")) == 11
    panel.inspect_forecast(SimpleNamespace(x=50))
    assert "model mass" in panel.forecast_hint.get()
    assert "not measured" in panel.forecast_hint.get()


def test_auto_default_keeps_linkedin_as_a_platform_handoff(jobs_window, monkeypatch):
    calls = []
    monkeypatch.setattr(jobs_window, "run", lambda kind, call, busy="": call())
    monkeypatch.setattr(jobs_window.center, "browser_search", lambda *args, **kwargs: calls.append((args, kwargs)))
    jobs_window.browser_platform.set("LinkedIn")
    assert jobs_window.browser_engine.get() == "Auto"
    jobs_window.browse_platform()
    assert calls[0][0][0] == "LinkedIn" and calls[0][1]["engine"] == "DuckDuckGo"


@pytest.mark.skipif(os.environ.get("HAYAT_SHOTS") != "1", reason="opt-in Jobs radar captures")
def test_capture_career_radar_at_laptop_and_compact_sizes(jobs_window):
    from tests.test_gui_screenshots import _grab
    window = jobs_window
    panel = ready(window)
    panel.skills.set("Snowflake")
    panel.refresh()
    pump(window, lambda: panel.report["scenario"]["skills"] == ["Snowflake"])
    window.deiconify()
    for width, height in ((1180, 760), (820, 680)):
        window.geometry(f"{width}x{height}+30+30")
        window.update()
        assert panel.run_button.winfo_width() > 20
        assert panel.run_button.winfo_rootx() + panel.run_button.winfo_width() <= window.winfo_rootx() + window.winfo_width()
        assert panel.detail.winfo_height() > 80
        _grab(SimpleNamespace(root=window), f"120-career-radar-{width}")
        if width == 820:
            page = window.pages[-1]
            page.canvas.yview_moveto(1)
            window.update()
            _grab(SimpleNamespace(root=window), "120-career-radar-820-evidence")
            page.canvas.yview_moveto(0)
    window.geometry("1180x760+30+30")
    for index, name in ((1, "learn"), (2, "forecast")):
        panel.tabs.select(index)
        window.update()
        _grab(SimpleNamespace(root=window), "121-career-radar-" + name)
