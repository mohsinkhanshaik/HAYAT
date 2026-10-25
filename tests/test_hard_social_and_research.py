"""Hard tests: attacks on the social accounts and public-work research, each one a problem found on 2026-10-17.

Every case here failed before its fix: a bot token quoted in a network error, local "servers" accepted, emoji and
CJK counted wrong, crashes on odd replies, duplicate posts queued, surveillance asks accepted as names, and
unbounded text from OpenAlex. The fuzz test throws random replies at every read of every account.
"""
import json
import random

import httpx
import pytest

from app import accounts as accounts_module
from app.accounts import PROVIDERS, AccountManager, AccountTools, public_server
from app.accounts_social import graphemes, x_weight
from app.research.people import PersonRequest, parse_person_request
from app.research.scholarly import Scholarly, scholarly_lines
from app.safety.confirmations import ConfirmationGate
from tests.test_accounts import Broker, Response

BOT = "123456789:AAEhBP0av28K3cv5vZzUq9X5QgNfYyFy0ZQ"
FAMILY = "\U0001F468‍\U0001F469‍\U0001F467‍\U0001F466"  # one family emoji: seven code points


def connected(tmp_path, request, key, secret, settings=None, name="acct.json"):
    """An account already connected, without a sign-in: the record and the broker's value."""
    broker = Broker()
    manager = AccountManager(broker, tmp_path / name, request=request, open_browser=lambda url: None)
    broker.store(f"account.{key}", json.dumps(secret), scopes=[f"account:{key}"])
    record = {"connected": True, "connected_as": "owner", "method": secret.get("method", "token")}
    if settings:
        record["settings"] = settings
    manager._save_records({key: record})
    return manager


# -- secrets never travel in an error -----------------------------------------------------------------

def test_a_network_error_never_quotes_the_bot_token(tmp_path):
    def down(method, url, **kwargs):
        raise httpx.ConnectError(f"All connection attempts failed for {url}")
    manager = connected(tmp_path, down, "telegram", {"access_token": BOT})
    with pytest.raises(RuntimeError) as caught:
        manager.read("telegram", "messages")
    assert BOT not in str(caught.value) and BOT.split(":")[1] not in str(caught.value)
    assert "api.telegram.org could not be reached (ConnectError)" in str(caught.value)


def test_a_webhook_failure_never_quotes_the_webhook_key(tmp_path):
    hook = "https://discord.com/api/webhooks/1234/very-secret-webhook-key"

    def down(method, url, **kwargs):
        raise httpx.ReadTimeout(f"timed out on {url}")
    manager = connected(tmp_path, down, "discord", {"webhook": hook, "method": "webhook", "access_token": ""})
    title, details, run = manager.prepare("discord", "post_message", {"text": "hello"})
    with pytest.raises(RuntimeError) as caught:
        run()
    assert "very-secret-webhook-key" not in str(caught.value)


def test_a_reply_that_is_not_json_is_named(tmp_path):
    class Html:
        status_code, content = 200, b"<html>maintenance</html>"

        def json(self):
            raise json.JSONDecodeError("Expecting value", "<html>", 0)
    manager = connected(tmp_path, lambda method, url, **kwargs: Html(), "threads", {"access_token": "t"})
    with pytest.raises(RuntimeError, match="not JSON"):
        manager.read("threads", "posts")


# -- a typed server must be on the public internet ---------------------------------------------------

@pytest.mark.parametrize("host", ["localhost", "localhost.localdomain", "router.lan.home", "intranet.corp",
                                  "printer.local", "127.0.0.1.nip.io", "169.254.169.254.nip.io", "box.internal"])
def test_local_names_are_refused_without_even_asking_dns(host):
    def resolve(name):
        raise AssertionError("a local name must be refused before any lookup")
    with pytest.raises(ValueError):
        public_server(host, resolve)


@pytest.mark.parametrize("addresses", [["10.0.0.5"], ["192.168.12.1"], ["127.0.0.1"], ["169.254.169.254"],
                                       ["203.0.113.9", "10.1.1.1"], ["fd00::1"], ["::1"]])
def test_a_name_that_resolves_to_a_private_address_is_refused(addresses):
    with pytest.raises(ValueError, match="private or local"):
        public_server("mastodon.example-social.org", lambda name: addresses)


def test_a_public_server_passes_and_numbers_or_unknown_names_do_not():
    assert public_server("Mastodon.Social", lambda name: ["157.240.1.35"]) == "mastodon.social"
    with pytest.raises(ValueError, match="name, not a number"):
        public_server("8.8.8.8", lambda name: ["8.8.8.8"])

    def missing(name):
        raise OSError("no such host")
    with pytest.raises(ValueError, match="could not be found"):
        public_server("no-such-server-hayat.social", missing)


def test_a_server_that_later_points_inside_the_network_is_refused_at_use(tmp_path, monkeypatch):
    """Saved while public, re-pointed afterwards (DNS rebinding): every read checks again."""
    asked = []
    manager = connected(tmp_path, lambda method, url, **kwargs: asked.append(url) or Response(body=[]), "mastodon",
                        {"access_token": "m"}, {"server": "masto.example-social.org"})
    monkeypatch.setattr(accounts_module, "public_server", lambda host, resolve=None: (_ for _ in ()).throw(
        ValueError(f"{host} points to a private or local address, so it is not used.")))
    with pytest.raises(ValueError, match="private or local"):
        manager.read("mastodon", "home")
    assert not asked  # nothing was sent


# -- lengths the way each network counts them ----------------------------------------------------------

def test_visible_characters_and_xs_weights():
    assert graphemes(FAMILY) == 1 and graphemes("é") == 1 and graphemes("hello") == 5
    assert x_weight("hello") == 5 and x_weight("中文") == 4 and x_weight(FAMILY) == 2
    assert x_weight("read https://example.org/a/very/long/path/that/is/long") == 5 + 23


def test_bluesky_takes_300_emoji_and_x_refuses_heavy_text(tmp_path):
    sent = {}
    manager = connected(tmp_path, lambda method, url, **kwargs: sent.update(kwargs.get("json") or {})
                        or Response(body={"uri": "at://did/app.bsky.feed.post/1"}), "bluesky",
                        {"access_token": "a", "did": "did:plc:x", "handle": "me.bsky.social"})
    manager._write_bluesky("post", {"text": FAMILY * 300})  # 300 visible characters: allowed
    with pytest.raises(ValueError):
        manager._write_bluesky("post", {"text": FAMILY * 301})
    xs = connected(tmp_path, lambda method, url, **kwargs: Response(body={"data": {"id": "1"}}), "x", {"access_token": "x"},
                   name="x.json")
    xs._write_x("post", {"text": "中" * 140})  # weight 280: exactly the limit
    with pytest.raises(ValueError, match="two"):
        xs._write_x("post", {"text": "中" * 141})


def test_post_everywhere_measures_each_network_its_own_way(tmp_path):
    broker = Broker()
    manager = AccountManager(broker, tmp_path / "a.json", request=lambda *a, **k: Response(body={}), open_browser=lambda url: None)
    records = {}
    for key in ("x", "bluesky", "mastodon"):
        broker.store(f"account.{key}", json.dumps({"access_token": "t"}), scopes=[f"account:{key}"])
        records[key] = {"connected": True, "connected_as": "owner", "method": "token"}
    records["mastodon"]["settings"] = {"server": "mastodon.social"}
    manager._save_records(records)
    tools = AccountTools(manager, ConfirmationGate())
    result = tools.post_everywhere("中" * 150)  # 150 characters, but X weighs it 300
    assert [item["name"] for item in result["prepared"]] == ["Bluesky", "Mastodon"]
    assert result["skipped"] == [{"name": "X (Twitter)", "why": "longer than its 280-character limit"}]


# -- odd replies are explained, never a crash --------------------------------------------------------------

SHAPES = [[], ["x"], "text", 42, None, {}, {"data": "x"}, {"result": 7}, {"feed": [1, "a", None]}, {"items": [{}]},
          {"data": {"children": "no"}}, {"ok": True, "result": "x"}, {"json": {"errors": "x"}}]
SAMPLE = {"subreddit": "python", "channel": "1234567890", "query": "x", "chat": "", "text": "hi", "title": "t", "repo": "a/b",
          "to": "a@b.c", "subject": "s", "body": "b", "start": "2026-10-20 10:00", "end": "2026-10-20 11:00"}


@pytest.mark.parametrize("key", sorted(PROVIDERS))
def test_every_read_of_every_account_survives_random_replies(tmp_path, key, monkeypatch):
    monkeypatch.setattr(accounts_module, "public_server", lambda host, resolve=None: host)
    rng = random.Random(key)

    def chaos(method, url, **kwargs):
        return Response(rng.choice([200, 200, 200, 400, 401, 403, 429, 500, 503]), rng.choice(SHAPES))
    secret = {"access_token": BOT if key == "telegram" else "token", "did": "did:plc:x", "handle": "me.bsky.social",
              "method": "token"}
    calls = []

    def counted(method, url, **kwargs):
        calls.append(url)
        return chaos(method, url, **kwargs)
    manager = connected(tmp_path, counted, key, secret, {"server": "mastodon.social", "page": "112233", "handle": "me.bsky.social"})
    for operation in PROVIDERS[key].operations:
        params = {name: SAMPLE.get(name, "x") for name, _label, _required in operation.fields}
        for _attempt in range(6):
            try:
                if operation.write:
                    manager.prepare(key, operation.key, params)[2]()
                else:
                    manager.read(key, operation.key, params)
            except (RuntimeError, PermissionError, ValueError):
                pass  # explained in words
            # anything else (AttributeError, TypeError, KeyError...) fails the test with its traceback
    assert len(calls) >= 3 * len(PROVIDERS[key].operations)  # the random replies really were met, not skipped


# -- one action waits once ------------------------------------------------------------------------------------

def test_the_same_post_asked_twice_waits_once_and_a_different_one_waits_too(tmp_path):
    manager = connected(tmp_path, lambda *a, **k: Response(body={}), "bluesky",
                        {"access_token": "a", "did": "did:plc:x", "handle": "me.bsky.social"})
    gate = ConfirmationGate()
    tools = AccountTools(manager, gate)
    first = tools.prepare("bluesky", "post", {"text": "Launch day"})
    again = tools.prepare("bluesky", "post", {"text": "Launch day"})
    assert again["already_waiting"] and again["action_id"] == first["action_id"] and len(gate.pending()) == 1
    tools.post_everywhere("Launch day")
    assert len(gate.pending()) == 1
    tools.prepare("bluesky", "post", {"text": "Launch day, take two"})
    assert len(gate.pending()) == 2


# -- public-work research refuses surveillance however it is worded --------------------------------------------

@pytest.mark.parametrize("text", [
    "research person Jane Example where does she live", "research person Jane Example kids",
    "research person Jane Example daily routine", "research person Jane Example license plate",
    "research person Jane Example at 12 Elm Street Springfield", "research person Jane Example home address",
    "research person Jane Example phone", "research person jane@example.com", "research person +1 555 123 4567",
    "research person Jane Example current location", "research person Jane Example whereabouts",
    "research person Jane Example girlfriend", "research person Jane Example photos of her",
    "research person Jane Example ip address", "research person Jane Example commute",
])
def test_surveillance_asks_are_refused_before_any_search(text):
    with pytest.raises(ValueError):
        parse_person_request(text)


@pytest.mark.parametrize("context", ["Partner at Baker Law LLP", "Family Medicine Institute", "Daily Mail journalist",
                                     "car designer at Toyota", "Face Research Lab, Glasgow", "Location scout at Netflix",
                                     "Universidad de Sevilla", "Children's Hospital oncology department"])
def test_real_work_contexts_still_pass(context):
    # "Children's Hospital" is a workplace; the context check refuses only someone's own children ("her kids").
    assert PersonRequest("Jane Example", context).context == context


@pytest.mark.parametrize("context", ["her kids' school", "Jane's children", "his wife", "their daughters"])
def test_asks_about_someones_own_family_in_the_context_are_refused(context):
    with pytest.raises(ValueError):
        PersonRequest("Jane Example", context)


# -- outside text is bounded ----------------------------------------------------------------------------------

def test_scholarly_text_from_outside_is_bounded():
    huge = {"results": [{"id": "https://openalex.org/A1", "display_name": "Jane Example " + "X" * 5000, "works_count": 1,
                         "topics": [{"display_name": "T" * 5000}], "last_known_institutions": [{"display_name": "I" * 5000}]}]}
    works = {"results": [{"display_name": "<script>alert(1)</script>" + "W" * 5000}]}
    records = Scholarly(lambda url: huge if "/authors?" in url else works).records("Jane Example")
    assert max(len(line) for line in scholarly_lines(records)) < 700
    assert records["candidates"][0]["top_works"][0]["title"].endswith("…")
