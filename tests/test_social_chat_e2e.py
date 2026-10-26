"""Social accounts through the real assistant: posts wait for the owner, unconnected accounts say how to connect."""
import json

import pytest

from app.assistant import AssistantService
from app.observed_reply import _plain_error
from app.runtime import build_runtime
from tests.test_accounts import Broker, Response


@pytest.fixture
def social(tmp_path):
    runtime = build_runtime(use_model=False, start_background_services=False)
    accounts = runtime.accounts
    accounts.broker = Broker()  # never the owner's Windows Credential Manager
    # Nor the owner's account records: the runtime's path is the live data/integrations/accounts.json (the test
    # sandbox redirects only databases), and this fixture's fake "Bluesky connected" record was left there, so
    # the real Accounts window showed a connection that had no sign-in behind it.
    accounts.path = tmp_path / "accounts.json"
    sent = []

    def fake(method, url, **kwargs):
        if "listnotifications" in url.lower():
            return Response(body={"notifications": [{"reason": "like", "author": {"handle": "friend.bsky.social"},
                                                    "indexedAt": "2026-10-17T10:00:00Z", "isRead": False}]})
        sent.append(url)
        return Response(body={"uri": "at://did/app.bsky.feed.post/1"})
    accounts._request = fake
    accounts.broker.store("account.bluesky", json.dumps({"access_token": "a", "did": "did:plc:x", "handle": "me.bsky.social"}),
                          scopes=["account:bluesky"])
    records = accounts._records()
    records["bluesky"] = {"connected": True, "connected_as": "@me.bsky.social", "method": "token"}
    accounts._save_records(records)
    try:
        yield runtime, sent
    finally:
        runtime.close()


def ask(runtime, text):
    return runtime.assistant.handle(text).display_response


def test_post_is_prepared_never_sent(social):
    runtime, sent = social
    before = len(runtime.confirmations.pending())
    reply = ask(runtime, "post to bluesky: hello from HAYAT")
    assert len(runtime.confirmations.pending()) == before + 1
    assert not sent, "a post went out before the owner confirmed it"
    assert "not approved" not in reply.lower()
    # The same post asked for twice waits once.
    ask(runtime, "post to bluesky: hello from HAYAT")
    assert len(runtime.confirmations.pending()) == before + 1


def test_reads_a_connected_account(social):
    runtime, _sent = social
    assert "friend.bsky.social" in ask(runtime, "check my bluesky notifications")


@pytest.mark.parametrize("text", ["tweet: shipping today", "my instagram posts", "toot: hi"])
def test_unconnected_account_says_how_to_connect(social, text):
    runtime, sent = social
    reply = ask(runtime, text)
    assert "not connected" in reply.lower() or "isn't connected" in reply.lower(), reply
    for wrong in ("not allowed", "no page saved", "not approved", "needs explicit approval"):
        assert wrong not in reply.lower(), reply
    assert not sent


def test_toot_and_skeet_are_the_owners_post_command():
    for text in ("toot: hi", "skeet: hi", "please toot this"):
        assert "connector_write" in AssistantService._outward_capabilities(text)
    assert not AssistantService._outward_capabilities("read the toot she sent")


def test_not_connected_failure_wording():
    said = _plain_error("account_read", {}, "PermissionError: X (Twitter) is not connected. Open Accounts & sign-in to connect it.")
    assert said.startswith("X (Twitter) isn't connected yet")
    assert "Accounts & sign-in" in said and "not allowed" not in said
