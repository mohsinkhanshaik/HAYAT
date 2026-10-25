"""Authorization curveballs, auditable history, ranking and the real Jobs window."""
import os
import json
import threading
import time
from types import SimpleNamespace

import pytest

from app.jobs.center import JobCenter
from app.jobs.profile import WORK_STATUSES
from app.jobs.sponsorship import history_for, import_history, research_lines
from app.jobs.tools import job_request
from app.jobs.understand import authorization, excludes_future_sponsorship
from tests.test_jobs import CV, Fetcher, posting


@pytest.fixture
def hunting(tmp_path):
    center = JobCenter(tmp_path / "jobs", Fetcher())
    cv = tmp_path / "cv.txt"
    cv.write_text(CV, encoding="utf-8")
    center.read_cv(str(cv))
    center.set_preferences({"target_titles": ["Data Analyst"], "sources": ["remoteok"],
                            "locations": ["US"], "work_status": "stem_opt"})
    return center


@pytest.mark.parametrize("text,expected", [
    ("Visa sponsorship is available.", "offered"),
    ("We can sponsor visas for this role.", "offered"),
    ("We do not sponsor employment visas.", "not offered"),
    ("We do not currently sponsor visas.", "not offered"),
    ("We are not able to offer visa sponsorship.", "not offered"),
    ("Sponsorship is unavailable.", "not offered"),
    ("No visa sponsorship.", "not offered"),
    ("No visa sponsorship is available.", "not offered"),
    ("We are not willing to sponsor visas.", "not offered"),
    ("Must be authorized to work in the US without sponsorship.", "not offered"),
    ("We sponsor visas globally. Sponsorship is not available for this role.", "conflicting"),
    ("Do you now or in the future require visa sponsorship?", "not stated"),
    ("No visa sponsorship is required to apply.", "not stated"),
    ("Prior experience with sponsorship programs is useful.", "not stated"),
    ("We offer a relocation package.", "not stated"),
])
def test_visa_statements_do_not_turn_questions_or_negations_into_promises(text, expected):
    assert authorization(text)[0] == expected


@pytest.mark.parametrize("text,required", [
    ("US citizenship required.", True),
    ("U.S. citizenship is required.", True),
    ("US citizenship not required.", False),
    ("No US citizenship required.", False),
    ("Must be a US citizen or permanent resident.", False),
    ("Must be a citizen of Canada.", False),
    ("Security clearance required.", False),
])
def test_citizenship_alternatives_are_not_silently_discarded(text, required):
    assert authorization(text)[2] is required


def test_future_sponsorship_is_distinct_from_current_authorization(hunting):
    number = hunting.store.upsert([posting(description="Must be authorized to work without sponsorship now or in the future.")])[0]
    hunting.rescore()
    assert hunting.store.job(number)["verdict"] == "not eligible"
    assert "future" in " ".join(hunting.store.job(number)["match"]["blocked"])
    hunting.set_preferences({"work_status": "citizen"})
    assert hunting.store.job(number)["verdict"] != "not eligible"
    hunting.set_preferences({"work_status": "stem_opt"})
    hunting.store.upsert([posting(description="Visa sponsorship is not available.")])
    hunting.rescore()
    assert hunting.store.job(number)["verdict"] != "not eligible"  # Current authorization is not an H-1B petition.


def test_future_sponsorship_questions_are_not_employer_restrictions():
    assert not excludes_future_sponsorship("Can you work without sponsorship now or in the future?")
    assert not excludes_future_sponsorship("Will you require sponsorship now or in the future?")
    assert excludes_future_sponsorship("Applicants must not require sponsorship now or in the future.")


def test_work_status_is_explicit_validated_and_persisted(hunting):
    for key in WORK_STATUSES:
        hunting.set_preferences({"work_status": key})
        assert hunting.profile.load()["preferences"]["work_status"] == key
    before = hunting.profile.path.read_bytes()
    with pytest.raises(ValueError, match="Unknown work status"):
        hunting.set_preferences({"work_status": "approved by AI"})
    assert hunting.profile.path.read_bytes() == before
    hunting.set_preferences({"work_status": "H-1B"})
    assert hunting.profile.load()["preferences"]["work_status"] == "h1b"


HEADER = "Employer,Fiscal Year,Initial Approval,Initial Denial,Continuing Approval,Continuing Denial\n"


def test_employer_history_has_exact_identity_fiscal_years_and_provenance(hunting, tmp_path):
    csv = tmp_path / "uscis-extract.csv"
    csv.write_text(HEADER + "Acme Data Inc.,2025,10,2,5,1\nAcme Data Inc.,2026,8,1,7,0\nOther Entity,2026,0,1,2,0\n")
    imported = hunting.import_sponsors(str(csv))
    assert imported["rows"] == 3 and imported["employers"] == 2 and len(imported["sha256"]) == 64
    report = hunting.sponsorship(company="ACME DATA INC")
    assert report["history"]["record"]["years"]["2026"]["initial_approval"] == 8
    assert "authenticity has not been verified" in "\n".join(research_lines(report))
    assert hunting.sponsorship(company="Acme Data")["history"]["status"] == "no exact match"
    assert hunting.sponsorship(company="Other Entity")["posting_sponsorship"] == "not stated"
    candidates = hunting.sponsorship()["sponsor_candidates"]
    assert len(candidates) == 1 and candidates[0]["fiscal_year"] == "2026"
    assert candidates[0]["company"] == "Acme Data Inc."  # Zero initial approvals are not presented as positive leads.


@pytest.mark.parametrize("content", [
    HEADER + "Acme,2026,-1,0,0,0\n", HEADER + "Acme,2026,,0,0,0\n",
    HEADER + "Acme,2026,yes,0,0,0\n", HEADER + "Acme,2026,1.5,0,0,0\n",
    HEADER + "Acme,unknown,1,0,0,0\n", "Employer,Case Status\nAcme,Certified\n", HEADER,
])
def test_bad_history_never_replaces_existing_records(tmp_path, content):
    destination, source = tmp_path / "history.json", tmp_path / "bad.csv"
    destination.write_text('{"owner":"keep"}')
    source.write_text(content)
    with pytest.raises(ValueError):
        import_history(source, destination)
    assert destination.read_text() == '{"owner":"keep"}'


def test_missing_or_damaged_history_is_unknown_not_negative_evidence(tmp_path):
    path = tmp_path / "history.json"
    assert history_for(path, "Acme")["status"] == "not loaded"
    path.write_text("{broken")
    assert history_for(path, "Acme")["status"] == "unreadable"
    path.write_text('{"companies":[]}')
    assert history_for(path, "Acme")["status"] == "unreadable"


def test_research_quotes_posting_and_never_confuses_history_with_an_offer(hunting):
    number = hunting.store.upsert([posting(description="STEM OPT candidates welcome. Visa sponsorship is not available. Travel 20%. E-Verify participation confirmed by HR only.")])[0]
    report = hunting.sponsorship(number)
    assert "stem_opt" in report["visa_types_named"] and report["posting_sponsorship"] == "not offered"
    assert report["history"]["status"] == "not loaded" and report["evidence"]
    text = "\n".join(research_lines(report))
    assert "20 hours/week" in text and "I-983" in text and "does not establish STEM OPT eligibility" in text
    assert all(source["url"].startswith("https://") for source in report["sources"])
    assert "No probability" in report["basis"]


def test_long_posting_excerpts_include_the_authorization_statement(hunting):
    number = hunting.store.upsert([posting(description="About our team " * 400 + "Visa sponsorship is not available.")])[0]
    evidence = hunting.sponsorship(number)["evidence"]
    assert len(evidence) == 1 and "Visa sponsorship is not available" in evidence[0] and evidence[0].startswith("…")


def test_pay_sorting_keeps_currencies_separate_and_filters_before_limiting(hunting):
    rows = [posting(key="remoteok:year", title="Data Analyst Yearly", salary_min=100000, salary_max=120000, currency="USD", salary_period="year"),
            posting(key="remoteok:hour", title="Data Analyst Hourly", salary_min=60, salary_max=60, currency="USD", salary_period="hour"),
            posting(key="remoteok:euro", title="Data Analyst EUR", salary_min=900000, salary_max=900000, currency="EUR", salary_period="year"),
            posting(key="remoteok:unknown", title="Data Analyst Unknown", description="Remote SQL and Python. Visa sponsorship is available.")]
    hunting.store.upsert(rows)
    hunting.rescore()
    matches = hunting.matches(sort_by="pay", limit=10)["matches"]
    assert [row["title"] for row in matches[:2]] == ["Data Analyst Hourly", "Data Analyst Yearly"]
    filtered = hunting.matches(sponsorship="offered", limit=1)["matches"]
    assert len(filtered) == 1 and filtered[0]["title"] == "Data Analyst Unknown"
    assert "2,080" in hunting.matches(sort_by="pay")["basis"]
    with pytest.raises(ValueError):
        hunting.matches(sort_by="hire probability")


def test_pay_and_query_filters_reach_jobs_beyond_the_top_500(hunting):
    rows = [posting(key=f"remoteok:{index}", title=f"Data Analyst {index}") for index in range(505)]
    rows.append(posting(key="remoteok:buried", title="Data Analyst Rare Specialty", salary_min=240000,
                        salary_max=260000, currency="USD", salary_period="year"))
    hunting.store.upsert(rows)
    from app.jobs.understand import understand
    for number in hunting.store.numbers():
        row = hunting.store.job(number)
        hunting.store.set_match(number, understand(row), {"score": 40 if row["key"] == "remoteok:buried" else 80,
                                                         "verdict": "stretch" if row["key"] == "remoteok:buried" else "strong"})
    assert hunting.matches(sort_by="pay", limit=1)["matches"][0]["title"] == "Data Analyst Rare Specialty"
    assert hunting.matches(query="Rare Specialty", limit=1)["matches"][0]["title"] == "Data Analyst Rare Specialty"


def test_application_packet_includes_evidence_practice_and_unverified_research(hunting):
    number = hunting.store.upsert([posting()])[0]
    hunting.rescore()
    plan = hunting.insight(number)["application_plan"]
    assert plan["state"] == "build evidence" and "Snowflake" in plan["missing_required"]
    assert any(item["skill"] == "Snowflake" and "docs.snowflake.com" in item["reference"] for item in plan["practice"])
    assert len(plan["stages"]) == 5 and plan["evidence"] and plan["questions"]
    assert all("not fetched" in item["status"] for item in plan["research"])
    assert "not a probability" in plan["basis"] and "unsent" in plan["basis"]
    from pathlib import Path
    packet = hunting.prepare(number, polish=False)
    folder = Path(packet["folder"])
    saved = json.loads((folder / "readiness.json").read_text(encoding="utf-8"))
    assert saved["missing_required"] == plan["missing_required"]
    text = (folder / "preparation.md").read_text(encoding="utf-8")
    assert "Snowflake" in text and "Before applying" in text and "Referral draft" in text
    assert hunting.store.application(number)["status"] == "prepared"


def test_application_plan_never_hides_blockers_or_cross_currency_unknowns(hunting):
    number = hunting.store.upsert([posting(description="US citizenship required. Must have SQL and Python.",
                                          salary_min=100000, salary_max=140000, currency="EUR", salary_period="year")])[0]
    hunting.rescore()
    plan = hunting.insight(number)["application_plan"]
    assert plan["state"] == "resolve blockers" and plan["blockers"]
    assert plan["compensation"]["meets_target_midpoint"] is None
    assert all(item in plan["stages"][0]["tasks"] for item in plan["blockers"])


@pytest.mark.parametrize("low,high,relation", [(100000, 140000, "entire range below target"),
                                              (140000, 180000, "range straddles target"),
                                              (160000, 200000, "published minimum meets target")])
def test_application_plan_compares_the_whole_pay_range(hunting, low, high, relation):
    hunting.set_preferences({"min_salary": 150000, "salary_currency": "USD"})
    number = hunting.store.upsert([posting(salary_min=low, salary_max=high, currency="USD", salary_period="year")])[0]
    hunting.rescore()
    plan = hunting.insight(number)["application_plan"]
    assert plan["compensation"]["target_relation"].startswith(relation)
    assert "experience" in plan["match_reasons"]


def test_company_discovery_uses_public_careers_links_and_records_their_origin():
    from app.jobs.sources import discover
    class Careers:
        def __init__(self):
            self.calls = []
        def fetch(self, url, max_bytes, accept=None):
            self.calls.append(url)
            if url == "https://employer.example/careers":
                return SimpleNamespace(content=b'<a href="https://jobs.lever.co/employer/123">Jobs</a>'
                                               b'<a href="https://jobs.lever.co.evil.example/other">Ignore</a>')
            assert url == "https://api.lever.co/v0/postings/employer?mode=json"
            return SimpleNamespace(content=json.dumps([{"id": 1, "text": "Engineer", "hostedUrl": "https://jobs.lever.co/employer/123"}]).encode())
    fetcher = Careers()
    found = discover(fetcher, "https://employer.example/careers")
    assert found[0]["follow"] == "lever:employer"
    assert found[0]["evidence"] == "linked from https://employer.example/careers"
    assert len(fetcher.calls) == 2


@pytest.mark.parametrize("url", ["https://jobs.lever.co.evil.example/acme", "https://jobs.lever.co@evil.example/acme",
                                 "http://jobs.lever.co/acme", "https://jobs.lever.co/acme%2fother"])
def test_lookalike_or_ambiguous_board_urls_do_not_become_ats_sources(url):
    from app.jobs.sources import board_from_url
    assert board_from_url(url) is None


def test_ats_base_salary_excludes_bonus_and_equity():
    from app.jobs.sources import ashby, lever
    class Pay:
        def fetch(self, url, max_bytes, accept=None):
            if "lever" in url:
                body = [{"id": 1, "text": "Engineer", "salaryRange": {"min": 90, "max": 100, "currency": "USD", "interval": "hourly"}}]
            else:
                assert url.endswith("?includeCompensation=true")
                body = {"jobs": [{"id": 2, "title": "Engineer", "isListed": True,
                                   "compensation": {"summaryComponents": [
                                       {"compensationType": "EquityPercentage", "minValue": 1, "maxValue": 5},
                                       {"compensationType": "Bonus", "minValue": 1000000, "maxValue": 2000000},
                                       {"compensationType": "Salary", "minValue": 180000, "maxValue": 220000,
                                        "currencyCode": "USD", "interval": "1 YEAR"}]}},
                                  {"id": 3, "title": "Unlisted", "isListed": False}]}
            return SimpleNamespace(content=json.dumps(body).encode())
    assert lever(Pay(), "example")[0]["salary_period"] == "hour"
    found = ashby(Pay(), "example")
    assert len(found) == 1 and found[0]["salary_max"] == 220000 and found[0]["salary_period"] == "year"


@pytest.mark.parametrize("text,deadline", [("Apply by sending your CV to jobs@example.com", False),
                                          ("Apply by email or through the form", False),
                                          ("Apply by Friday at noon", True), ("Apply by October 25, 2026", True),
                                          ("Applications close 2026-11-01", True)])
def test_application_method_is_not_mistaken_for_a_deadline(text, deadline):
    from app.jobs.insight import checklist
    assert any("deadline" in item for item in checklist(text)) is deadline


def test_refetched_posting_updates_reading_and_score(hunting):
    hunting.search(query="data analyst")
    row = hunting.store.matches(include_blocked=True)[0]
    changed = {**row, "description": "US citizenship required. SQL and Python.", "key": row["key"]}
    from unittest.mock import patch
    with patch("app.jobs.center.collect", return_value={"jobs": [changed], "sources": []}):
        hunting.search(query="data analyst", force=True)
    assert hunting.store.job(row["number"])["verdict"] == "not eligible"


def test_saved_filters_understand_multiple_terms_and_skill_tags(hunting):
    hunting.store.upsert([posting(key="remoteok:a", title="Platform Engineer", company="Acme", tags=["python"]),
                          posting(key="remoteok:b", title="Data Analyst", company="Other", tags=[]),
                          posting(key="remoteok:c", title="Accountant", company="Third", tags=[])])
    hunting.rescore()
    matches = hunting.matches(query="python, data analyst")["matches"]
    assert {row["title"] for row in matches} == {"Platform Engineer", "Data Analyst"}
    assert len(hunting.matches(query="python OR data analyst")["matches"]) == 2
    assert hunting.matches(query="Acme")["matches"][0]["company"] == "Acme"


@pytest.mark.parametrize("text,tool,args", [
    ("I'm on STEM OPT", "job_profile", {"action": "set", "preferences": {"work_status": "stem_opt"}}),
    ("my work status is H-1B", "job_profile", {"action": "set", "preferences": {"work_status": "h1b"}}),
    ("check sponsorship for job 12", "job_sponsorship", {"number": 12}),
    ("STEM OPT for job 12", "job_sponsorship", {"number": 12}),
    ("research sponsorship at Acme Data", "job_sponsorship", {"company": "Acme Data"}),
    ("show my job matches by salary", "job_matches", {"sort_by": "pay"}),
    ("find H-1B sponsors", "job_sponsorship", {}),
])
def test_job_hunting_requests_route_without_a_model(text, tool, args):
    assert job_request(text) == {"tool": tool, "arguments": args}


def test_form_plan_preserves_existing_and_noneditable_answers(hunting):
    from app.jobs.apply_form import fill_plan
    fields = [{"mark": "a", "label": "Email", "type": "email", "has_value": True},
              {"mark": "b", "label": "Phone", "type": "tel", "disabled": True},
              {"mark": "c", "label": "Name", "type": "text", "read_only": True}]
    plan = fill_plan(fields, hunting.profile.load(), "Example letter")
    assert not plan["fill"] and len(plan["left"]) == 3


def test_sponsorship_and_sorting_are_accessible_through_the_real_tool_registry(hunting, tmp_path):
    from app.jobs.tools import register_job_tools, report
    from app.safety.permissions import PermissionManager
    from app.tools.registry import ToolRegistry
    registry = ToolRegistry(PermissionManager({"default": "ALLOW"}, tmp_path))
    register_job_tools(registry, hunting)
    number = hunting.store.upsert([posting(description="STEM OPT candidates welcome. Visa sponsorship is available.")])[0]
    hunting.rescore()
    answer = registry.execute("job_sponsorship", {"number": number})
    assert answer.success and answer.output["posting_sponsorship"] == "offered"
    history = [{"tool": "job_sponsorship", "result": {"success": True, "output": answer.output}, "verification": {"passed": True}}]
    assert "Sponsorship research" in report(history)
    matches = registry.execute("job_matches", {"sort_by": "pay", "remote_only": True, "sponsorship": "offered"})
    assert matches.success and matches.output["matches"][0]["number"] == number


def pump(root, condition, seconds=12):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        root.update()
        if condition():
            return
        time.sleep(.015)
    status = root.status.cget("text") if hasattr(root, "status") else ""
    raise AssertionError("Jobs UI did not reach the expected state. Status: " + status)


@pytest.fixture
def jobs_window(shared_tk, hunting):
    from app.gui_jobs import JobsWindow
    from app.gui_theme import Palette, style_controls
    from tkinter import ttk
    hunting.store.upsert([posting(), posting(key="remoteok:second", title="Data Analyst II", company="Second Company")])
    hunting.rescore()
    class GUI(Palette):
        pass
    gui = GUI()
    gui.root, gui.runtime = shared_tk, SimpleNamespace(jobs=hunting)
    style = ttk.Style(shared_tk)
    old_theme = style.theme_use()
    style.theme_use("clam")
    style_controls(shared_tk, gui)
    window = JobsWindow(gui)
    try:
        pump(shared_tk, lambda: bool(window.match_tree.get_children()))
        yield window
    finally:
        window.close()
        style.theme_use(old_theme)


def test_jobs_gui_shows_sponsorship_pay_scrollbars_and_ignores_stale_details(jobs_window):
    window = jobs_window
    iid = window.match_tree.get_children()[0]
    window.match_tree.selection_set(iid)
    window.match_selected()
    pump(window, lambda: "I-983" in window.sponsorship_text.get("1.0", "end"))
    assert window.detail.vbar.winfo_exists() and window.match_tree.cget("xscrollcommand")
    assert "Pay" == window.match_tree.heading("pay")["text"]
    old = window.detail.get("1.0", "end")
    report = window.center.detail(int(iid))
    report["job"]["number"] = 999999
    window.show_detail(report)
    assert window.detail.get("1.0", "end") == old
    window.tabs.select(2)
    pump(window, lambda: window.fields["work_status"].get() == "stem_opt")
    window.geometry("980x620")
    window.update()
    assert window.profile_scroll.canvas.cget("yscrollcommand")
    window.profile_scroll.canvas.yview_moveto(1)
    window.update()
    assert window.profile_scroll.canvas.yview()[1] >= .99
    window.fields["work_status"].set("h1b")
    window.save_preferences()
    pump(window, lambda: window.center.profile.load()["preferences"]["work_status"] == "h1b")


def test_gui_discards_superseded_read_requests_without_running_them(jobs_window):
    window = jobs_window
    entered, release = threading.Event(), threading.Event()
    calls = []
    def slow():
        entered.set()
        release.wait(5)
        return window.center.matches()
    window.run("matches", slow)
    assert entered.wait(2)
    window.run("matches", lambda: calls.append("superseded") or window.center.matches())
    window.run("matches", lambda: calls.append("latest") or window.center.matches())
    release.set()
    pump(window, lambda: "latest" in calls)
    assert calls == ["latest"]


def test_gui_displays_imported_history_without_hiding_source_or_limitations(jobs_window, tmp_path):
    window = jobs_window
    source = tmp_path / "sample-history.csv"
    source.write_text(HEADER + "Acme Data,2024,12,2,3,1\n", encoding="utf-8")
    window.center.import_sponsors(str(source))
    path = window.center.folder / "sponsor-history.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["provenance"] = "Historical public mirror; not independently verified against USCIS."
    value["source"] = {"fiscal_years": [2024], "url": "https://example.test/sample-history.csv",
                       "sha256": "a" * 64, "source_rows": 2, "omitted_unidentified_rows": 1}
    path.write_text(json.dumps(value), encoding="utf-8")
    iid = next(iid for iid in window.match_tree.get_children()
               if window.center.store.job(int(iid))["company"] == "Acme Data")
    window.match_tree.selection_set(iid)
    window.match_selected()
    pump(window, lambda: "FY2024: initial approvals 12" in window.sponsorship_text.get("1.0", "end"))
    text = window.sponsorship_text.get("1.0", "end")
    assert "not independently verified" in text and "example.test/sample-history.csv" in text
    assert "unnamed petitioners omitted: 1" in text and "a" * 64 in text
    window.detail_tabs.select(2)
    window.geometry("980x620")
    window.update()
    window.sponsorship_text.yview_moveto(1)
    window.update()
    assert window.sponsorship_text.yview()[1] >= .99


def test_a_delayed_profile_read_cannot_replace_an_edit_before_save(jobs_window):
    window = jobs_window
    window.tabs.select(2)
    pump(window, lambda: window.fields["work_status"].get() == "stem_opt")
    old = window.center.show_profile()
    entered, release = threading.Event(), threading.Event()
    def delayed_read():
        entered.set()
        assert release.wait(5)
        return old
    window.run("profile", delayed_read)
    assert entered.wait(2)
    window.fields["work_status"].set("h1b")
    window.willing_to_relocate.set(True)
    release.set()
    # A later marker proves the delayed reply was drained on the Tk thread.
    displayed = []
    original = window.show_profile
    def record(value):
        original(value)
        displayed.append(True)
    window.show_profile = record
    pump(window, lambda: bool(displayed))
    assert window.fields["work_status"].get() == "h1b"
    assert window.willing_to_relocate.get()
    window.save_preferences()
    pump(window, lambda: window.center.profile.load()["preferences"]["work_status"] == "h1b")
    assert window.center.profile.load()["preferences"]["willing_to_relocate"] is True


def test_a_save_response_preserves_edits_made_while_saving(jobs_window, monkeypatch):
    window = jobs_window
    window.tabs.select(2)
    pump(window, lambda: window.fields["work_status"].get() == "stem_opt")
    original = window.center.set_preferences
    entered, release = threading.Event(), threading.Event()
    def delayed_save(changes):
        result = original(changes)
        entered.set()
        assert release.wait(5)
        return result
    monkeypatch.setattr(window.center, "set_preferences", delayed_save)
    displayed = []
    show = window.show_preferences
    def record(value):
        show(value)
        displayed.append(True)
    window.show_preferences = record
    window.fields["work_status"].set("h1b")
    window.save_preferences()
    assert entered.wait(2)
    window.fields["work_status"].set("stem_opt")
    release.set()
    pump(window, lambda: bool(displayed))
    assert window.center.profile.load()["preferences"]["work_status"] == "h1b"
    assert window.fields["work_status"].get() == "stem_opt"  # the newer draft remains unsaved
    window.save_preferences()
    pump(window, lambda: window.center.profile.load()["preferences"]["work_status"] == "stem_opt")


def test_profile_save_waits_for_a_reader_to_release_its_file_handle(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor, TimeoutError
    from pathlib import Path
    from app.jobs.profile import Profile
    profile = Profile(tmp_path / "profile.json")
    before = profile.set_preferences({"work_status": "stem_opt"})
    reading, release, saving = threading.Event(), threading.Event(), threading.Event()
    original = Path.read_text
    def held_read(path, *args, **kwargs):
        if path == profile.path and threading.current_thread().name.endswith("_0"):
            with path.open(encoding="utf-8") as handle:
                reading.set()
                assert release.wait(5)
                return handle.read()
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", held_read)
    after = {**before, "preferences": {**before["preferences"], "work_status": "h1b"}}
    def save():
        saving.set()
        profile.save(after)
    with ThreadPoolExecutor(max_workers=2) as pool:
        reader = pool.submit(profile.load)
        assert reading.wait(2)
        writer = pool.submit(save)
        assert saving.wait(2)
        try:
            with pytest.raises(TimeoutError):
                writer.result(timeout=.15)
        finally:
            release.set()
        assert reader.result(timeout=2)["preferences"]["work_status"] == "stem_opt"
        writer.result(timeout=2)
    assert profile.load()["preferences"]["work_status"] == "h1b"


@pytest.mark.skipif(os.environ.get("HAYAT_SHOTS") != "1", reason="screenshots on request")
def test_capture_job_hunting_gui(jobs_window):
    from tests.test_gui_screenshots import _grab, _pump
    window = jobs_window
    window.geometry("1180x760+0+0")
    window.attributes("-topmost", True)
    iid = window.match_tree.get_children()[0]
    window.match_tree.selection_set(iid)
    window.match_selected()
    pump(window, lambda: "I-983" in window.sponsorship_text.get("1.0", "end"))
    window.detail_tabs.select(2)
    _pump(window.gui, .4)
    _grab(window.gui, "58-job-sponsorship", window)
    window.tabs.select(2)
    window.geometry("980x620+0+0")
    _pump(window.gui, .4)
    _grab(window.gui, "59-job-profile-compact", window)
    window.profile_scroll.canvas.yview_moveto(1)
    _pump(window.gui, .3)
    _grab(window.gui, "61-job-profile-controls", window)
    window.tabs.select(5)
    _pump(window.gui, .4)
    _grab(window.gui, "60-job-sources-compact", window)
    window.tabs.select(0)
    window.detail_tabs.select(3)
    window.geometry("1180x760+0+0")
    _pump(window.gui, .4)
    _grab(window.gui, "77-job-application-plan", window)


def test_jobs_plan_and_scroll_survive_a_background_table_refresh(jobs_window):
    window = jobs_window
    iid = window.match_tree.get_children()[0]
    window.match_tree.selection_set(iid)
    window.match_selected()
    pump(window, lambda: "Referral draft" in window.preparation_text.get("1.0", "end"))
    window.detail_tabs.select(3)
    window.update()
    window.preparation_text.yview_moveto(.5)
    window.update()
    position = window.preparation_text.yview()[0]
    top_line = window.preparation_text.index("@0,0")
    assert position > .1
    content = window.preparation_text.get("1.0", "end-1c")
    window.show_matches(window.center.matches(limit=200))
    window.update()
    assert window.preparation_text.get("1.0", "end-1c") == content
    assert window.preparation_text.index("@0,0") == top_line
    # An identical report must not reset the view or cancel an upward wheel.
    window.say(window.preparation_text, content)
    assert window.preparation_text.index("@0,0") == top_line


def test_studio_windows_do_not_install_or_remove_global_wheel_handlers(jobs_window):
    from app.gui_keys import KeysWindow
    from app.gui_money_desk import MoneyDeskWindow
    import tkinter as tk
    root = jobs_window.gui.root
    before = root.bind_all("<MouseWheel>")
    keys_window = KeysWindow(jobs_window.gui)
    root.update()
    assert root.bind_all("<MouseWheel>") == before
    keys_window.destroy()
    top = tk.Toplevel(root)
    owner = SimpleNamespace(p=jobs_window.p, tabs=top, destroy=top.destroy)
    frame = MoneyDeskWindow._build_decisions(owner)
    frame.pack(fill="both", expand=True)
    root.update()
    owner.decision_canvas.event_generate("<Enter>")
    root.update()
    assert root.bind_all("<MouseWheel>") == before
    owner.decision_canvas.event_generate("<Leave>")
    root.update()
    MoneyDeskWindow.close(owner)
    assert root.bind_all("<MouseWheel>") == before


def test_application_plan_research_links_open_only_when_clicked(jobs_window):
    window = jobs_window
    opened = []
    window.center.opener = opened.append
    iid = window.match_tree.get_children()[0]
    window.match_tree.selection_set(iid)
    window.match_selected()
    pump(window, lambda: bool(window.preparation_text.tag_ranges("plan-link")))
    assert not opened
    box = window.preparation_text
    window.detail_tabs.select(3)
    window.update()
    ranges = box.tag_ranges("plan-link")
    start, end = next((start, end) for start, end in zip(ranges[::2], ranges[1::2])
                      if box.get(start, end).startswith("https://www.google.com/search?"))
    box.see(start)
    window.update()
    x, y, width, height = box.bbox(start)
    window._open_plan_link(SimpleNamespace(x=x + width // 2, y=y + height // 2))
    assert opened == [box.get(start, end)]
    assert window.center.store.application(int(iid)) is None


def test_selected_job_reports_refresh_when_the_posting_changes(jobs_window):
    window = jobs_window
    iid = window.match_tree.get_children()[0]
    window.match_tree.selection_set(iid)
    window.match_selected()
    pump(window, lambda: "Referral draft" in window.preparation_text.get("1.0", "end"))
    row = window.center.store.job(int(iid))
    window.center.store.upsert([{**row, "description": "US citizenship required. Python and SQL."}])
    window.center.rescore([int(iid)])
    window.show_matches(window.center.matches(include_blocked=True))
    pump(window, lambda: "requires US citizenship" in window.preparation_text.get("1.0", "end"))
    assert "resolve blockers" in window.preparation_text.get("1.0", "end")
