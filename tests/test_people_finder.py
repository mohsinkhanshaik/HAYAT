"""Find people to contact: goals, the four public sources, merging, keeping runs, chat and the window."""
from __future__ import annotations

import json
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from app.research import people_finder as finder_module
from app.research.people_finder import (PeopleFinder, choose_sources, format_report, parse_goal, plan, report, request,
                                        team_members)


@pytest.mark.parametrize("goal, role, field, place, organizations", [
    ("CTOs of fintech companies in UAE", "CTO", "fintech", "UAE", []),
    ("founders of AI startups in Hyderabad", "founder", "AI", "Hyderabad", []),
    ("top machine learning researchers in India", "researcher", "machine learning", "India", []),
    ("python developers in Bangalore", "developer", "python", "Bangalore", []),
    ("CEOs of banks in the United Kingdom", "CEO", "banks", "United Kingdom", []),
    ("investors in climate tech in Europe", "investor", "climate tech", "Europe", []),
    ("heads of marketing at Stripe", "head of marketing", "", "", ["Stripe"]),
    ("people to contact: top tier founders in Dubai for business", "founder", "", "Dubai", []),
])
def test_goals_are_read_into_role_field_place_and_organisations(goal, role, field, place, organizations):
    parsed = parse_goal(goal)
    assert (parsed["role"], parsed["field"], parsed["place"], parsed["organizations"]) == (role, field, place, organizations)


def test_each_role_is_looked_for_where_it_is_recorded():
    assert choose_sources("researcher", "machine learning") == ["openalex"]
    assert choose_sources("developer", "python") == ["github"]
    assert choose_sources("founder", "fintech") == ["wikidata"]
    assert choose_sources("CTO", "fintech") == ["wikidata", "teams"]  # CTOs are rarely on Wikidata; team pages list them
    assert parse_goal("recruiters hiring data analysts in Michigan")["purpose"] == "job"


class Net:
    """Fake Wikidata, OpenAlex, GitHub and web pages; records every request and the headers sent."""

    def __init__(self):
        self.calls, self.slow_once = [], False
        self.pages = {}

    def get_json(self, url, headers=None, timeout=None):
        self.calls.append((url, headers, timeout))
        parts = urlsplit(url)
        query = parse_qs(parts.query)
        if parts.hostname == "www.wikidata.org":
            assert headers and "github.com" in headers["User-Agent"]  # Wikimedia refuses an agent with no contact address
            search = query["search"][0].casefold()
            return {"search": {
                "germany": [{"id": "Q183", "label": "Germany", "description": "country in Central Europe"}],
                "bank": [{"id": "Q22687", "label": "bank", "description": "financial institution"}],
                "banks": [{"id": "Q99", "label": "Banks", "description": "family name"}],
                "infosys": [{"id": "Q26129", "label": "Infosys", "description": "Indian IT company"}],
            }.get(search, [])}
        if parts.hostname == "query.wikidata.org":
            assert headers and "github.com" in headers["User-Agent"] and timeout == 55
            sparql = unquote(query["query"][0])
            if "P297" in sparql:
                return {"results": {"bindings": [{"country": {"value": "http://www.wikidata.org/entity/Q183"},
                                                  "iso": {"value": "DE"}}]}}
            if "P856 ?site. }" in sparql:
                return {"results": {"bindings": [{"site": {"value": "https://www.infosys.com/"}}]}}
            if self.slow_once and "P279*" in sparql:
                self.slow_once = False
                raise TimeoutError("The read operation timed out")
            assert "FILTER NOT EXISTS { ?person wdt:P570 ?died }" in sparql and "wdt:P17 wd:Q183" in sparql
            assert "FILTER NOT EXISTS { ?held pq:P582 ?ended }" in sparql  # a post someone left is not theirs
            assert "wd:Q22687" in sparql and "UNION { ?org wdt:P31" in sparql  # a bank is a kind of organisation
            row = lambda q, name, org, links, site="", li="", role="chief executive officer", since="": {
                "person": {"value": f"http://www.wikidata.org/entity/{q}"}, "personLabel": {"value": name},
                "org": {"value": f"http://www.wikidata.org/entity/O{org[:3]}"},
                "orgLabel": {"value": org}, "links": {"value": str(links)}, "role": {"value": role},
                **({"site": {"value": site}} if site else {}), **({"li": {"value": li}} if li else {}),
                **({"since": {"value": since}} if since else {})}
            return {"results": {"bindings": [
                row("Q1", "Christian Sewing", "Deutsche Bank", 60, "https://www.db.com", "christian-sewing"),
                row("Q2", "Bettina Orlopp", "Commerzbank", 40, "https://www.commerzbank.com", since="2024-10-01T00:00:00Z"),
                # Her predecessor has no end date recorded (Wikidata lags): the later start wins.
                row("Q4", "Manfred Knof", "Commerzbank", 40, since="2021-01-01T00:00:00Z"),
                row("Q3", "Q3", "Unlabelled Bank", 90),  # no English label: nothing readable to show
                row("Q1", "Christian Sewing", "Deutsche Bank", 60, role="chairperson"),
            ]}}
        if url.startswith("https://api.openalex.org/topics"):
            return {"results": [{"id": "https://openalex.org/T12072", "display_name": "Machine Learning and Algorithms"}]}
        if url.startswith("https://api.openalex.org/authors"):
            assert "topics.id:T12072" in url and "last_known_institutions.country_code:IN" in url
            return {"results": [{"id": "https://openalex.org/A1", "display_name": "Chiranjib Bhattacharyya", "cited_by_count": 5764,
                                 "works_count": 200, "orcid": "https://orcid.org/0000-0001-0000-0001",
                                 "last_known_institutions": [{"display_name": "Elsewhere Lab", "country_code": "SG"},
                                                             {"display_name": "Indian Institute of Science", "country_code": "IN"}]}]}
        if url.startswith("https://api.github.com/search/users"):
            assert "language:python" in unquote(url) and 'location:"Hyderabad"' in unquote(url.replace("+", " "))
            return {"items": [{"login": "dev-one"}, {"login": "an-org"}, {"login": "dev-two"}]}
        if url.startswith("https://api.github.com/users/"):
            login = url.rsplit("/", 1)[-1]
            return {"dev-one": {"type": "User", "name": "Dev One", "company": "@redhat", "followers": 9000, "public_repos": 40,
                                "blog": "dev.example", "email": "dev.one@example.com", "bio": "Principal engineer"},
                    "an-org": {"type": "Organization", "name": "An Org"},
                    "dev-two": {"type": "User", "name": "Dev Two", "followers": 300, "blog": "https://instagram.com/devtwo"}}[login]
        if parts.hostname == "openalex.org" or parts.hostname is None:
            raise AssertionError(url)
        raise AssertionError("unexpected " + url)

    def fetch_html(self, url):
        self.calls.append((url, None, None))
        if url not in self.pages:
            raise RuntimeError("404 Not Found")
        return url, self.pages[url]


def make(net, tmp_path=None, **extra):
    return PeopleFinder(net.get_json, net.fetch_html, lambda query, limit: [], folder=tmp_path, **extra)


def test_wikidata_gives_the_leaders_of_organisations_in_a_field_and_place(tmp_path):
    net = Net()
    result = make(net, tmp_path).find("CEOs of banks in Germany")
    names = [person["name"] for person in result["people"]]
    assert names == ["Christian Sewing", "Bettina Orlopp"]  # ranked by how widely the organisation is covered
    assert result["people"][1]["role"] == "Chief executive (since 2024)"
    sewing = result["people"][0]
    assert sewing["role"] == "Chief executive · Chair" and sewing["organization"] == "Deutsche Bank"
    assert sewing["source_url"] == "https://www.wikidata.org/wiki/Q1" and "60 Wikipedia editions" in sewing["standing"]
    routes = {route["label"]: route["value"] for route in sewing["routes"]}
    assert routes["Deutsche Bank website"] == "https://www.db.com"
    assert routes["LinkedIn profile (published on Wikidata)"] == "https://www.linkedin.com/in/christian-sewing"
    assert any(route["kind"] == "search" and "linkedin.com/search" in route["value"] for route in sewing["routes"])
    assert not any("@" in route["value"] for person in result["people"] for route in person["routes"])  # no e-mail guessed
    kept = json.loads(open(result["saved_to"], encoding="utf-8").read())
    assert kept["people"][0]["name"] == "Christian Sewing"
    assert make(net, tmp_path).recent()[0]["goal"] == "CEOs of banks in Germany"


def test_a_slow_wikidata_query_retries_without_every_sub_industry():
    net = Net()
    net.slow_once = True
    result = make(net).find("CEOs of banks in Germany")
    assert [person["name"] for person in result["people"]] == ["Christian Sewing", "Bettina Orlopp"] and not result["errors"]


def test_openalex_gives_cited_researchers_with_the_institution_in_that_country():
    original = Net().get_json  # India's place lookup is answered here; the rest by the shared fake

    def get_json(url, headers=None, timeout=None):
        if "wbsearchentities" in url and "India" in unquote(url):
            return {"search": [{"id": "Q668", "label": "India", "description": "country in South Asia"}]}
        if "P297" in unquote(url):
            return {"results": {"bindings": [{"country": {"value": "http://www.wikidata.org/entity/Q668"}, "iso": {"value": "IN"}}]}}
        return original(url, headers, timeout)
    finder = PeopleFinder(get_json, None, None)
    result = finder.find("top machine learning researchers in India")
    person = result["people"][0]
    assert person["name"] == "Chiranjib Bhattacharyya" and person["organization"] == "Indian Institute of Science"
    assert "5,764 citations" in person["standing"] and person["source"] == "OpenAlex"
    assert {"kind": "profile", "label": "ORCID record", "value": "https://orcid.org/0000-0001-0000-0001"} in person["routes"]


def test_github_gives_developers_without_their_email_or_social_pages():
    net = Net()
    result = PeopleFinder(net.get_json, None, None).find("python developers in Hyderabad")
    assert [person["name"] for person in result["people"]] == ["Dev One", "Dev Two"]  # the organisation account is left out
    one = result["people"][0]
    assert one["role"] == "Python developer" and one["organization"] == "redhat" and "Principal engineer" in one["why"]
    values = [route["value"] for person in result["people"] for route in person["routes"]]
    assert "https://dev.example" in values and not any("instagram" in value or "@" in value for value in values)


def test_team_pages_pair_names_with_titles_and_skip_buttons_and_slogans():
    page = """<html><body><nav><a>About</a></nav>
      <div class="card"><h3>Nandan M. Nilekani</h3><p>Co-founder and Chairman of the Board</p><a>Read Bio</a></div>
      <div class="card"><h3>Salil Parekh</h3><p>Chief Executive Officer &amp; Managing Director</p></div>
      <div><h4>Zoho Vault</h4><p>Rolling out our latest addition: a password manager for businesses.</p></div>
      <div><h3>Read Bio</h3><p>Director</p></div></body></html>"""
    assert team_members(page) == [("Nandan M. Nilekani", "Co-founder and Chairman of the Board"),
                                  ("Salil Parekh", "Chief Executive Officer & Managing Director")]


def test_named_organisations_are_read_from_their_own_site_found_on_wikidata():
    net = Net()
    net.pages = {"https://www.infosys.com/": '<a href="/industries/waste-management.html">Waste management</a>'
                                            '<a href="/about/management-profiles.html">Management profiles</a>',
                 "https://www.infosys.com/about/management-profiles.html":
                     "<div><h3>Salil Parekh</h3><p>Chief Executive Officer &amp; Managing Director</p></div>"
                     "<div><h3>Jayesh Sanghrajka</h3><p>Chief Financial Officer</p></div>"}
    result = make(net).find("executives at Infosys")
    assert [person["name"] for person in result["people"]] == ["Salil Parekh", "Jayesh Sanghrajka"]
    person = result["people"][0]
    assert person["source_url"] == "https://www.infosys.com/about/management-profiles.html"
    assert {"kind": "website", "label": "Infosys website", "value": "https://www.infosys.com/"} in person["routes"]
    read = [url for url, _headers, _timeout in net.calls]
    assert not any("waste-management" in url for url in read)  # "management" alone is not a team page
    assert result["organisation_routes"][0]["organization"] == "Infosys"
    # The role narrows the titles: a CFO is not "head of marketing".
    assert [person["name"] for person in make(net).find("heads of marketing at Infosys")["people"]] == []


def test_the_same_person_from_two_sources_is_one_row_crediting_both():
    one = finder_module._person("Ada Example", "Founder", "Example Labs", "Wikidata", "https://www.wikidata.org/wiki/Q9",
                                why="w", standing="s", score=12)
    two = finder_module._person("Ada Example", "CEO", "Example Labs", "example.com", "https://example.com/team",
                                why="w", standing="s", score=9)
    two["routes"].append({"kind": "website", "label": "Example Labs website", "value": "https://example.com/"})
    merged = finder_module._merge({"wikidata": [one], "teams": [two]}, ["wikidata", "teams"], 10)
    assert len(merged) == 1 and merged[0]["also_in"] == [{"source": "example.com", "url": "https://example.com/team"}]
    assert merged[0]["routes"][0]["value"] == "https://example.com/"


def test_a_goal_without_who_is_asked_for_and_sources_failing_are_named():
    with pytest.raises(ValueError, match="Say who you want to reach"):
        PeopleFinder(lambda *a, **k: {}, None, None).find("")

    def refused(url, headers=None, timeout=None):
        raise RuntimeError("Client error '403 Forbidden'")
    result = PeopleFinder(refused, None, None).find("python developers in Hyderabad")
    assert result["people"] == [] and result["errors"] == [
        {"source": "GitHub", "error": "the source refused or rate-limited this request; try again in a few minutes"}]
    assert "Nobody matched" in format_report(result)


def test_chat_finds_people_and_reports_them_with_sources():
    assert request("find people to contact: CTOs of fintech companies in UAE") == {"goal": "CTOs of fintech companies in UAE"}
    assert request("find top machine learning researchers in India") == {"goal": "top machine learning researchers in India"}
    assert request("find contacts for Ada Example at Example Institute") is None  # a known name: contact discovery
    assert request("find python jobs in Michigan") is None and request("how are you today?") is None
    assert plan("show me founders of fintech startups in Dubai", {"people_find"})["tool"] == "people_find"
    result = make(Net()).find("CEOs of banks in Germany")
    text = report([{"tool": "people_find", "result": {"success": True, "output": result}}])
    assert text.startswith('People to contact for "CEOs of banks in Germany": 2 found (Wikidata 2).')
    assert "1. Christian Sewing · Chief executive · Chair · Deutsche Bank" in text and "Source: https://www.wikidata.org/wiki/Q1" in text
    from app.intent.router import IntentRouter
    assert IntentRouter().route("find people to contact: founders of fintech startups in India").entities == {"modality": "private_networking"}


def test_the_window_finds_saves_and_hands_over_to_contact_discovery(shared_tk, tmp_path, monkeypatch):
    import app.gui_people_finder as window_module
    from app.gui_theme import Palette

    opened = []
    monkeypatch.setattr(window_module.webbrowser, "open", opened.append)
    net = Net()
    saved, handed = [], []

    class Book:
        def save(self, name, **fields):
            saved.append((name, fields))
            return {"id": "c1"}

    class GUI(Palette):
        pass
    gui = GUI()
    gui.root = shared_tk
    gui.runtime = SimpleNamespace(people_finder=lambda check=None: make(net, tmp_path, check=check))
    import tkinter as tk
    networking = tk_frame = tk.Toplevel(shared_tk)  # stands in for the Private networking window
    tk_frame.withdraw()
    networking.gui, networking.palette, networking.book = gui, GUI, Book()
    networking.find_professional_contacts = lambda **fields: handed.append(fields) or True
    window = window_module.PeopleFinderWindow(networking)

    def wait(ready, seconds=10):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            shared_tk.update()
            if ready():
                return True
            time.sleep(.02)
        return ready()
    try:
        assert not window.find()  # nothing asked yet
        window.goal.set("CEOs of banks in Germany")
        assert window.find()
        assert wait(lambda: not window.busy and window.table.get_children())
        assert [window.table.item(key, "text") for key in window.table.get_children()] == ["Christian Sewing", "Bettina Orlopp"]
        assert "Found 2 people" in window.status.get()
        window.table.selection_set(window.table.get_children())
        assert window.save_selected()
        name, fields = saved[0]
        assert name == "Christian Sewing" and fields["organization"] == "Deutsche Bank"
        assert {"kind": "website", "value": "https://www.db.com", "status": "public_professional",
                "source_url": "https://www.wikidata.org/wiki/Q1"} in fields["channels"]
        assert not any(channel["kind"] == "search" for channel in fields["channels"]) and len(saved) == 2
        window.table.selection_set(window.table.get_children()[0])
        assert window.find_routes()
        assert handed == [{"name": "Christian Sewing", "organization": "Deutsche Bank", "official_url": "https://www.db.com"}]
        window.open_source()
        assert opened[-1] == "https://www.wikidata.org/wiki/Q1"
        # Every search is kept and can be reopened.
        assert window.earlier.cget("values")
        window.show_report({"people": [], "coverage": {}, "goal": "x"})
        window.earlier.current(0)
        window.open_earlier()
        assert len(window.table.get_children()) == 2
    finally:
        window.close()
        tk_frame.destroy()
