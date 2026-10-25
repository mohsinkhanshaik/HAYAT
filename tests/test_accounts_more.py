"""LinkedIn and Slack's extra ways, posting from chat (prepared, never sent unconfirmed), and replies said aloud."""
import json
import threading
import urllib.request
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from app.accounts import AccountManager, AccountTools
from app.accounts_chat import account_request
from app.safety.confirmations import ConfirmationGate
from app.speech_text import speakable
from tests.test_accounts import Broker, Response


class LinkedInAndSlack:
    def __init__(self):
        self.calls = []

    def __call__(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        auth = (kwargs.get("headers") or {}).get("Authorization", "")
        if url == "https://www.linkedin.com/oauth/v2/accessToken":
            form = kwargs["data"]
            assert "code_verifier" not in form and form["client_secret"] == "li-secret" and form["redirect_uri"].endswith(":8796/")
            return Response(body={"access_token": "li-token", "expires_in": 5183999})
        if url == "https://api.linkedin.com/v2/userinfo":
            assert auth == "Bearer li-token"
            return Response(body={"sub": "abc123", "name": "Owner Name", "email": "owner@example.com"})
        if url == "https://api.linkedin.com/v2/ugcPosts":
            assert kwargs["headers"]["X-Restli-Protocol-Version"] == "2.0.0"
            return Response(201, {"id": "urn:li:share:1"})
        if url.startswith("https://hooks.slack.com/services/"):
            return Response(200, {})
        if url == "https://slack.com/api/conversations.list":
            return Response(body={"ok": True, "channels": [{"id": "C1", "name": "general"}]})
        if url == "https://slack.com/api/conversations.history":
            assert kwargs["params"]["channel"] == "C1"
            return Response(body={"ok": True, "messages": [{"text": "Standup at 10", "user": "U1", "ts": "1791700000.0001"}]})
        raise AssertionError("unexpected " + url)


@pytest.fixture
def setup(tmp_path):
    fake, broker = LinkedInAndSlack(), Broker()
    manager = AccountManager(broker, tmp_path / "accounts.json", request=fake)
    return SimpleNamespace(fake=fake, broker=broker, manager=manager, path=tmp_path / "accounts.json")


def test_linkedin_signs_in_without_pkce_on_its_fixed_address_and_posts_only_when_confirmed(setup):
    setup.manager.set_app("linkedin", "86abcdefgh", "li-secret")

    def browser(url):
        query = parse_qs(urlsplit(url).query)
        assert url.startswith("https://www.linkedin.com/oauth/v2/authorization?") and "code_challenge" not in query
        assert query["redirect_uri"] == ["http://127.0.0.1:8796/"] and "w_member_social" in query["scope"][0]
        threading.Thread(target=lambda: urllib.request.urlopen(f"http://127.0.0.1:8796/?code=li-code&state={query['state'][0]}",
                                                               timeout=5).read(), daemon=True).start()
    setup.manager.open_browser = browser
    done = setup.manager.sign_in_browser("linkedin", timeout=10)
    assert done["connected_as"] == "Owner Name" and done["methods"] == [{"key": "browser", "label": "Sign in with your browser", "ready": True}]
    gate = ConfirmationGate()
    proposal = AccountTools(setup.manager, gate).prepare("linkedin", "post", {"text": "We launched!"})
    assert not any(url.endswith("ugcPosts") for _method, url, _kwargs in setup.fake.calls)
    gate.confirm(proposal["action_id"], by_owner=True)
    posted = next(kwargs for _method, url, kwargs in setup.fake.calls if url.endswith("ugcPosts"))["json"]
    assert posted["author"] == "urn:li:person:abc123" and \
        posted["specificContent"]["com.linkedin.ugc.ShareContent"]["shareCommentary"]["text"] == "We launched!"


def test_a_slack_webhook_posts_but_never_reads(setup):
    with pytest.raises(ValueError, match="hooks.slack.com"):
        setup.manager.save_webhook("slack", "https://evil.example.com/hook")
    done = setup.manager.save_webhook("slack", "https://hooks.slack.com/services/T0/B0/xyz")
    assert done["connected"] and done["method"] == "webhook" and "posting only" in done["connected_as"]
    with pytest.raises(PermissionError, match="only post"):
        setup.manager.read("slack", "channels")
    gate = ConfirmationGate()
    proposal = AccountTools(setup.manager, gate).prepare("slack", "post_message", {"channel": "#general", "text": "Hello"})
    assert gate.confirm(proposal["action_id"], by_owner=True)["result"] == {"sent": True, "where": "the webhook's channel"}
    hook = next(kwargs for _method, url, kwargs in setup.fake.calls if "hooks.slack.com" in url)
    assert hook["json"] == {"text": "Hello"}


def test_slack_channel_history_resolves_a_channel_name(setup):
    setup.broker.store("account.slack", json.dumps({"access_token": "xoxb-1", "method": "token"}), scopes=["account:slack"])
    setup.path.write_text(json.dumps({"slack": {"connected": True, "connected_as": "bot", "method": "token"}}), encoding="utf-8")
    rows = setup.manager.read("slack", "history", {"channel": "#general"})["rows"]
    assert rows[0]["title"] == "Standup at 10" and rows[0]["when"].startswith("2026-")


def test_chat_understands_everyday_phrasings_and_prepares_posts():
    assert account_request("do I have new emails in gmail?")["arguments"] == {"provider": "google", "operation": "gmail_inbox"}
    assert account_request("anything new on github")["arguments"] == {"provider": "github", "operation": "notifications"}
    assert account_request("what's on my outlook calendar today")["arguments"]["provider"] == "microsoft"
    assert account_request("post to linkedin: We just launched our new website!") == {
        "tool": "account_prepare", "arguments": {"provider": "linkedin", "operation": "post",
                                                 "params": {"text": "We just launched our new website!"}}}
    assert account_request("post to slack #general: standup in 5")["arguments"]["params"] == {"channel": "#general", "text": "standup in 5"}
    assert account_request("post to #general: hi") is None and account_request("connect my linkedin account")["arguments"] == {"provider": "linkedin"}


def test_replies_are_said_in_words_and_whole_sentences():
    assert speakable("Hello") == "Hello" and speakable("I couldn't find that file.") == "I couldn't find that file."
    said = speakable("BTC price on Kraken: $83,560.00 · +1.12% since 00:00 UTC · 24 h range $82,716.10 – $84,125.50")
    assert said == "BTC price on Kraken: 83,560 dollars, up 1.12 percent since 00:00 UTC, 24 hours range 82,716 dollars to 84,125 dollars."
    said = speakable("Ethereum address vitalik.eth (ethereum, eth.blockscout.com):\n  0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045\n  Balance 5.7687 ETH")
    assert said == "Ethereum address vitalik.eth. Balance 5.7687 ETH."
    assert speakable("Fees 2 sat/vB · hashrate 1,028 EH/s · see https://mempool.space") == \
        "Fees 2 sats per byte, hashrate 1,028 exahashes per second, see a link."
    long = "\n".join(f"Line number {index} says something useful about the market today." for index in range(20))
    spoken = speakable(long, 120)
    assert len(spoken) <= 120 and spoken.endswith(".") and spoken.count("Line number") == 2
    assert speakable("") == "" and speakable("DeFi: $92.42 B locked").startswith("DeFi: 92.42 billion dollars")
