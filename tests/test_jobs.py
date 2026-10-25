"""Jobs: public postings read and understood, the owner's CV understood, honest matching, application packets,
sending only through the confirmation window, the tracker, the automatic round, chat and the Jobs window."""
import json
import sqlite3
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.jobs.center import JobCenter, cover_letter, polished
from app.jobs.match import score
from app.jobs.profile import Profile, experience, understand_cv
from app.jobs.sources import clean, collect, relevant
from app.jobs.text import plain, skills_in
from app.jobs.tools import job_request, register_job_tools, report
from app.jobs.understand import pay_text, understand
from app.jobs.watcher import JobWatcher, round_text

NOW = datetime.now(UTC)
CV = """Aisha Rahman
Data Analyst · aisha.rahman@example.org · +91 98765 43210
linkedin.com/in/aisha-rahman-data · github.com/aisharahman

EXPERIENCE
Data Analyst, Brightcart Retail — Mar 2021 – Present
- Built Power BI dashboards and SQL pipelines on PostgreSQL for sales and inventory.
- Automated weekly reporting with Python (Pandas) and Airflow.
Junior Analyst, Metro Logistics — Jun 2019 – Feb 2021
- Excel models and Tableau reports.
Freelance Analyst — Jan 2020 – Dec 2020

EDUCATION
B.Tech in Computer Science, Osmania University, 2015 - 2019

SKILLS
Python, SQL, Pandas, Power BI, Tableau, Excel, PostgreSQL, Airflow, Statistics

LANGUAGES
English (fluent), Hindi (native)
"""
POSTING = """<p>We are hiring a <b>Data Analyst</b> to join our remote team.</p>
<h3>What you'll do</h3>
<ul><li>Build Power BI dashboards for the sales team</li><li>Automate weekly reporting with Python and Airflow</li>
<li>Present findings to senior stakeholders every month</li><li>Own the Snowflake data model and its documentation</li></ul>
<p>Success means the weekly report runs itself within your first 90 days. You will report to the Head of Analytics.</p>
<h3>Requirements</h3>
<ul><li>3+ years of experience in data analysis</li><li>Strong SQL and Python</li><li>Power BI or Tableau</li><li>Excellent communication</li>
<li>Experience with Snowflake</li></ul>
<h3>Nice to have</h3><ul><li>dbt</li><li>Airflow</li></ul>
<h3>Benefits</h3><p>Salary $70,000 - $90,000 per year. Apply by sending your CV to jobs@acme-data.com. Please include your portfolio
and use the subject line "Analyst 2026". Mention the word PINEAPPLE so we know you read this.</p>
<p>ACME will not pay fees to any third-party agency without a signed agreement.</p>"""


def posting(**extra):
    row = {"key": "remoteok:1", "source": "remoteok", "source_name": "Remote OK", "title": "Data Analyst", "company": "Acme Data",
           "location": "Remote", "remote": True, "url": "https://remoteok.com/remote-jobs/1", "apply_url": "https://remoteok.com/apply/1",
           "description": plain(POSTING), "tags": ["analyst"], "posted_at": (NOW - timedelta(days=2)).isoformat(timespec="seconds"),
           "employment_type": "", "seniority": "", "salary_min": None, "salary_max": None, "currency": "", "salary_period": ""}
    return {**row, **extra}


# ---------------------------------------------------------------------- text
def test_skills_are_read_in_their_own_spelling_and_ordinary_words_are_not_skills():
    found = skills_in("Python, SQL and Go; React Native and React; Node.js; C++, C#, .NET; Excel and Power BI. Spring 2024 start.")
    assert {"Python", "SQL", "Go", "React Native", "React", "Node.js", "C++", "C#", ".NET", "Excel", "Power BI"} <= set(found)
    assert "JavaScript" not in found and "Spring Boot" not in found  # the "js" of Node.js; a season
    ordinary = skills_in("You will excel at this; react quickly; at the helm; a chef; the assembly line; sales and inventory")
    assert not {"Excel", "React", "Helm"} & set(ordinary)


def test_html_and_broken_text_become_readable():
    assert plain("<p>Hi&nbsp;<b>there</b></p><ul><li>One</li><li>Two</li></ul><script>x()</script>") == "Hi there\n\n• One\n• Two"
    assert clean("Transportation Partners &amp; Logistics") == "Transportation Partners & Logistics"
    assert clean("الرياض".encode("utf-8").decode("latin-1")) == "الرياض"


# ---------------------------------------------------------------------- understanding a posting
def test_a_posting_is_understood_section_by_section():
    reading = understand(posting())
    assert reading["skills"]["required"] == ["Communication", "Data analysis", "Power BI", "Python", "SQL", "Snowflake", "Tableau"]
    assert reading["skills"]["nice"] == ["Airflow", "dbt"]
    assert reading["years_min"] == 3 and reading["mode"] == "remote" and reading["written_in"] == "English"
    assert reading["salary"] == {"min": 70000.0, "max": 90000.0, "currency": "USD", "period": "year", "from": "text"}
    assert pay_text(reading["salary"]) == "$70k–$90k per year"
    assert reading["apply_emails"] == ["jobs@acme-data.com"]
    assert reading["warnings"] == []  # "will not pay fees to any agency" is not a fee asked of you
    assert reading["summary"].startswith("Data Analyst at Acme Data, remote")


def test_where_level_language_and_warning_signs_are_read():
    german = posting(title="Senior Datenanalyst (m/w/d)", remote=False, location="Berlin, Germany", description=(
        "Wir suchen einen Datenanalyst für unser Team in Berlin. Du arbeitest mit SQL und Python und bist vor Ort im Büro. "
        "Wir bieten ein gutes Gehalt, und die Arbeit ist spannend für dich. Ihr Profil: Erfahrung mit der Analyse von Daten und "
        "eine Leidenschaft für Zahlen, die du mit dem Team teilst. Das ist eine Stelle bei uns vor Ort."))
    reading = understand(german)
    assert reading["level"] == "senior" and reading["written_in"] == "German" and reading["languages_needed"] == ["German"]
    assert reading["mode"] == "on-site" and "Germany" in reading["regions"]
    us = understand(posting(location="USA", description="Remote role. Must be based in the US. We cannot sponsor visas. SQL."))
    assert "US" in us["regions"] and us["sponsorship"] == "not offered"
    scam = understand(posting(description="Great pay! Selected candidates must first pay a registration fee of $50 for the starter kit. "
                                          "Contact us on Telegram. Send your bank details to start."))
    assert {"asks you to pay or buy something", "mentions a fee to apply, train or start", "contact only via a chat app",
            "asks for bank or identity details"} <= set(scam["warnings"])


# ---------------------------------------------------------------------- understanding the CV
def test_a_cv_is_understood_without_guessing():
    cv = understand_cv(CV, today=date(2026, 10, 1))
    assert cv["name"] == "Aisha Rahman" and cv["email"] == "aisha.rahman@example.org" and cv["phone"].startswith("+91")
    assert cv["links"] == ["linkedin.com/in/aisha-rahman-data", "github.com/aisharahman"]  # the email's domain is not a website
    assert cv["titles"][:3] == ["Data Analyst", "Junior Analyst", "Freelance Analyst"]  # without employer names or dates
    names = [item["name"] for item in cv["skills"]]
    assert all(item["listed"] for item in cv["skills"][:9]) and {"Python", "SQL", "Power BI", "Airflow", "PostgreSQL"} <= set(names)
    assert "Sales" not in names and "Logistics" not in names  # prose and company names, not skills
    assert cv["languages"] == ["English", "Hindi"]
    # Jun 2019 – Oct 2026 (this month counts) with an overlapping 2020 freelance role counted once; the degree's years are not work.
    assert cv["years_experience"] == pytest.approx(7.4, abs=.05) and cv["level"] == "senior"
    assert any("B.Tech" in line for line in cv["education"])
    assert experience("Engineer 2018 - 2020\nDeveloper 2019 - 2021", today=date(2026, 1, 1))["years"] == 4.0


def test_the_profile_reads_a_cv_file_and_keeps_the_owners_changes(tmp_path):
    cv_file = tmp_path / "cv.txt"
    cv_file.write_text(CV, encoding="utf-8")
    profile = Profile(tmp_path / "profile.json")
    data = profile.read_cv(cv_file)
    assert data["preferences"]["target_titles"] == ["Data Analyst"]
    assert data["preferences"]["search_terms"] == ["data analyst", "analyst"]
    assert "Data analysis" in profile.skills()  # implied by the role held
    profile.change_skill("docker", True)
    profile.change_skill("Tableau", False)
    assert "Docker" in profile.skills() and "Tableau" not in profile.skills()
    profile.set_preferences({"remote_only": "yes", "min_salary": "80000", "locations": "Hyderabad, India", "notify_score": 150})
    preferences = profile.load()["preferences"]
    assert preferences["remote_only"] is True and preferences["min_salary"] == 80000 and preferences["notify_score"] == 100
    assert preferences["locations"] == ["Hyderabad", "India"]
    with pytest.raises(ValueError, match="Unknown preference"):
        profile.set_preferences({"password": "x"})
    with pytest.raises(ValueError, match="greenhouse:name"):
        profile.set_preferences({"companies": ["stripe"]})
    with pytest.raises(ValueError, match="PDF, Word"):
        profile.read_cv(tmp_path / "profile.json")


# ---------------------------------------------------------------------- matching
def profile_data(**preferences):
    from app.jobs.profile import DEFAULT_PREFERENCES
    cv = understand_cv(CV, today=date(2026, 10, 1))
    return {"cv": cv, "preferences": {**DEFAULT_PREFERENCES, "target_titles": ["Data Analyst"], **preferences},
            "added_skills": [], "removed_skills": []}


def test_a_match_shows_every_part_and_the_honest_gaps():
    data = profile_data(locations=["Hyderabad, India"])
    skills = [item["name"] for item in data["cv"]["skills"]] + ["Data analysis"]
    result = score(posting(), understand(posting()), data, skills)
    assert result["verdict"] == "strong" and result["score"] >= 75 and not result["blocked"]
    assert set(result["parts"]) == {"skills", "duties", "title", "experience", "level", "pay", "freshness"}
    assert sum(part["weight"] for part in result["parts"].values()) == pytest.approx(1)
    assert result["missing_required"] == ["Snowflake"]  # soft skills are never "missing requirements"
    assert "Communication" in result["missing_other"]
    assert any("Snowflake" in line and "rather than claiming them" in line for line in result["advice"])


def test_hard_checks_make_a_job_not_eligible_whatever_its_score():
    data = profile_data(locations=["Hyderabad, India"], remote_only=True, exclude_companies=["Acme"])
    skills = [item["name"] for item in data["cv"]["skills"]]
    job = posting(location="USA", remote=False, description=plain(POSTING) + "\nMust be based in the US. On-site in our Austin office. "
                                                                             "Fluent German required.")
    result = score(job, understand(job), data, skills)
    assert result["verdict"] == "not eligible" and result["score"] <= 25
    joined = " | ".join(result["blocked"])
    assert "limited to US" in joined and "you want remote only" in joined and "needs German" in joined and "excluded Acme" in joined


# ---------------------------------------------------------------------- sources
class Fetcher:
    """Serves small copies of each board's real response shape."""

    def __init__(self, refuse=()):
        self.calls, self.refuse = [], set(refuse)

    def fetch(self, url, max_bytes, accept=None, request_headers=None):
        self.calls.append(url)
        if any(host in url for host in self.refuse):
            raise PermissionError(f"robots.txt disallows retrieval: {url}")
        stamp = int(NOW.timestamp())
        if "remoteok.com" in url:
            body = [{"legal": "link back"}, {"id": "11", "position": "Data Analyst", "company": "Acme Data", "url": "https://remoteok.com/11",
                                              "apply_url": "https://remoteok.com/a/11", "description": POSTING, "tags": ["analyst"],
                                              "location": "Remote", "epoch": stamp, "salary_min": 70000, "salary_max": 90000},
                    {"id": "12", "position": "Chef", "company": "Kitchen", "url": "https://remoteok.com/12", "tags": ["food"], "epoch": stamp}]
        elif "jobicy.com" in url:
            body = {"jobs": [{"id": 21, "url": "https://jobicy.com/21", "jobTitle": "Senior Data Analyst", "companyName": "Beta",
                              "jobGeo": "Anywhere", "jobDescription": "<p>SQL, Python, Looker</p>", "pubDate": NOW.isoformat()},
                             # The same job as Remote OK's, from another board: kept once.
                             {"id": 22, "url": "https://jobicy.com/22", "jobTitle": "Data Analyst", "companyName": "Acme Data",
                              "jobGeo": "Anywhere", "jobDescription": POSTING, "pubDate": NOW.isoformat()}]}
        elif "himalayas.app" in url:
            body = {"jobs": [{"guid": "https://himalayas.app/j/31", "title": "Analyst, Pricing", "companyName": "Gamma",
                              "locationRestrictions": ["India"], "description": "Excel and SQL", "pubDate": stamp}]}
        elif "workingnomads" in url:
            body = [{"url": "https://www.workingnomads.com/job/41", "title": "Data Analyst", "company_name": "Delta",
                     "description": "Python and Tableau", "tags": "data,analytics", "location": "Europe", "pub_date": NOW.isoformat()}]
        elif "themuse.com" in url:
            body = {"results": [{"id": 51, "name": "Marketing Analyst", "company": {"name": "Epsilon"}, "contents": "<p>Excel</p>",
                                 "locations": [{"name": "Flexible / Remote"}], "refs": {"landing_page": "https://www.themuse.com/j/51"},
                                 "publication_date": NOW.isoformat(), "categories": [], "levels": [{"name": "Mid Level"}]}]}
        elif "arbeitnow" in url:
            body = {"data": [{"slug": "a-61", "title": "Werkstudent Data Analyst (m/w/d)", "company_name": "Zeta", "url": "https://arbeitnow.com/61",
                              "remote": False, "location": "Munich", "description": (
                                  "Wir suchen dich für unser Team. Du arbeitest mit der Analyse von Daten und bist bei uns vor Ort. "
                                  "Wir bieten dir eine spannende Aufgabe, und du bringst Erfahrung mit SQL oder Excel mit, die du im "
                                  "Team einsetzt. Das ist eine Stelle für Studierende, die bei uns lernen und mit uns wachsen."),
                              "tags": [], "job_types": ["internship"], "created_at": stamp}]}
        elif "greenhouse.io" in url:
            body = {"jobs": [{"id": 71, "title": "Data Analyst II", "company_name": "Stripe", "absolute_url": "https://stripe.com/jobs/71",
                              "location": {"name": "Remote in India"}, "content": "&lt;p&gt;SQL and Python&lt;/p&gt;", "departments": [],
                              "first_published": NOW.isoformat()}]}
        else:
            raise AssertionError("unexpected " + url)
        return SimpleNamespace(content=json.dumps(body).encode())


def test_every_board_is_normalised_and_one_failing_board_never_stops_the_others():
    found = collect(Fetcher(refuse={"themuse.com"}), ["remoteok", "jobicy", "himalayas", "workingnomads", "themuse", "arbeitnow",
                                                      "greenhouse:stripe"], ["data analyst", "analyst"])
    titles = {row["key"]: row["title"] for row in found["jobs"]}
    assert "remoteok:12" not in titles  # a chef is not a data analyst
    assert titles["greenhouse@stripe:71"] == "Data Analyst II" and "himalayas:https://himalayas.app/j/31" in titles
    stripe = next(row for row in found["jobs"] if row["key"] == "greenhouse@stripe:71")
    assert stripe["description"] == "SQL and Python" and stripe["source_name"] == "Greenhouse"
    report_by_source = {item["source"]: item for item in found["sources"]}
    assert "robots.txt" in report_by_source["themuse"]["error"] and report_by_source["remoteok"]["kept"] == 1
    assert relevant({"title": "Analyst, Data", "tags": []}, ["data analyst"]) and not relevant({"title": "Data Engineer", "tags": []}, ["analyst"])


@pytest.fixture
def center(tmp_path):
    cv_file = tmp_path / "cv.txt"
    cv_file.write_text(CV, encoding="utf-8")
    opened = []
    built = JobCenter(tmp_path / "jobs", Fetcher(), opener=opened.append)
    built.opened = opened
    built.read_cv(str(cv_file))
    built.set_preferences({"locations": ["Hyderabad, India"]})
    return built


def test_search_keeps_each_job_once_reads_each_board_hourly_and_scores_everything(center):
    first = center.search()
    assert first["terms"] == ["data analyst", "analyst"] and first["new"] >= 5
    titles = [(row["title"], row["company"]) for row in center.store.matches(limit=100, include_blocked=True)]
    assert titles.count(("Data Analyst", "Acme Data")) == 1
    top = first["matches"][0]
    assert (top["title"], top["company"], top["verdict"]) == ("Data Analyst", "Acme Data", "strong")
    calls = len(center.fetcher.calls)
    again = center.search()
    assert len(center.fetcher.calls) == calls and again["new"] == 0 and len(again["skipped_recent"]) == 6  # within the hour
    blocked = {row["title"]: row for row in center.matches(include_blocked=True, limit=100)["matches"]}
    assert blocked["Werkstudent Data Analyst (m/w/d)"]["verdict"] == "not eligible"
    text = report([{"tool": "job_search", "result": {"success": True, "output": first}, "verification": {"passed": True}}])
    assert text.startswith("Searched for data analyst, analyst:") and "#" in text and "Remote OK" in text


def test_a_packet_is_prepared_from_facts_only_and_sending_waits_for_confirmation(center):
    center.search()
    number = center.matches()["matches"][0]["number"]
    detail = center.detail(number)
    assert detail["reading"]["apply_emails"] == ["jobs@acme-data.com"] and "skills" in detail["match"]["parts"]
    packet = center.prepare(number)
    folder = Path(packet["folder"])
    assert sorted(path.name for path in folder.iterdir()) == ["CV.txt", "answers.md", "application.json", "cover_letter.docx",
                                                             "cover_letter.md", "posting.md", "preparation.md", "readiness.json"]
    letter = (folder / "cover_letter.md").read_text(encoding="utf-8")
    assert "Acme Data" in letter and "Aisha Rahman" in letter and "Snowflake" not in letter  # never claims a missing skill
    assert (folder / "CV.txt").read_text(encoding="utf-8") == CV  # the CV is attached unchanged
    answers = (folder / "answers.md").read_text(encoding="utf-8")
    assert "## Asked for but not in your profile (answer honestly)\n- Snowflake" in answers
    assert packet["apply_by"] == "email" and "send my application" in packet["next_step"]
    assert center.store.application(number)["status"] == "prepared"
    with pytest.raises(RuntimeError, match="confirmation window"):
        center.send(number)  # no gate, no mailer: nothing can be sent

    sent = []
    center.mailer = SimpleNamespace(setup_problem=lambda: None,
                                    send=lambda to, subject, body, attachments: sent.append((to, subject, attachments)) or {"sent": True})
    from app.safety.confirmations import KINDS, ConfirmationGate
    assert KINDS["apply"] == "Send a job application"
    center.gate = ConfirmationGate()
    proposal = center.send(number)
    assert proposal["proposed"] and "Nothing has been sent yet" in proposal["how_to_confirm"] and sent == []
    with pytest.raises(PermissionError):
        center.gate.confirm(proposal["action_id"], by_owner=False)
    center.gate.confirm(proposal["action_id"], by_owner=True)
    assert sent[0][0] == "jobs@acme-data.com" and sorted(Path(path).name for path in sent[0][2]) == ["CV.txt", "cover_letter.docx"]
    record = center.store.application(number)
    assert record["status"] == "applied" and record["channel"] == "email" and record["follow_up_at"] > record["applied_at"]
    with pytest.raises(ValueError, match="already marked applied"):
        center.send(number)


def test_web_form_postings_are_opened_for_the_owner_to_submit(center):
    center.set_preferences({"companies": ["greenhouse:stripe"]})
    center.search()
    web = next(row for row in center.matches(limit=50)["matches"] if row["company"] == "Stripe")
    assert center.prepare(web["number"])["apply_by"] == "web page"
    with pytest.raises(ValueError, match="employer's own page"):
        center.send(web["number"])
    opened = center.open(web["number"])
    assert center.opened == ["https://stripe.com/jobs/71"] and "mark job" in opened["next_step"]
    assert center.store.application(web["number"])["status"] == "opened"
    changed = center.set_status(web["number"], "interview", "call on Friday")
    assert changed["status"] == "interview"
    events = center.store.application(web["number"])["events"]
    assert [event["status"] for event in events][-1] == "interview" and events[-1]["note"] == "call on Friday"
    with pytest.raises(ValueError, match="Status is one of"):
        center.set_status(web["number"], "hired")


def test_the_model_may_polish_a_letter_but_never_add_a_skill(center):
    center.search()
    row = center.store.job(center.matches()["matches"][0]["number"])
    data = center.profile.load()
    skills = center.profile.skills(data)
    letter = cover_letter(row, data, skills)
    from tests.test_executive_invention import ScriptedModel
    honest = letter.replace("I am applying", "I am excited to apply")
    assert polished(ScriptedModel(honest), letter, row, skills) == honest
    inflated = letter.replace("I am applying", "As a Snowflake and Kubernetes expert, I am applying")
    assert polished(ScriptedModel(inflated), letter, row, skills) is None


def test_the_automatic_round_tells_prepares_and_drafts_follow_ups_but_sends_nothing(center):
    center.set_preferences({"auto_prepare": True, "notify_score": 60})
    notices = []
    watcher = JobWatcher(center, notices.append)
    result = watcher.run_once()
    assert result["searched"] and result["new_matches"] and result["prepared"]
    assert "new job match" in notices[0] and "Applications prepared (not sent)" in notices[0]
    again = watcher.run_once()
    assert again["new_matches"] == []  # each match is told once
    number = result["prepared"][0]
    center.set_status(number, "applied")
    with sqlite3.connect(center.store.path) as db:
        db.execute("UPDATE applications SET follow_up_at=? WHERE number=?", ((NOW - timedelta(days=1)).isoformat(), number))
    later = center.round()
    assert later["follow_ups"][0]["number"] == number and Path(later["follow_ups"][0]["draft"]).is_file()
    assert "Nothing was sent" in round_text(later)
    assert center.round()["follow_ups"] == []


# ---------------------------------------------------------------------- tools and chat
@pytest.mark.parametrize("text, tool, arguments", [
    ("find python jobs", "job_search", {"query": "python"}),
    ("find me remote data analyst jobs in India", "job_search", {"query": "data analyst", "remote": True, "location": "India"}),
    ("any new jobs for me", "job_search", {}),
    ("show my job matches", "job_matches", {}),
    ("why is job 4 a good match", "job_detail", {"number": 4}),
    ("read my CV from C:\\Users\\me\\cv.pdf", "job_profile", {"action": "read_cv", "path": "C:\\Users\\me\\cv.pdf"}),
    ("add docker to my skills", "job_profile", {"action": "add_skill", "skill": "docker"}),
    ("i want remote data analyst jobs in Hyderabad", "job_profile",
     {"action": "set", "preferences": {"target_titles": ["data analyst"], "remote_only": True, "locations": ["Hyderabad"]}}),
    ("my minimum salary is 80k usd", "job_profile", {"action": "set", "preferences": {"min_salary": 80000.0, "salary_currency": "USD"}}),
    ("stop preparing applications automatically", "job_profile", {"action": "set", "preferences": {"auto_prepare": False}}),
    ("follow stripe on greenhouse", "job_profile", {"action": "follow", "company": "greenhouse:stripe"}),
    ("prepare an application for job 7", "job_application_prepare", {"number": 7}),
    ("send my application for job 7", "job_application_send", {"number": 7}),
    ("open job 7", "job_open", {"number": 7}),
    ("i got an offer from job 7", "job_applications", {"action": "update", "number": 7, "status": "offer"}),
    ("my applications", "job_applications", {"action": "list"}),
    ("which job sources do you use", "job_sources", {}),
    ("what should I learn", "job_learning_plan", {}),
    ("what skills should I learn for these jobs", "job_learning_plan", {}),
    ("learning plan", "job_learning_plan", {}),
    ("what to study to land a data job", "job_learning_plan", {}),
    ("I am willing to relocate", "job_profile", {"action": "set", "preferences": {"willing_to_relocate": True}}),
    ("i can move for work", "job_profile", {"action": "set", "preferences": {"willing_to_relocate": True}}),
    ("i won't relocate", "job_profile", {"action": "set", "preferences": {"willing_to_relocate": False}}),
    ("i'm not willing to relocate", "job_profile", {"action": "set", "preferences": {"willing_to_relocate": False}}),
])
def test_job_phrases_route(text, tool, arguments):
    assert job_request(text) == {"tool": tool, "arguments": arguments}


def test_learning_plan_ranks_skills_by_jobs_unlocked_and_pay_and_reports(center):
    center.search()
    plan = center.learning_plan()
    assert plan["jobs_view"] and plan["learning_plan"] and plan["jobs_considered"] >= 1
    assert isinstance(plan["skills"], list)
    assert set(plan["readiness"]) == {"strong_now", "one_skill_away"}
    for skill in plan["skills"]:
        # Ranking fields are present and sane; a skill the CV already has is never suggested.
        assert skill["needed_by"] >= 1 and skill["required_by"] <= skill["needed_by"]
        assert skill["skill"].lower() not in {s.lower() for s in center.profile.skills(center.profile.load())}
    # Ranking is non-increasing by (unlocks, required_by).
    keys = [(s["unlocks"], s["required_by"]) for s in plan["skills"]]
    assert keys == sorted(keys, reverse=True)
    text = report([{"tool": "job_learning_plan", "result": {"success": True, "output": plan},
                    "verification": {"passed": True}}])
    assert "What to learn" in text and "not a promise" in text.lower()


def _reading(mode="on-site", regions=("Germany",)):
    return {"skills": {"required": ["Python"], "nice": [], "mentioned": []}, "regions": list(regions), "mode": mode,
            "languages_needed": [], "written_in": "English", "level": "", "salary": None, "warnings": [], "years_min": None}


def _profile(**prefs):
    base = dict(target_titles=[], search_terms=[], locations=["India"], countries_ok=["India"], remote_only=False,
                willing_to_relocate=False, min_salary=None, salary_currency="USD", level="", job_types=[],
                exclude_companies=[], exclude_words=[])
    base.update(prefs)
    return {"preferences": base, "cv": {"languages": ["English"], "level": "", "years": None}}


def test_relocation_keeps_an_away_job_eligible_with_a_note_and_a_penalty():
    from datetime import UTC, datetime

    from app.jobs.match import score
    job = {"title": "Data Analyst", "company": "X", "posted_at": datetime.now(UTC).isoformat()}
    skills = ["Python"]
    stay = score(job, _reading(), _profile(), skills)
    move = score(job, _reading(), _profile(willing_to_relocate=True), skills)
    home = score(job, _reading(regions=["India"]), _profile(willing_to_relocate=True), skills)
    remote = score(job, _reading(mode="remote", regions=["India"]), _profile(willing_to_relocate=True), skills)
    # Not willing: a place it does not cover is a hard block. Willing: eligible, with a relocation note.
    assert stay["verdict"] == "not eligible" and stay["relocation"] is None
    assert move["verdict"] != "not eligible" and "relocating to Germany" in move["relocation"]
    # Moving costs 10 points against an otherwise-equal in-place role, so local/remote rank above it.
    assert move["score"] == max(0, home["score"] - 10)
    # A role already in the owner's place, or a remote one there, needs no move.
    assert home["relocation"] is None and remote["relocation"] is None


class DiscoverFetcher:
    """Serves one company's Greenhouse board; every other slug or ATS answers like a 404."""

    def __init__(self, hit="stripe"):
        self.hit, self.calls = hit, []

    def fetch(self, url, max_bytes, accept=None, request_headers=None):
        self.calls.append(url)
        if "boards-api.greenhouse.io" in url and f"/boards/{self.hit}/" in url:
            body = {"jobs": [{"id": 71, "title": "Data Analyst", "company_name": "Stripe", "absolute_url": "https://x/71",
                              "location": {"name": "Remote"}, "content": "SQL", "departments": [], "first_published": NOW.isoformat()},
                             {"id": 72, "title": "ML Engineer", "company_name": "Stripe", "absolute_url": "https://x/72",
                              "location": {"name": "Remote"}, "content": "PyTorch", "departments": [], "first_published": NOW.isoformat()}]}
            return SimpleNamespace(content=json.dumps(body).encode())
        raise FileNotFoundError(f"404 {url}")  # not on this ATS / unknown slug


def test_discover_finds_a_public_board_only_and_can_follow(center):
    from app.jobs.sources import discover
    center.fetcher = DiscoverFetcher()
    boards = discover(center.fetcher, "Stripe Inc.")
    assert boards and boards[0]["ats"] == "greenhouse" and boards[0]["slug"] == "stripe" and boards[0]["jobs"] == 2
    assert "Data Analyst" in boards[0]["samples"]
    # Only public keyless ATS hosts are ever contacted.
    assert all(any(host in url for host in ("greenhouse.io", "lever.co", "ashbyhq.com")) for url in center.fetcher.calls)
    # A company with no public board returns nothing, not an error.
    assert discover(DiscoverFetcher(hit="nobody"), "Ghost LLC") == []
    # follow=true adds the board to the owner's searches.
    result = center.discover_company("Stripe", follow=True)
    assert result["followed"] == "greenhouse:stripe"
    assert "greenhouse:stripe" in center.profile.load()["preferences"]["companies"]


@pytest.mark.parametrize("text, company", [
    ("find hidden jobs at Ramp", "Ramp"), ("does Stripe have any jobs", "Stripe"),
    ("check Acme's careers page", "Acme"), ("discover Figma careers", "Figma"), ("is Google hiring", "Google")])
def test_discover_routing(text, company):
    assert job_request(text) == {"tool": "job_discover_company", "arguments": {"company": company}}


@pytest.mark.parametrize("text", ["what are my chances", "how likely am I to hear back", "my response rate",
                                  "my job prospects", "what is my reply rate"])
def test_prospects_routing(text):
    assert job_request(text) == {"tool": "job_prospects", "arguments": {}}


def test_prospects_reads_only_the_owners_record_and_splits_by_strength():
    from app.jobs.insight import prospects, prospects_lines

    class Store:
        def __init__(self, apps):
            self._apps = apps

        def applications(self, status=""):
            return self._apps

    def app(status, score):
        return {"status": status, "score": score, "title": "Data Analyst", "company": "X"}
    sent = [app("offer", 80), app("interview", 72), app("rejected", 65), app("applied", 55),
            app("rejected", 40), app("interview", 85), app("applied", 70)]
    value = prospects(Store(sent + [app("saved", 50), app("prepared", 50)]))  # saved/prepared are not "sent"
    assert value["sent"] == 7 and value["replies"] == 3 and value["offers"] == 1 and value["waiting"] == 2
    assert value["by_strength"]["strong_fit"]["reply_rate"] == 0.6 and value["enough"]
    assert value["projection"]["applications_per_reply"] == 2
    text = "\n".join(prospects_lines(value))
    # Honest framing: own record only, and an explicit disclaimer that it never predicts an employer's decision.
    assert "own record" in text and "never predicts a particular employer" in text
    # A thin record never reads a rate.
    thin = prospects(Store([app("applied", 60), app("rejected", 50)]))
    assert thin["enough"] is False and "too few" in thin["note"]
    assert prospects(Store([]))["sent"] == 0


def test_learning_plan_without_matches_asks_to_search_first(center):
    plan = center.learning_plan()
    assert plan["jobs_considered"] == 0
    text = report([{"tool": "job_learning_plan", "result": {"success": True, "output": plan},
                    "verification": {"passed": True}}])
    assert "Run a job search first" in text


def test_other_jobs_are_not_job_searches():
    for text in ("show my research jobs", "show running jobs", "list background jobs", "find the file report.pdf", "good job"):
        assert job_request(text) is None, text
    from app.intent.router import IntentRouter
    assert IntentRouter().route("find python jobs").entities.get("modality") == "jobs"


def test_the_tools_are_registered_with_sending_behind_the_outward_tier(center):
    found = {}

    class Registry:
        def register(self, name, description, schema, risk, category, handler):
            found[name] = (risk, category)
    register_job_tools(Registry(), center)
    from app.jobs.tools import TOOL_NAMES
    assert set(found) == set(TOOL_NAMES)
    assert found["job_application_send"] == ("high", "send_message") and found["job_search"] == ("low", "public_api_get")
    from app.safety.authority import CATEGORY_TIERS, RiskTier
    assert CATEGORY_TIERS[found["job_application_send"][1]] is RiskTier.HIGH_CONSEQUENCE


# ---------------------------------------------------------------------- the window
def test_the_jobs_window(shared_tk, center):
    from app.gui_jobs import JobsWindow
    from app.gui_theme import Palette
    center.search()

    class GUI(Palette):
        pass
    gui = GUI()
    gui.root, gui.runtime = shared_tk, SimpleNamespace(jobs=center, job_watcher=JobWatcher(center, lambda text: None))
    window = JobsWindow(gui)

    def wait(ready, seconds=10):
        deadline = time.time() + seconds
        while time.time() < deadline and not ready():
            shared_tk.update()
            time.sleep(.02)
        return ready()
    try:
        assert [window.tabs.tab(tab, "text") for tab in window.tabs.tabs()] == ["Matches", "Applications", "Profile", "CV review", "Learn", "Sources"]
        assert wait(lambda: window.match_tree.get_children())
        first = window.match_tree.get_children()[0]
        assert window.match_tree.item(first, "values")[2] == "Data Analyst"
        window.match_tree.selection_set(first)
        window.match_selected()
        assert wait(lambda: "Score parts:" in window.detail.get("1.0", "end"))
        assert wait(lambda: "How it matches your CV" in window.deep.get("1.0", "end") and "Prepare:" in window.deep.get("1.0", "end"))
        window.act("prepare")
        assert wait(lambda: "is ready in" in window.detail.get("1.0", "end"))
        window.tabs.select(1)
        assert wait(lambda: window.app_tree.get_children())
        assert window.app_tree.item(first, "values")[2] == "prepared"
        window.tabs.select(2)
        assert wait(lambda: "Aisha Rahman" in window.profile_text.get("1.0", "end"))
        assert window.fields["target_titles"].get() == "Data Analyst"
        window.tabs.select(3)
        assert wait(lambda: "CV review (" in window.review_text.get("1.0", "end"))
        window.tabs.select(4)
        assert wait(lambda: "What to learn" in window.learn_text.get("1.0", "end")
                    or "Run a job search first" in window.learn_text.get("1.0", "end"))
        window.tabs.select(5)
        assert wait(lambda: window.source_tree.get_children())
        assert "remoteok" in window.source_tree.get_children()
    finally:
        window.close()


# ---------------------------------------------------------------------- the deep look, the CV review, pre-filling, learning
def test_duties_are_matched_to_the_cvs_own_lines():
    from app.jobs.insight import alignment, responsibilities
    duties = responsibilities(plain(POSTING))
    assert duties == ["Build Power BI dashboards for the sales team", "Automate weekly reporting with Python and Airflow",
                      "Present findings to senior stakeholders every month", "Own the Snowflake data model and its documentation"]
    cv = understand_cv(CV, today=date(2026, 10, 1))
    fit = alignment(duties, cv["lines"], [item["name"] for item in cv["skills"]])
    status = {row["duty"]: row for row in fit["rows"]}
    assert status["Build Power BI dashboards for the sales team"]["status"] == "aligned"
    assert "Power BI dashboards" in status["Build Power BI dashboards for the sales team"]["evidence"]
    assert status["Automate weekly reporting with Python and Airflow"]["status"] == "aligned"
    assert status["Present findings to senior stakeholders every month"]["status"] == "gap"
    assert 0 < fit["percent"] < 100 and fit["aligned"] + fit["partly"] + fit["gaps"] == 4
    assert not alignment(duties, [], [])["available"] and "read your CV again" in alignment(duties, [], [])["reason"]
    assert responsibilities("What you will do\nData Quality & Validation\nProcess Improvement & Documentation\nBuild dashboards") == ["Build dashboards"]


def test_the_deep_look_says_what_it_knows_and_what_nobody_knows(center):
    center.search()
    number = center.matches()["matches"][0]["number"]
    value = center.insight(number)
    assert value["fit"]["available"] and value["fit"]["rows"][0]["status"] == "aligned"
    assert any("PINEAPPLE" in item for item in value["checklist"]) and any("portfolio" in item for item in value["checklist"])
    assert any("subject line" in item for item in value["checklist"])
    assert value["success"] and "first 90 days" in value["success"][0] and value["people"]
    assert [item["when"] for item in value["first_90_days"]] == ["Days 1–30", "Days 31–60", "Days 61–90"]
    assert any("Snowflake" in item and "would build" in item for item in value["benefit"])
    assert value["history"]["note"] == "You have not applied to a similar role yet." and "Nobody publishes who was hired" in value["history"]["basis"]
    assert value["market"]["similar"] >= 1 and "not the whole market" in value["market"]["basis"]
    assert value["market"]["next_roles"][0] == "Senior Data Analyst"
    prepare = value["prepare"]
    assert any(item["kind"] == "behavioural" and "Use:" in item["question"] for item in prepare["questions"])
    assert prepare["study"][0]["skill"] == "Snowflake" and prepare["study"][0]["start"].startswith("https://docs.snowflake.com")
    assert any(text.startswith("Put these first") for text in prepare["tailoring"]) and "What is the salary range?" not in prepare["ask_them"]
    assert "Nobody knows who was hired before" in value["basis"]
    text = report([{"tool": "job_insight", "result": {"success": True, "output": value}, "verification": {"passed": True}}])
    assert "How it matches your CV:" in text and "Instructions applicants miss:" in text and "Your first 90 days" in text
    from tests.test_executive_invention import ScriptedModel
    center.model = ScriptedModel("A strong fit for the dashboards and reporting; learn Snowflake before applying.")
    assert center.insight(number, explain=True)["narrative"].startswith("A strong fit")
    assert "Never invent facts" in center.model.calls[0][0].content


def test_the_cv_review_checks_and_reads_against_the_matches(center):
    center.search()
    value = center.cv_review()
    checks = {item["check"]: item for item in value["checks"]}
    assert checks["Email"]["passed"] and checks["Skills section"]["passed"] and checks["Experience dates"]["passed"]
    assert not checks["Length"]["passed"] and "250 words" in checks["Length"]["advice"]
    assert value["keyword_gaps"][0]["skill"] == "Snowflake" and value["best_fits"][0]["title"] == "Data Analyst"
    from app.jobs.cv_review import review
    weak = center.profile.load()
    weak["cv"]["lines"] = ["Responsible for weekly reports", "Helped the sales team with dashboards"]
    result = {item["check"]: item for item in review(weak, [], [])["checks"]}
    assert "start with “Led / Owned”" in result["Strong openings"]["advice"] and not result["Results with numbers"]["passed"]
    with pytest.raises(ValueError, match="Read your CV first"):
        review({"cv": {}, "preferences": {}}, [], [])


def test_a_form_is_prefilled_with_facts_only_and_nothing_is_sent(center):
    from app.jobs.apply_form import fill_plan
    fields = [{"mark": "a", "label": "First name", "type": "text", "tag": "input"},
              {"mark": "b", "label": "Last name", "type": "text", "tag": "input"},
              {"mark": "c", "label": "Email", "type": "email", "tag": "input"},
              {"mark": "d", "label": "LinkedIn profile", "type": "url", "tag": "input"},
              {"mark": "e", "label": "Cover letter", "type": "textarea", "tag": "textarea"},
              {"mark": "f", "label": "Resume/CV", "type": "file", "tag": "input"},
              {"mark": "g", "label": "Are you legally authorized to work in the US?", "type": "select", "tag": "select", "required": True},
              {"mark": "h", "label": "Gender", "type": "select", "tag": "select"},
              {"mark": "i", "label": "I agree to the privacy policy", "type": "checkbox", "tag": "input", "required": True},
              {"mark": "j", "label": "Password", "type": "password", "tag": "input", "secret": True}]
    plan = fill_plan(fields, center.profile.load(), "Dear Hiring Team")
    filled = {item["label"]: item["value"] for item in plan["fill"]}
    assert filled == {"First name": "Aisha", "Last name": "Rahman", "Email": "aisha.rahman@example.org",
                      "LinkedIn profile": "linkedin.com/in/aisha-rahman-data", "Cover letter": "Dear Hiring Team"}
    assert plan["files"] == ["Resume/CV"]
    assert plan["left"] == ["Are you legally authorized to work in the US?", "Gender", "I agree to the privacy policy"]

    class Browser:
        def __init__(self):
            self.filled = None

        def form_fields(self, url, visible=False):
            self.url, self.visible = url, visible
            return fields

        def fill_fields(self, plan):
            self.filled = plan
            return [{"label": item["label"], "value": item["value"]} for item in plan]
    center.browser = Browser()
    center.set_preferences({"companies": ["greenhouse:stripe"]})
    center.search()
    web = next(row for row in center.matches(limit=50)["matches"] if row["company"] == "Stripe")
    result = center.fill(web["number"])
    assert center.browser.visible and center.browser.url == "https://stripe.com/jobs/71"
    assert [item["label"] for item in center.browser.filled] == list(filled)
    assert result["files"] == ["Resume/CV"] and result["cv"].endswith("CV.txt") and "press their submit button yourself" in result["next_step"]
    assert center.store.application(web["number"])["status"] == "opened"
    text = report([{"tool": "job_application_fill", "result": {"success": True, "output": result}, "verification": {"passed": True}}])
    assert "Attach yourself: Resume/CV" in text and "Left for you:" in text
    center.browser = None
    with pytest.raises(RuntimeError, match="browser"):
        center.fill(web["number"])


def test_scores_learn_a_little_from_what_the_owner_pursues_and_hides(center):
    from app.jobs.match import LEARNED_LIMIT, learned_from
    assert learned_from(["Data Analyst"], []) == {}  # too few choices to learn from
    learned = learned_from(["Data Analyst", "Data Analyst II"], ["Marketing Analyst", "Marketing Analyst", "Sales Analyst"])
    assert learned["data"] > 0 and learned["marketing"] < 0
    center.search()
    rows = {row["title"]: row for row in center.matches(limit=50, include_blocked=True)["matches"]}
    before = rows["Senior Data Analyst"]["score"]
    center.hide(rows["Marketing Analyst"]["number"])
    center.prepare(rows["Data Analyst"]["number"])
    center.prepare(rows["Werkstudent Data Analyst (m/w/d)"]["number"])
    center.rescore()
    after = center.store.job(rows["Senior Data Analyst"]["number"])
    assert before < after["score"] <= before + LEARNED_LIMIT
    assert after["match"]["choices"]["reason"].startswith("like jobs you pursued (") and "data" in after["match"]["choices"]["reason"]
    hidden_numbers = [row["number"] for row in center.matches(limit=50, include_blocked=True)["matches"]]
    assert rows["Marketing Analyst"]["number"] not in hidden_numbers


@pytest.mark.parametrize("text, tool, arguments", [
    ("tell me everything about job 12", "job_insight", {"number": 12, "explain": True}),
    ("is job 12 good for my career", "job_insight", {"number": 12, "explain": True}),
    ("how do i prepare for job 12", "job_insight", {"number": 12}),
    ("does job 12 fit my cv", "job_insight", {"number": 12}),
    ("review my cv", "job_cv_review", {}),
    ("what should i change in my resume", "job_cv_review", {}),
    ("fill the application form for job 12", "job_application_fill", {"number": 12}),
])
def test_deep_look_phrases_route(text, tool, arguments):
    assert job_request(text) == {"tool": tool, "arguments": arguments}


def test_saying_what_you_want_shows_the_saved_jobs_that_fit(center):
    # Live 2026-10-24: "I want remote data analyst jobs in Hyderabad" saved the wish and showed no job.
    from app.jobs.tools import report_lines

    center.search()
    result = center.set_preferences({"target_titles": ["data analyst"], "remote_only": True})
    titles = {row["title"] for row in result["wanted_matches"]}
    # "Hyderabad, India" keeps the India role and the unrestricted remote ones.
    assert {"Data Analyst", "Senior Data Analyst", "Analyst, Pricing"} <= titles
    text = "\n".join(report_lines(result))
    assert "Saved jobs that fit this now" in text and "find jobs" in text


def test_a_job_matched_by_its_text_says_so_beside_the_listed_place():
    from app.jobs.tools import _row_line

    row = {"number": 77, "title": "Python Developer", "company": "AssetIntel", "score": 42, "verdict": "weak",
           "mode": "remote", "location": "United States", "source_name": "Himalayas"}
    assert "United States (listed; filter matched stated eligibility)" in _row_line(row, "India")
    assert "(listed)" not in _row_line(row, "USA")  # an alias of the listed country is not a text match
    assert "(listed)" not in _row_line(row, "")


def test_without_a_cv_the_profile_does_not_claim_one_was_read(center):
    assert "No CV read yet" in center.profile_summary({**center.profile.load(), "cv": {}})["basis"]
