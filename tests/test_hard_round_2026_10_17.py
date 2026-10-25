"""Hard tests from the 2026-10-17 round: every case here failed (or was slow) before that round's fixes."""
import time

import numpy as np
import pytest

from app import invention_sim
from app.accounts_chat import account_request
from app.email_check import EmailAddressCheck, _runs, advice, spf_problem
from app.location_privacy import LocationShield, structured_positions, text_positions
from app.markets import market_request
from app.privacy_masks import local_dns
from app.research.people import PersonRequest


def route(text):
    found = account_request(text)
    if not found:
        return None
    args = found["arguments"]
    if found["tool"] == "account_status":
        return "account_status" + (":" + args["provider"] if args.get("provider") else "")
    if found["tool"] == "account_post_everywhere":
        return "account_post_everywhere"
    return f"{found['tool']}:{args['provider']}:{args['operation']}"


@pytest.mark.parametrize("text, expected", [
    ("check my bluesky", "account_read:bluesky:timeline"),
    ("check my mastodon", "account_read:mastodon:home"),
    ("top posts in r/learnpython", "account_read:reddit:hot"),
    ("can you check my github notifications please", "account_read:github:notifications"),
    ("hayat, post to bluesky: hello", "account_prepare:bluesky:post"),
    ("can you post to bluesky: hello", "account_prepare:bluesky:post"),
    ("tweet this: hello", "account_prepare:x:post"),
    ("could you post everywhere: launch day", "account_post_everywhere"),
    ("please go ahead and post to linkedin: hiring", "account_prepare:linkedin:post"),
    ("check my bluesky notifications", "account_read:bluesky:notifications"),
    ("open my outlook inbox", "account_read:microsoft:mail_inbox"),
    # Not account requests: the app or site, a question, or prose.
    ("open outlook", None), ("open microsoft", None), ("open linkedin", None), ("launch outlook", None),
    ("instagram", None), ("outlook", None), ("what is mastodon", None), ("toot toot", None),
    ("post-mortem: the outage was dns", None), ("i post to bluesky every day", None), ("check my email", None),
])
def test_account_routing(text, expected):
    assert route(text) == expected


@pytest.mark.parametrize("text", ["open outlook", "my outlook", "the outlook", "career outlook", "start outlook", "launch outlook"])
def test_everyday_outlook_is_not_a_ticker(text):
    assert market_request(text) is None


@pytest.mark.parametrize("text, symbol", [("bitcoin outlook", "BTC"), ("tsla outlook", "TSLA"), ("OPEN outlook", "OPEN"),
                                          ("nvda forecast", "NVDA"), ("eth forecast", "ETH")])
def test_real_tickers_keep_their_outlook(text, symbol):
    assert market_request(text)["arguments"]["symbol"] == symbol


ALLOWED = [("Geoffrey Hinton", "University of Toronto"), ("Jane Doe", "Children's Hospital of Philadelphia"),
           ("Ravi Kumar", "Home Office minister"), ("Aisha Khan", "Neighbourhood Watch founder"),
           ("Julia Child", "chef and author"), ("Masayoshi Son", "SoftBank"), ("Alec Douglas-Home", "Prime Minister"),
           ("Jane Smith", "her papers on graph theory"), ("Jane Smith", "Partner at a law firm"),
           ("Lee Daily", "Daily Mail journalist"), ("Ana Track", "Track and field coach")]
REFUSED = [("Jane Smith", "where she works out"), ("Jane Smith", "what gym she goes to"),
           ("Jane Smith", "her schedule this week"), ("Jane Smith", "her instagram account"), ("Jane Smith", "her car"),
           ("Jane Smith", "what does she look like"), ("Jane Smith", "when does she leave the office"),
           ("Jane Smith", "her mother's maiden name"), ("Jane Smith", "her ex"), ("Jane Smith", "is she pregnant"),
           ("Jane Smith", "her postcode"), ("Jane Smith", "her zip code"), ("Jane Smith", "find her"),
           ("Jane Smith", "locate her"), ("Jane Smith", "find this person"), ("Jane Smith", "their house"),
           ("Jane Smith", "her hotel"), ("Jane Smith", "Where Does She Live"), ("Jane Smith's home", ""),
           ("jane smith home", ""), ("Jane Smith Address", ""), ("Jane Smith Location", "")]


@pytest.mark.parametrize("name, context", ALLOWED)
def test_public_work_contexts_are_accepted(name, context):
    PersonRequest(name, context)


@pytest.mark.parametrize("name, context", REFUSED)
def test_personal_asks_are_refused_however_worded(name, context):
    with pytest.raises(ValueError):
        PersonRequest(name, context)


@pytest.fixture
def shield():
    guard = LocationShield()
    guard.remember_fix(51.5074, -0.1278, "SW1A 1AA")
    return guard


@pytest.mark.parametrize("text", [
    "https://api.example.com/weather?lat=51.5074&lon=-0.1278", "lat: 51.5074 lon: -0.1278", "51.5074 N, 0.1278 W",
    "51.5074°N 0.1278°W", "51°30'26.6\"N 0°07'40.1\"W", "51° 30.44' N, 0° 7.67' W", "-0.1278, 51.5074",
    "[-0.1278, 51.5074]", "51.51, -0.13", "51.5074%2C-0.1278", "51.5074 , −0.1278",
    "come over: sw1a 1aa", "come over: SW1A1AA", "plus code 9C3XGV4C+XX", "GV4C+XX London", "gcpvj0d",
    "https://www.openstreetmap.org/#map=17/51.5074/-0.1278"])
def test_every_way_of_writing_the_position_is_held(shield, text):
    _args, reason, _notes = shield.screen("account_prepare", "connector_write", {"params": {"text": text}})
    assert reason, text


@pytest.mark.parametrize("arguments", [
    {"params": {"text": "here", "location": {"lat": 51.5074, "lng": -0.1278}}},
    {"params": {"geometry": {"type": "Point", "coordinates": [-0.1278, 51.5074]}}},
    {"params": {"latitude": "51.5074", "longitude": "-0.1278"}}])
def test_positions_held_as_data_are_held_in_messages(shield, arguments):
    assert shield.screen("account_prepare", "connector_write", arguments)[1]


@pytest.mark.parametrize("text", ["Paris is at 48.8566, 2.3522", "meet at 3.30 in room 12.45", "version 51.50.1 shipped",
                                  "51.5, -0.1", "SW1A is a big area", "score 51.50 to 0.12 in the final"])
def test_ordinary_text_is_not_held(shield, text):
    assert shield.screen("account_prepare", "connector_write", {"params": {"text": text}})[1] is None


def test_nested_positions_sent_to_a_public_service_are_rounded(shield):
    arguments, reason, notes = shield.screen("public_api_get", "public_api_get",
                                             {"query": {"point": {"lat": 51.5074, "lng": -0.1278}}})
    assert reason is None and arguments["query"]["point"] == {"lat": 51.5, "lng": -0.1} and notes


def test_position_decoders():
    assert any(abs(lat - 48.8583) < 1e-3 and abs(lon - 2.2945) < 1e-3 for lat, lon in text_positions("8FW4V75V+8Q"))
    assert any(abs(lat - 48.858) < 1e-2 for lat, _lon in text_positions("u09tunq"))
    assert structured_positions({"lat": 51.5, "lon": -0.1}) == []  # rounded already: not precise


def resolver(records, delay=0.0):
    def resolve(name, kind):
        time.sleep(delay)
        if (name, kind) == ("missing.example", "MX"):
            raise LookupError("no such domain")
        return list(records.get((name, kind), []))
    return resolve


def test_null_mx_is_a_domain_that_takes_no_mail():
    mail = EmailAddressCheck(resolve=resolver({("example.com", "MX"): ["0 "]})).mail_setup("example.com")
    assert mail["exists"] is True and mail["null_mx"] is True and mail["mx"] == []
    assert "accepts no mail" in advice(mail, {})[0]
    assert EmailAddressCheck(resolve=resolver({})).mail_setup("missing.example")["exists"] is False


def test_providers_are_matched_by_domain_not_substring():
    assert _runs("mx.zoho.com", "zoho") and _runs("aspmx.l.google.com", "google.com")
    assert not _runs("notgoogle.com.evil.net", "google.com") and not _runs("mail.example.zoho", "zoho")


@pytest.mark.parametrize("record, problem", [("v=spf1 +all", "+all"), ("v=spf1 all", "+all"), ("v=spf1 ?all", "?all"),
                                             ("v=spf1 mx", "no all rule"), ("v=spf1 include:_spf.google.com ~all", ""),
                                             ("v=spf1 " + " ".join(f"include:s{i}.example" for i in range(11)) + " -all", "11 DNS")])
def test_spf_problems(record, problem):
    assert (problem in spf_problem(record)) if problem else spf_problem(record) == ""


def test_two_dmarc_records_are_reported_and_lookups_run_side_by_side():
    records = {("d.example", "MX"): ["10 mx.d.example"], ("d.example", "TXT"): ["v=spf1 mx -all"],
               ("_dmarc.d.example", "TXT"): ["v=DMARC1; p=reject", "v=DMARC1; p=none"]}
    start = time.perf_counter()
    mail = EmailAddressCheck(resolve=resolver(records, delay=0.1)).mail_setup("d.example")
    assert time.perf_counter() - start < 1.2  # 19 lookups of 0.1 s each took 1.9 s one after another
    assert mail["dmarc"] is None and mail["dmarc_problem"] and any("two DMARC" in tip or "DMARC records" in tip
                                                                    for tip in advice(mail, {}))


@pytest.mark.parametrize("server, local", [("192.168.1.1", True), ("fd00::1", True), ("fe80::1%12", True),
                                           ("fec0:0:0:ffff::1", False), ("1.1.1.1", False), ("100.64.0.1", False),
                                           ("2001:4860:4860::8888", False), ("garbage", False)])
def test_local_dns_servers(server, local):
    assert local_dns(server) is local


def test_linear_shapes_are_solved_directly():
    tests = [{"inputs": {"x": str(x)}, "expected": str(3 + 2 * x - 0.5 * x * x)} for x in range(-3, 5)]
    params = invention_sim.free_parameters("c1 + c2*x + c3*x**2", ["x"])
    rows = invention_sim._rows(tests)
    residuals = invention_sim._vector_residuals("c1 + c2*x + c3*x**2", params, rows)
    solved = invention_sim._linear_solution(residuals, len(params))
    assert solved is not None and np.allclose(solved.x, [3, 2, -0.5])
    assert invention_sim._linear_solution(invention_sim._vector_residuals("c1*exp(c2*x)", ["c1", "c2"], rows), 2) is None
    assert invention_sim.fit("c1 + c2*x + c3*x**2", tests)["formula"].replace(" ", "") in {"3+2*x-1/2*x**2", "3+2*x+-1/2*x**2",
                                                                                         "3+2*x-0.5*x**2"}


@pytest.mark.parametrize("formula", ["c1*sqrt(x - c2)", "c1*log(x*c2)", "c1*x**c2", "(x - c1)**(1/3)", "c1/(x - c2)",
                                     "min(c1*x, c2)", "max(c1, x, c2)", "hypot(c1, x)", "c1*asin(x/c2)", "c1"])
def test_vector_residuals_match_the_per_case_path(formula):
    rng = np.random.default_rng(5)
    params = invention_sim.free_parameters(formula, ["x"])
    rows = [({"x": x}, float(rng.normal(0, 10))) for x in (-3.0, -0.5, 0.0, 0.5, 2.0, 7.0, 1e5)]
    run = invention_sim.compile_formula(formula)
    vector = invention_sim._vector_residuals(formula, params, rows)
    for _ in range(25):
        theta = rng.normal(0, 3, len(params))
        expected = []
        for inputs, value in rows:
            try:
                expected.append((run({**inputs, **dict(zip(params, theta))}) - value) / max(1.0, abs(value)))
            except (ArithmeticError, ValueError, TypeError):
                expected.append(invention_sim._BIG_RESIDUAL)
        assert np.allclose(vector(theta), expected, rtol=1e-9, atol=1e-12)


def test_the_embedding_client_is_kept():
    from app.memory.embeddings import OllamaEmbeddingProvider
    provider = OllamaEmbeddingProvider.__new__(OllamaEmbeddingProvider)
    first = provider._client()
    assert provider._client() is first
    first.close()
    assert provider._client() is not first
