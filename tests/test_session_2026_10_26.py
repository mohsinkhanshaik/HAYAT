"""Email with a regular login, the verified onion directory, and deep people search (2026-10-26)."""
from __future__ import annotations

import imaplib
import json
from types import SimpleNamespace

import pytest

from app.accounts import AccountManager
from app.research import onion_directory as od
from app.research.people_finder import PeopleFinder, _routes_from_discovery
from tests.test_accounts import Broker


# ---------------------------------------------------------------- email login
class FakeImap:
    def __init__(self, host, password="right-password"):
        self.host, self.password, self.calls = host, password, []

    def login(self, user, password):
        self.calls.append(("login", user))
        if password != self.password:
            raise imaplib.IMAP4.error("AUTHENTICATIONFAILED")

    def logout(self):
        self.calls.append(("logout",))

    def select(self, box, readonly=False):
        assert readonly  # reading never marks mail as read
        return "OK", [b"2"]

    def search(self, charset, *criteria):
        self.calls.append(("search", criteria))
        return "OK", [b"1 2"]

    def fetch(self, number, parts):
        headers = {b"1": b"Subject: Hello\r\nFrom: Sam <sam@example.com>\r\nDate: Sat, 25 Oct 2026 10:00:00 +0000\r\n\r\n",
                   b"2": b"Subject: =?utf-8?q?Invoice_=E2=82=AC5?=\r\nFrom: shop@example.com\r\n\r\n"}[number]
        flags = b"2 (FLAGS (\\Seen) BODY[HEADER] {10}" if number == b"1" else b"2 (FLAGS () BODY[HEADER] {10}"
        return "OK", [(flags, headers)]


@pytest.fixture
def mail(tmp_path):
    hosts, sent = [], []
    manager = AccountManager(Broker(), tmp_path / "accounts.json", request=lambda *a, **k: None, open_browser=lambda url: None)

    def imap(host):
        connection = FakeImap(host)
        hosts.append(connection)
        return connection

    class Smtp:
        def __init__(self, host, port, security):
            self.host = host

        def starttls(self):
            sent.append("starttls")

        def login(self, user, password):
            sent.append(("login", user, password))

        def send_message(self, message):
            sent.append(message)

        def quit(self):
            pass
    manager.imap_factory = imap
    manager.smtp_factory = Smtp
    return SimpleNamespace(manager=manager, hosts=hosts, sent=sent)


def test_email_connects_with_a_normal_login_checked_before_it_is_kept(mail):
    with pytest.raises(PermissionError, match="refused the login"):
        mail.manager.save_token("mail", "wrong", {"address": "me@zoho.com"})
    assert "account.mail" not in mail.manager.broker.values
    done = mail.manager.save_token("mail", "right-password", {"address": "Me@Zoho.com"})
    assert done["connected"] and done["connected_as"] == "me@zoho.com" and done["check_ok"]
    assert mail.hosts[-1].host == "imap.zoho.com"
    kept = json.loads(mail.manager.broker.values["account.mail"])
    assert kept["smtp"] == "smtp.zoho.com" and "right-password" not in (mail.manager.path.read_text(encoding="utf-8"))
    assert mail.manager.status("mail")["token_title"] == "Sign in with your email address and password"


def test_providers_that_refuse_passwords_say_what_to_do(mail):
    with pytest.raises(PermissionError, match="app password"):
        mail.manager.save_token("mail", "normal", {"address": "me@gmail.com"})
    with pytest.raises(PermissionError, match="Connect Microsoft"):
        mail.manager.save_token("mail", "whatever", {"address": "me@outlook.com"})
    with pytest.raises(PermissionError, match="Bridge"):
        mail.manager.save_token("mail", "whatever", {"address": "me@proton.me"})


def test_a_google_app_password_with_spaces_works_and_others_are_kept_as_typed(mail):
    mail.manager.imap_factory = lambda host: FakeImap(host, password="abcdefghijklmnop")
    assert mail.manager.save_token("mail", "abcd efgh ijkl mnop", {"address": "me@gmail.com"})["connected"]
    mail.manager.imap_factory = lambda host: FakeImap(host, password="my pass word")
    assert mail.manager.save_token("mail", "my pass word", {"address": "me@zoho.com"})["connected"]


def test_reading_mail_sending_after_confirmation_and_the_live_check(mail):
    mail.manager.save_token("mail", "right-password", {"address": "me@zoho.com"})
    rows = mail.manager.read("mail", "inbox")["rows"]
    assert rows[0]["title"] == "Invoice €5" and rows[0]["unread"] and rows[1]["title"] == "Hello" and not rows[1]["unread"]
    assert mail.manager.read("mail", "search", {"query": "invoice"})["rows"]
    title, details, run = mail.manager.prepare("mail", "send", {"to": "sam@example.com", "subject": "Hi", "body": "Hello"})
    assert not mail.sent and ("To (an email address)", "sam@example.com") in details  # nothing sent before confirming
    assert run()["sent"] and mail.sent[-1]["To"] == "sam@example.com"
    assert mail.manager.check("mail")["check_ok"]
    mail.manager.imap_factory = lambda host: FakeImap(host, password="changed")
    failed = mail.manager.check("mail")
    assert failed["connected"] and failed["check_ok"] is False and "sign in again" in failed["check_error"]


# ---------------------------------------------------------------- onion directory
README = """# Real-World Onion Sites
## Legend/Key for Symbols
## News
### [BBC](https://www.bbcnewsd73hkzno2ini43t4gblxvycyac5aw4gnv7t2rccijh7745uqd.onion/)
* proof: [link](https://www.bbc.co.uk/news/technology-50150981)
* check: <span title="attempts=1 code=200 exit=0">ok</span><span title="attempts=6 code=903 exit=97">x</span>
## Social Networks
### [Facebook](https://www.facebookwkhpilnemxj7asaniu7vnjjbiltxjqhye3mhbshg7kx5tfyd.onion/)
* proof: [link](https://www.facebook.com/onion-service)
* check: <span title="attempts=1 code=500 exit=0">x</span>
""" + "".join(f"## Blogs\n### [Blog {n}](http://{chr(97 + n % 26) * 52}abcd.onion/)\n* proof: [link](https://b{n}.example)\n" for n in range(20))


def test_the_directory_lists_sites_with_category_proof_and_reachability(tmp_path):
    directory = od.OnionDirectory(lambda url: README, tmp_path / "onion.json")
    bbc = directory.find("bbc")[0]
    assert bbc["category"] == "News" and bbc["proof"].startswith("https://www.bbc.co.uk") and (bbc["up"], bbc["checks"]) == (1, 2)
    assert directory.find(category="Social Networks")[0]["up"] == 1  # Facebook answers the check with a 500: still reachable
    # Kept for a week: the next reader does not fetch again.
    assert len(od.OnionDirectory(lambda url: pytest.fail("fetched again"), tmp_path / "onion.json").sites()) == len(directory.sites())


def test_links_are_checked_and_lookalike_copies_flagged(tmp_path):
    directory = od.OnionDirectory(lambda url: README, tmp_path / "onion.json")
    real = "bbcnewsd73hkzno2ini43t4gblxvycyac5aw4gnv7t2rccijh7745uqd"
    assert directory.check(f"http://{real}.onion/news")["verdict"] == "listed"
    assert directory.check(f"http://{real[:50]}aaaaaa.onion/")["verdict"] == "lookalike"
    assert directory.check("http://" + "z" * 56 + ".onion/")["verdict"] == "unknown"


def test_index_results_are_labelled_and_illegal_offers_withheld(tmp_path):
    directory = od.OnionDirectory(lambda url: README, tmp_path / "onion.json")
    results = [SimpleNamespace(title="Independent news", url="http://" + ("234567" * 10)[:56] + ".onion/", snippet="daily reporting"),
               SimpleNamespace(title="Cheap cocaine shop", url="http://" + "r" * 56 + ".onion/", snippet="escrow"),
               SimpleNamespace(title="BBC", url="https://www.bbcnewsd73hkzno2ini43t4gblxvycyac5aw4gnv7t2rccijh7745uqd.onion/",
                               snippet="")]
    out = od.describe_results(results, directory)
    assert [row["nature"] for row in out["results"]] == ["News & journalism", "News"]
    assert not out["results"][0]["verified"] and out["results"][1]["verified"] and out["withheld"] == 1
    assert od.request("show me onion sites for news") == {"query": "", "category": "News"}
    assert "check_url" in od.request("is this onion link real? http://" + "q" * 56 + ".onion")


# ---------------------------------------------------------------- deep people search
def test_deep_search_adds_published_contact_details_with_their_sources():
    def contacts(name, organization, site):
        return {"candidates": [{"name": "Ada Example", "channels": [
                    {"kind": "email", "value": "ada@example.org", "source_url": "https://example.org/team"}]},
                               {"name": "Someone Else", "channels": [{"kind": "email", "value": "x@example.org"}]}],
                "team_routes": [{"team_name": "Example press office", "kind": "email", "value": "press@example.org",
                                 "source_url": "https://example.org/press"}],
                "errors": [{"stage": "fetch", "error": "HTTPStatusError: source could not be read."}]}
    finder = PeopleFinder(lambda *a, **k: {}, None, None, contacts=contacts)
    person = {"name": "Ada Example", "organization": "Example Org", "routes": [], "organization_site": "https://example.org"}
    summary = finder._reach([person])
    values = [route["value"] for route in person["routes"]]
    assert values == ["ada@example.org", "press@example.org"]  # someone else's address on the same page is not hers
    assert person["routes"][0]["label"] == "Work e-mail (published on example.org)"
    assert summary["with_contact"] == 1 and summary["refused_pages"] == 1 and "unmask" in summary["note"]
    assert _routes_from_discovery({"name": "Nobody Here"}, {}) == []
