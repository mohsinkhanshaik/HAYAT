"""Jobs controls drive actual profile, filtering and application state without sending anything."""
from __future__ import annotations

import os
from types import SimpleNamespace

from tests.test_job_hunting_upgrade import hunting as hunting, jobs_window as jobs_window, pump
from tests.test_jobs import posting


def test_setup_guidance_loads_without_visiting_profile_and_accepts_other_careers(jobs_window):
    window = jobs_window
    pump(window, lambda: "pay target" in window.setup_note.cget("text"))
    assert window.tabs.index("current") == 0
    button = next(child for child in window.setup_note.master.winfo_children()
                  if child.cget("text") == "Profile & targets")
    button.invoke()
    pump(window, lambda: window.fields["target_titles"].get() == "Data Analyst")
    window.fields["target_titles"].set("Mechanical Engineer, Product Designer")
    window.fields["search_terms"].set("mechanical engineer, product designer")
    window.fields["min_salary"].set("125000")
    window.save_preferences()
    pump(window, lambda: "Profile ready" in window.setup_note.cget("text"))
    assert "Mechanical Engineer" in window.setup_note.cget("text")
    assert window.center.profile.load()["preferences"]["min_salary"] == 125000


def test_search_carries_location_and_keeps_provider_failures_visible_after_refresh(jobs_window, monkeypatch):
    window = jobs_window
    received = []

    def search(**criteria):
        received.append(criteria)
        return {"new": 2, "matching_total": 0, "read": [
            {"source": "Working source", "kept": 2},
            {"source": "Offline source", "kept": 0, "error": "Request timed out"}],
            "empty_reason": "No jobs match these search criteria."}

    monkeypatch.setattr(window.center, "search", search)
    window.query.set("mechanical engineer")
    window.location.set("Toronto")
    window.remote.set(True)
    window.search()
    assert str(window.search_button.cget("state")) == "disabled"
    # The failure stays visible, said plainly ("Offline source failed: it took too long to answer").
    pump(window, lambda: bool(received) and "took too long" in window.search_note.cget("text")
         and str(window.search_button.cget("state")) == "normal")
    pump(window, lambda: not window.match_tree.get_children())
    assert len(received) == 1
    criteria = received[0]
    assert criteria.pop("check")() is True and callable(criteria.pop("progress"))
    assert criteria == {"query": "mechanical engineer", "location": "Toronto", "remote": True, "force": True, "depth": "deep",
                        "include_remote": True}  # remote jobs doable from Toronto count, unless unticked
    assert "Offline source" in window.search_note.cget("text")
    assert "No jobs match" in window.search_note.cget("text")
    window.clear_filters()
    pump(window, lambda: bool(window.match_tree.get_children()))
    assert not window.location.get() and not window.remote.get()


def test_search_failure_stops_progress_and_leaves_saved_jobs_available(jobs_window, monkeypatch):
    window = jobs_window

    def fail(**_criteria):
        raise RuntimeError("All providers unavailable")

    monkeypatch.setattr(window.center, "search", fail)
    window.search()
    pump(window, lambda: "All providers unavailable" in window.search_note.cget("text"))
    assert str(window.search_button.cget("state")) == "normal"
    assert window.match_tree.get_children()
    assert "saved jobs" in window.search_note.cget("text")


def test_application_controls_reflect_real_channel_and_session_setup(jobs_window):
    window = jobs_window
    assert all(str(button.cget("state")) == "disabled" for button in window.job_actions.values())
    iid = window.match_tree.get_children()[0]
    window.match_tree.selection_set(iid)
    window.match_selected()
    pump(window, lambda: "Email application" in window.application_note.cget("text"))
    assert str(window.job_actions["prepare"].cget("state")) == "normal"
    assert str(window.job_actions["open"].cget("state")) == "normal"
    assert str(window.job_actions["send"].cget("state")) == "disabled"
    assert "Connect an email account" in window.application_note.cget("text")
    number = int(iid)
    window.center.store.upsert([posting(key=window.center.store.job(number)["key"],
                                        description="Mechanical engineer. Apply on the employer careers page.")])
    window.center.rescore([number])
    window.center.browser = SimpleNamespace()
    window.show_detail(window.center.detail(number))
    assert "Web application" in window.application_note.cget("text")
    assert str(window.job_actions["fill"].cget("state")) == "normal"
    window.center.set_status(number, "applied")
    window.show_detail(window.center.detail(number))
    assert "Marked applied" in window.application_note.cget("text")
    assert window.center.store.application(number)["status"] == "applied"


def test_sources_can_be_paused_and_reenabled_from_the_gui(jobs_window):
    window = jobs_window
    window.tabs.select(5)
    pump(window, lambda: bool(window.source_tree.get_children()))
    window.source_tree.selection_set("remoteok")
    window.toggle_source()
    pump(window, lambda: "remoteok" not in window.center.profile.load()["preferences"]["sources"]
         and "remoteok" not in window._source_settings["enabled"])
    assert "Searches run when you start them" in window.source_note.cget("text")
    window.toggle_source()
    pump(window, lambda: "remoteok" in window.center.profile.load()["preferences"]["sources"])


def test_applications_have_both_scrollbars_and_keep_selection_on_refresh(jobs_window):
    window = jobs_window
    number = int(window.match_tree.get_children()[0])
    window.center.set_status(number, "saved")
    window.show_applications(window.center.applications())
    window.app_tree.selection_set(str(number))
    window.show_applications(window.center.applications())
    assert window.app_tree.selection() == (str(number),)
    assert window.app_tree.cget("yscrollcommand") and window.app_tree.cget("xscrollcommand")


def test_compact_jobs_controls_fit_and_capture_when_requested(jobs_window):
    window = jobs_window
    window.geometry("820x680+0+0")
    window.update()
    iid = window.match_tree.get_children()[0]
    window.match_tree.selection_set(iid)
    window.match_selected()
    pump(window, lambda: "Email application" in window.application_note.cget("text"))
    for button in (window.search_button, *window.job_actions.values()):
        assert button.winfo_rootx() + button.winfo_width() <= window.winfo_rootx() + window.winfo_width()
        assert button.winfo_rooty() + button.winfo_height() <= window.winfo_rooty() + window.winfo_height()
    if os.environ.get("HAYAT_SHOTS") == "1":
        from tests.test_gui_screenshots import _grab, _pump
        window.attributes("-topmost", True)
        _pump(window.gui, .3)
        _grab(window.gui, "91-jobs-guided-compact", window)
        window.tabs.select(5)
        pump(window, lambda: bool(window.source_tree.get_children()))
        _pump(window.gui, .2)
        _grab(window.gui, "92-jobs-sources-compact", window)


def test_narrow_matches_table_shows_whole_columns_and_keeps_hidden_ones_in_detail(jobs_window):
    window = jobs_window
    tree = window.match_tree
    # About the matches pane on the owner's 1536-pixel screen: all eight
    # columns ended in a header cut to "Sou".
    window._fit_match_columns(705)
    shown = list(tree["displaycolumns"])
    assert "source" not in shown and {"score", "verdict", "title", "company", "pay"} <= set(shown)
    assert sum(int(tree.column(key, "width")) for key in shown) <= 705
    window._fit_match_columns(1200)
    assert list(tree["displaycolumns"]) in (["#all"], list(window.MATCH_MIN_WIDTHS))
    assert sum(int(tree.column(key, "width")) for key in window.MATCH_MIN_WIDTHS) <= 1200
    window._fit_match_columns(300)
    assert {"source", "where", "posted"}.isdisjoint(tree["displaycolumns"])

    from app.jobs.tools import report_lines
    iid = tree.get_children()[0]
    detail = window.center.detail(int(iid))
    header = report_lines(detail)[0]
    assert detail["job"]["source_name"] in header
    if detail["job"].get("posted_at"):
        assert "posted " + str(detail["job"]["posted_at"])[:10] in header


def test_failed_sources_are_one_plain_sentence_not_raw_errors(jobs_window):
    window = jobs_window
    refused = "ConnectError: [WinError 10061] No connection could be made because the target machine actively refused it"
    window.show_search({"new": 0, "read": [{"source": name, "kept": 0, "error": refused}
                                            for name in ("remoteok", "jobicy", "himalayas")]})
    note = window.search_note.cget("text")
    assert "RemoteOK, Jobicy and Himalayas" in note and "connection was refused" in note
    assert "WinError" not in note and "ConnectError" not in note and "remoteok:" not in note
