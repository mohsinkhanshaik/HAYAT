"""The email-address check: mail setup from DNS, where the address is published, and never a made-up location.

DNS, web pages and the location service are stand-ins here; the live check is described in
docs/email-address-check.md.
"""
import pytest

from app.email_check import NO_LOCATION, EmailAddressCheck, advice, email_check_request

GOOD = {
    ("hamuzair.com", "MX"): ["1 aspmx.l.google.com", "5 alt1.aspmx.l.google.com"],
    ("hamuzair.com", "TXT"): ["v=spf1 include:_spf.google.com ~all", "google-site-verification=abc"],
    ("_dmarc.hamuzair.com", "TXT"): ["v=DMARC1; p=quarantine; rua=mailto:d@hamuzair.com"],
    ("google._domainkey.hamuzair.com", "TXT"): ["v=DKIM1; k=rsa; p=MIIB"],
}


def resolver(records):
    def resolve(name, kind):
        if name.endswith("nowhere.invalid"):
            raise LookupError("no such domain")
        return records.get((name, kind), [])
    return resolve


def pages(by_url):
    def fetch(url):
        if url not in by_url:
            error = RuntimeError("404")
            error.response = type("R", (), {"status_code": 404})()
            raise error
        if by_url[url] is PermissionError:
            raise PermissionError("robots")
        return by_url[url].encode()
    return fetch


def test_a_well_set_up_domain_is_read_and_needs_no_fixes():
    checker = EmailAddressCheck(resolve=resolver(GOOD), fetch=pages({"https://hamuzair.com/": "<p>Welcome</p>"}))
    result = checker.check("MohsinKhanShaik@hamuzair.com")
    mail = result["mail"]
    assert result["address"] == "mohsinkhanshaik@hamuzair.com"
    assert mail["provider"] == "Google Workspace (Gmail)" and mail["mx"][0] == "aspmx.l.google.com"
    assert mail["spf"].startswith("v=spf1") and mail["dmarc_policy"] == "quarantine" and mail["dkim_selectors_found"] == ["google"]
    assert result["advice"] == [] and result["published"]["checked"] == ["https://hamuzair.com/"]


def test_missing_spf_dmarc_and_dkim_are_each_explained():
    records = {("shop.example", "MX"): ["10 mx1.titan.email"]}
    result = EmailAddressCheck(resolve=resolver(records)).check("sales@shop.example")
    assert result["mail"]["provider"] == "Titan email"
    tips = " ".join(result["advice"])
    assert "Publish an SPF record" in tips and "_dmarc.shop.example" in tips and "No DKIM key" in tips


def test_two_spf_records_and_a_monitoring_only_dmarc_are_flagged():
    records = {("d.example", "MX"): ["10 mx.d.example"], ("d.example", "TXT"): ["v=spf1 a ~all", "v=spf1 mx ~all"],
               ("_dmarc.d.example", "TXT"): ["v=DMARC1; p=none"]}
    result = EmailAddressCheck(resolve=resolver(records)).check("a@d.example")
    assert result["mail"]["spf"] is None and result["mail"]["spf_problem"].startswith("two SPF records")
    assert "Merge the two SPF records" in " ".join(result["advice"]) and "p=none" in " ".join(result["advice"])
    assert result["mail"]["provider"] == "its own or an unrecognised mail server"


def test_a_domain_that_does_not_exist_says_so():
    result = EmailAddressCheck(resolve=resolver({})).check("x@nowhere.invalid")
    assert result["mail"]["exists"] is False and "does not exist" in result["advice"][0]


def test_an_address_printed_on_the_site_is_found_even_written_with_at():
    fetch = pages({"https://hamuzair.com/": "<p>Hi</p>", "https://hamuzair.com/contact": "Mail mohsinkhanshaik [at] hamuzair.com",
                   "https://hamuzair.com/contact-us": PermissionError})
    result = EmailAddressCheck(resolve=resolver(GOOD), fetch=fetch).check("mohsinkhanshaik@hamuzair.com")
    assert result["published"]["found_on"] == ["https://hamuzair.com/contact"]
    assert "robots.txt" in result["published"]["notes"][0]
    assert "harvesters" in " ".join(result["advice"])


def test_an_address_is_never_given_a_location():
    located = []
    checker = EmailAddressCheck(resolve=resolver(GOOD), locate=lambda: located.append(1) or {"method": "x"})
    result = checker.check("someone@hamuzair.com")
    assert result["location"]["answer"] == NO_LOCATION and "you_now" not in result["location"]
    assert not located  # not the owner's address: this computer's location is not even read
    assert "check my email address" in result["location"]["note"]


def test_the_owners_own_address_says_where_the_owner_is_now():
    here = {"method": "windows_location_service", "windows_location": {"status": "Ready"}, "latitude": 42.47,
            "longitude": -83.22, "place": {"label": "Southfield, Michigan, United States"}}
    checker = EmailAddressCheck(resolve=resolver(GOOD), locate=lambda: here,
                                own_addresses=lambda: {"mohsinkhanshaik@hamuzair.com"})
    result = checker.check("mohsinkhanshaik@hamuzair.com")
    assert result["mine"] is True and result["location"]["you_now"] == here
    assert EmailAddressCheck(resolve=resolver(GOOD), locate=lambda: here).check("a@hamuzair.com", mine=True)["location"]["you_now"]


def test_a_bad_address_is_refused():
    with pytest.raises(ValueError):
        EmailAddressCheck(resolve=resolver({})).check("not an address")


def test_advice_is_empty_when_dns_could_not_be_read():
    assert advice({"domain": "x", "error": "DNS could not be read"}, {}) == []


@pytest.mark.parametrize("text, mine", [
    ("find current location of mohsinkhanshaik@hamuzair.com", False),
    ("where is my email mohsinkhanshaik@hamuzair.com", True),
    ("check my email address mohsinkhanshaik@hamuzair.com", True),
    ("check the mail setup of sales@shop.example", False),
    ("check dmarc for sales@shop.example", False),
    ("is my email a@b.com set up correctly?", True),
    ("a@b.com location", False),
])
def test_owner_phrasings_reach_the_check(text, mine):
    request = email_check_request(text)
    assert request["tool"] == "email_address_check" and request["arguments"]["mine"] is mine


@pytest.mark.parametrize("text", ["send an email to a@b.com", "email a@b.com about the location of the meeting",
                                  "reply to a@b.com", "save a@b.com as Sam's email", "forward this to a@b.com"])
def test_messages_and_contacts_keep_their_own_tools(text):
    assert email_check_request(text) is None


def test_the_assistant_answers_the_learned_question_without_inventing_a_place(monkeypatch):
    """The question behind the learned skill, end to end: no IP trace, no city for the address."""
    from app import email_check
    from app.runtime import build_runtime

    real_mail_setup = email_check.EmailAddressCheck.mail_setup
    monkeypatch.setattr(email_check.EmailAddressCheck, "mail_setup",
                        lambda self, domain: real_mail_setup(EmailAddressCheck(resolve=resolver(GOOD)), domain))
    monkeypatch.setattr(email_check.EmailAddressCheck, "published",
                        lambda self, domain, address: {"checked": ["https://hamuzair.com/"], "found_on": []})
    runtime = build_runtime(use_model=False, start_background_services=False)
    try:
        assert "email_address_check" in runtime.registry.names()
        reply = runtime.assistant.handle("find current location of mohsinkhanshaik@hamuzair.com").display_response
        assert "carries no location" in reply and "Google Workspace" in reply and "DMARC: p=quarantine" in reply
        assert "Charlotte" not in reply and "IP trace" not in reply
        assert 'check my email address' in reply
    finally:
        runtime.close()


def test_zohos_default_dkim_selector_is_found():
    """hamuzair.com publishes DKIM under Zoho's 'zmail' selector; missing it once produced a false 'turn DKIM on'."""
    records = {("z.example", "MX"): ["10 mx.zoho.in"], ("z.example", "TXT"): ["v=spf1 include:zoho.in ~all"],
               ("_dmarc.z.example", "TXT"): ["v=DMARC1; p=reject"], ("zmail._domainkey.z.example", "TXT"): ["v=DKIM1; k=rsa; p=MIGf"]}
    result = EmailAddressCheck(resolve=resolver(records)).check("me@z.example")
    assert result["mail"]["provider"] == "Zoho Mail" and result["mail"]["dkim_selectors_found"] == ["zmail"]
    assert result["advice"] == []
