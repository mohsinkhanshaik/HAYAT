"""Social accounts: Bluesky, Mastodon, Reddit, X, Telegram, Discord, Facebook Page & Instagram, Threads, YouTube.

Every platform's endpoints are a stand-in that checks what HAYAT sends (headers, payloads) and answers like the
real API. Nothing posts until the confirmation gate runs the prepared action.
"""
import base64
import json
import threading
import time
import urllib.request
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from app.accounts import PROVIDERS, AccountManager, AccountTools
from app.accounts_chat import account_request, report
from app.safety.confirmations import ConfirmationGate
from tests.test_accounts import Broker, Response

BOT = "123456789:AAEhBP0av28K3cv5vZzUq9X5QgNfYyFy0ZQ"


class Social:
    """The platforms' APIs, recording what was sent."""

    def __init__(self):
        self.calls, self.sent, self.updates = [], {}, [{"message": {"chat": {"id": 4242}, "from": {"first_name": "Mohsin"},
                                                                   "text": "hi bot", "date": 1760700000}}]

    def __call__(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        auth = (kwargs.get("headers") or {}).get("Authorization", "")
        # Bluesky
        if url == "https://bsky.social/xrpc/com.atproto.server.createSession":
            body = kwargs["json"]
            if body != {"identifier": "mohsin.bsky.social", "password": "abcd-efgh-ijkl-mnop"}:
                return Response(401, {"error": "AuthenticationRequired", "message": "Invalid identifier or password"})
            return Response(body={"accessJwt": "bsky-access", "refreshJwt": "bsky-refresh", "did": "did:plc:owner",
                                  "handle": "mohsin.bsky.social"})
        if url == "https://bsky.social/xrpc/com.atproto.server.refreshSession":
            assert auth == "Bearer bsky-refresh"
            return Response(body={"accessJwt": "bsky-access-2", "refreshJwt": "bsky-refresh-2"})
        if url == "https://bsky.social/xrpc/com.atproto.server.getSession":
            assert auth.startswith("Bearer bsky-access")
            return Response(body={"handle": "mohsin.bsky.social", "did": "did:plc:owner"})
        if url == "https://bsky.social/xrpc/app.bsky.feed.getTimeline":
            assert auth.startswith("Bearer bsky-access")
            return Response(body={"feed": [{"post": {"uri": "at://did:plc:a/app.bsky.feed.post/3kabc", "indexedAt": "2026-10-17T10:00:00Z",
                                                     "author": {"handle": "friend.bsky.social"}, "record": {"text": "Hello Bluesky"}}}]})
        if url == "https://bsky.social/xrpc/com.atproto.repo.createRecord":
            self.sent["bluesky"] = kwargs["json"]
            return Response(body={"uri": "at://did:plc:owner/app.bsky.feed.post/3kpost"})
        # Mastodon
        if url == "https://mastodon.social/api/v1/accounts/verify_credentials":
            assert auth == "Bearer masto-token"
            return Response(body={"acct": "mohsin", "display_name": "Mohsin", "followers_count": 12, "statuses_count": 40})
        if url == "https://mastodon.social/api/v1/timelines/home":
            return Response(body=[{"content": "<p>Hello &amp; welcome</p>", "account": {"acct": "friend"},
                                   "created_at": "2026-10-17T09:00:00Z", "url": "https://mastodon.social/@friend/1"}])
        if url == "https://mastodon.social/api/v1/statuses":
            self.sent["mastodon"] = kwargs["data"]
            return Response(body={"url": "https://mastodon.social/@mohsin/2"})
        # Reddit
        if url == "https://www.reddit.com/api/v1/access_token":
            self.sent["reddit_token_auth"] = (kwargs.get("headers") or {}).get("Authorization")
            return Response(body={"access_token": "reddit-access", "refresh_token": "reddit-refresh", "expires_in": 3600})
        if url == "https://oauth.reddit.com/api/v1/me":
            assert auth == "Bearer reddit-access" and "hayat" in kwargs["headers"]["User-Agent"]
            return Response(body={"name": "mohsin_k", "link_karma": 10, "comment_karma": 5})
        if url == "https://oauth.reddit.com/api/submit":
            self.sent["reddit"] = kwargs["data"]
            return Response(body={"json": {"errors": [], "data": {"url": "https://www.reddit.com/r/test/comments/x"}}})
        # X
        if url == "https://api.x.com/2/oauth2/token":
            return Response(body={"access_token": "x-access", "refresh_token": "x-refresh", "expires_in": 7200})
        if url == "https://api.x.com/2/users/me":
            return Response(body={"data": {"username": "mohsin_k", "name": "Mohsin", "public_metrics": {"followers_count": 3}}})
        if url == "https://api.x.com/2/tweets":
            self.sent["x"] = kwargs["json"]
            return Response(body={"data": {"id": "1800"}})
        # Telegram (the token travels in the address, never in a header)
        if url.startswith("https://api.telegram.org/bot"):
            assert "Authorization" not in (kwargs.get("headers") or {})
            if not url.startswith(f"https://api.telegram.org/bot{BOT}/"):
                return Response(body={"ok": False, "description": "Unauthorized"})
            name = url.rsplit("/", 1)[-1]
            if name == "getMe":
                return Response(body={"ok": True, "result": {"username": "hayat_owner_bot", "first_name": "HAYAT"}})
            if name == "getUpdates":
                return Response(body={"ok": True, "result": self.updates})
            if name == "sendMessage":
                self.sent["telegram"] = kwargs["json"]
                return Response(body={"ok": True, "result": {"message_id": 7}})
        # Discord
        if url.startswith("https://discord.com/api/webhooks/"):
            self.sent["discord_webhook"] = kwargs["json"]
            return Response(204)
        if url == "https://discord.com/api/v10/users/@me":
            assert auth == "Bot discord-bot-token"
            return Response(body={"username": "HAYAT"})
        if url == "https://discord.com/api/v10/channels/1234567890/messages":
            if method == "POST":
                self.sent["discord"] = kwargs["json"]
                return Response(body={"id": "99"})
            return Response(body=[{"content": "deploy done", "author": {"username": "ci"}, "timestamp": "2026-10-17T08:00:00Z"}])
        # Facebook Page and Instagram
        if url == "https://graph.facebook.com/112233445566":
            fields = (kwargs.get("params") or {}).get("fields", "")
            if fields.startswith("instagram_business_account"):
                return Response(body={"instagram_business_account": {"id": "17841400000000000", "username": "mohsin.shop"}})
            return Response(body={"name": "Mohsin's Shop", "followers_count": 210, "link": "https://facebook.com/mohsinshop"})
        if url == "https://graph.facebook.com/17841400000000000/media":
            return Response(body={"data": [{"caption": "New stock", "media_type": "IMAGE", "timestamp": "2026-10-16T12:00:00+0000",
                                            "permalink": "https://instagram.com/p/abc"}]})
        if url == "https://graph.facebook.com/112233445566/feed":
            self.sent["facebook"] = kwargs["data"]
            return Response(body={"id": "112233445566_777"})
        # Threads
        if url == "https://graph.threads.net/v1.0/me":
            return Response(body={"username": "mohsin_k", "name": "Mohsin"})
        if url == "https://graph.threads.net/v1.0/me/threads":
            self.sent["threads_container"] = kwargs["data"]
            return Response(body={"id": "container-1"})
        if url == "https://graph.threads.net/v1.0/me/threads_publish":
            self.sent["threads"] = kwargs["data"]
            return Response(body={"id": "thread-1"})
        # YouTube
        if url == "https://www.googleapis.com/youtube/v3/channels":
            return Response(body={"items": [{"id": "UC123", "snippet": {"title": "Mohsin Builds"},
                                             "statistics": {"subscriberCount": "1500", "videoCount": "42", "viewCount": "90000"},
                                             "contentDetails": {"relatedPlaylists": {"uploads": "UU123"}}}]})
        if url == "https://www.googleapis.com/youtube/v3/playlistItems":
            assert kwargs["params"]["playlistId"] == "UU123"
            return Response(body={"items": [{"snippet": {"title": "Building HAYAT", "publishedAt": "2026-10-15T00:00:00Z",
                                                         "resourceId": {"videoId": "vid1"}}}]})
        raise AssertionError("unexpected " + method + " " + url)


@pytest.fixture
def setup(tmp_path):
    fake, broker = Social(), Broker()
    manager = AccountManager(broker, tmp_path / "accounts.json", request=fake, open_browser=lambda url: None)
    gate = ConfirmationGate()
    return SimpleNamespace(fake=fake, broker=broker, manager=manager, gate=gate, tools=AccountTools(manager, gate))


def confirm_all(gate):
    """The owner confirming every prepared action, as the confirmation window does."""
    for pending in gate.pending():
        gate.confirm(pending["action_id"], by_owner=True)


# -- what is offered, and what is not -------------------------------------------------------------

def test_social_accounts_are_grouped_and_the_unofferable_are_named_with_why(setup):
    status = setup.manager.status()
    groups = {row["key"]: row["group"] for row in status["accounts"]}
    assert all(groups[key] == "Social" for key in ("bluesky", "mastodon", "reddit", "x", "facebook", "threads", "linkedin"))
    assert all(groups[key] == "Messaging" for key in ("telegram", "discord", "slack"))
    assert {item["name"] for item in status["not_offered"]} >= {"TikTok", "Pinterest", "Snapchat"}
    google = next(row for row in status["accounts"] if row["key"] == "google")
    assert {"youtube_channel", "youtube_uploads"} <= {op["key"] for op in google["operations"]}
    bluesky = next(row for row in status["accounts"] if row["key"] == "bluesky")
    assert bluesky["token_label"] == "app password" and bluesky["token_fields"][0]["name"] == "handle"


# -- Bluesky -------------------------------------------------------------------------------------------

def test_bluesky_keeps_the_session_not_the_app_password(setup):
    with pytest.raises(RuntimeError, match="401"):
        setup.manager.save_token("bluesky", "wrong-wrong-wrong", {"handle": "mohsin.bsky.social"})
    done = setup.manager.save_token("bluesky", "abcd-efgh-ijkl-mnop", {"handle": "@Mohsin.bsky.social"})
    assert done["connected"] and done["connected_as"] == "@mohsin.bsky.social"
    stored = setup.broker.values["account.bluesky"]
    assert "abcd-efgh-ijkl-mnop" not in stored and json.loads(stored)["did"] == "did:plc:owner"
    rows = setup.manager.read("bluesky", "timeline")["rows"]
    assert rows[0]["title"] == "Hello Bluesky" and rows[0]["link"] == "https://bsky.app/profile/friend.bsky.social/post/3kabc"


def test_bluesky_renews_its_session_before_it_expires(setup):
    setup.manager.save_token("bluesky", "abcd-efgh-ijkl-mnop", {"handle": "mohsin.bsky.social"})
    value = json.loads(setup.broker.values["account.bluesky"])
    value["expires_at"] = time.time() + 10
    setup.broker.values["account.bluesky"] = json.dumps(value)
    assert setup.manager._token("bluesky") == "bsky-access-2"
    assert json.loads(setup.broker.values["account.bluesky"])["refresh_token"] == "bsky-refresh-2"


def test_a_bluesky_post_waits_for_confirmation_then_goes_to_the_owners_repo(setup):
    setup.manager.save_token("bluesky", "abcd-efgh-ijkl-mnop", {"handle": "mohsin.bsky.social"})
    setup.tools.prepare("bluesky", "post", {"text": "Launch day"})
    assert "bluesky" not in setup.fake.sent  # prepared only
    confirm_all(setup.gate)
    record = setup.fake.sent["bluesky"]
    assert record["repo"] == "did:plc:owner" and record["record"]["text"] == "Launch day"
    assert record["record"]["$type"] == "app.bsky.feed.post" and record["record"]["createdAt"].endswith("Z")
    with pytest.raises(ValueError, match="300"):
        setup.manager._write_bluesky("post", {"text": "x" * 301})


# -- Mastodon ------------------------------------------------------------------------------------------

def test_mastodon_checks_the_server_reads_and_posts_plain_text(setup):
    with pytest.raises(ValueError, match="server"):
        setup.manager.save_token("mastodon", "masto-token", {"server": "not a server"})
    done = setup.manager.save_token("mastodon", "masto-token", {"server": "https://Mastodon.social/"})
    assert done["connected_as"] == "@mohsin@mastodon.social"
    assert setup.manager.read("mastodon", "home")["rows"][0]["title"] == "Hello & welcome"
    setup.tools.prepare("mastodon", "post", {"text": "Hello fediverse"})
    confirm_all(setup.gate)
    assert setup.fake.sent["mastodon"] == {"status": "Hello fediverse"}


# -- Reddit ---------------------------------------------------------------------------------------------

def test_reddit_signs_in_with_the_browser_and_authenticates_the_app_with_basic_auth(setup):
    setup.manager.set_app("reddit", "reddit-client-id")

    def browser(url):
        query = parse_qs(urlsplit(url).query)
        assert url.startswith("https://www.reddit.com/api/v1/authorize?") and query["duration"] == ["permanent"]
        assert "code_challenge" not in query and query["redirect_uri"] == ["http://127.0.0.1:8798/"]
        threading.Thread(target=lambda: urllib.request.urlopen(f"http://127.0.0.1:8798/?code=c1&state={query['state'][0]}",
                                                               timeout=5).read(), daemon=True).start()
    setup.manager.open_browser = browser
    done = setup.manager.sign_in_browser("reddit", timeout=10)
    assert done["connected_as"] == "u/mohsin_k"
    assert setup.fake.sent["reddit_token_auth"] == "Basic " + base64.b64encode(b"reddit-client-id:").decode()
    setup.tools.prepare("reddit", "submit", {"subreddit": "r/test", "title": "Hello", "text": "First post"})
    confirm_all(setup.gate)
    assert setup.fake.sent["reddit"]["sr"] == "test" and setup.fake.sent["reddit"]["kind"] == "self"


# -- X ----------------------------------------------------------------------------------------------------

def test_x_uses_pkce_on_its_fixed_port_and_posts_within_280_characters(setup):
    setup.manager.set_app("x", "x-client-id")

    def browser(url):
        query = parse_qs(urlsplit(url).query)
        assert query["code_challenge_method"] == ["S256"] and query["redirect_uri"] == ["http://127.0.0.1:8797/"]
        assert "tweet.write" in query["scope"][0]
        threading.Thread(target=lambda: urllib.request.urlopen(f"http://127.0.0.1:8797/?code=c2&state={query['state'][0]}",
                                                               timeout=5).read(), daemon=True).start()
    setup.manager.open_browser = browser
    assert setup.manager.sign_in_browser("x", timeout=10)["connected_as"] == "@mohsin_k"
    setup.tools.prepare("x", "post", {"text": "Shipping"})
    confirm_all(setup.gate)
    assert setup.fake.sent["x"] == {"text": "Shipping"}
    with pytest.raises(ValueError, match="280"):
        setup.manager._write_x("post", {"text": "y" * 281})


# -- Telegram ---------------------------------------------------------------------------------------------

def test_telegram_checks_the_token_shape_reads_and_messages_the_last_chat(setup):
    with pytest.raises(ValueError, match="bot token"):
        setup.manager.save_token("telegram", "not-a-bot-token-at-all")
    assert setup.manager.save_token("telegram", BOT)["connected_as"] == "@hayat_owner_bot"
    rows = setup.manager.read("telegram", "messages")["rows"]
    assert rows[0]["title"] == "hi bot" and rows[0]["detail"] == "Mohsin"
    setup.tools.prepare("telegram", "send", {"text": "Your report is ready"})
    confirm_all(setup.gate)
    assert setup.fake.sent["telegram"] == {"chat_id": "4242", "text": "Your report is ready"}
    setup.fake.updates = []
    with pytest.raises(ValueError, match="send it a message"):
        setup.manager._write_telegram("send", {"chat": "", "text": "hi"})


# -- Discord ----------------------------------------------------------------------------------------------

def test_discord_webhook_posts_only_and_a_bot_reads_with_its_own_header(setup):
    with pytest.raises(ValueError, match="discord.com/api/webhooks"):
        setup.manager.save_webhook("discord", "https://hooks.slack.com/services/T/B/C")
    setup.manager.save_webhook("discord", "https://discord.com/api/webhooks/1/abc")
    setup.tools.prepare("discord", "post_message", {"text": "Build passed"})
    confirm_all(setup.gate)
    assert setup.fake.sent["discord_webhook"] == {"content": "Build passed"}
    with pytest.raises(PermissionError, match="webhook"):
        setup.manager.read("discord", "servers")
    setup.manager.save_token("discord", "discord-bot-token")
    assert setup.manager.read("discord", "history", {"channel": "1234567890"})["rows"][0]["title"] == "deploy done"


# -- Facebook Page, Instagram, Threads -----------------------------------------------------------------

def test_a_facebook_page_reads_its_posts_and_instagram_and_posts_once_confirmed(setup):
    with pytest.raises(ValueError, match="Page ID"):
        setup.manager.save_token("facebook", "EAAGpagetoken123", {"page": "my page"})
    assert setup.manager.save_token("facebook", "EAAGpagetoken123", {"page": "112233445566"})["connected_as"] == "Page: Mohsin's Shop"
    assert setup.manager.read("facebook", "instagram")["rows"][0]["title"] == "New stock"
    setup.tools.prepare("facebook", "post", {"text": "Open on Sunday"})
    confirm_all(setup.gate)
    assert setup.fake.sent["facebook"] == {"message": "Open on Sunday"}


def test_threads_publishes_in_two_steps(setup):
    setup.manager.save_token("threads", "THQWJthreads-token")
    setup.tools.prepare("threads", "post", {"text": "Hello Threads"})
    confirm_all(setup.gate)
    assert setup.fake.sent["threads_container"] == {"media_type": "TEXT", "text": "Hello Threads"}
    assert setup.fake.sent["threads"] == {"creation_id": "container-1"}


# -- YouTube through Google --------------------------------------------------------------------------------

def test_youtube_channel_and_uploads_come_through_the_google_sign_in(setup):
    setup.broker.store("account.google", json.dumps({"access_token": "g-access", "refresh_token": "", "expires_at": None}),
                       scopes=["account:google"])
    records = {"google": {"connected": True, "connected_as": "owner@gmail.com", "client_id": "x"}}
    setup.manager._save_records(records)
    channel = setup.manager.read("google", "youtube_channel")["rows"][0]
    assert channel["title"] == "Mohsin Builds" and "1,500 subscribers" in channel["detail"]
    assert setup.manager.read("google", "youtube_uploads")["rows"][0]["link"] == "https://www.youtube.com/watch?v=vid1"


# -- post everywhere ---------------------------------------------------------------------------------------

def test_post_everywhere_prepares_one_confirmation_per_account_and_skips_what_does_not_fit(setup):
    setup.manager.save_token("bluesky", "abcd-efgh-ijkl-mnop", {"handle": "mohsin.bsky.social"})
    setup.manager.save_token("mastodon", "masto-token", {"server": "mastodon.social"})
    setup.manager.save_token("threads", "THQWJthreads-token")
    long = "word " * 62  # 310 characters: past Bluesky's 300, inside Mastodon's and Threads' 500
    result = setup.tools.post_everywhere(long)
    assert [item["name"] for item in result["prepared"]] == ["Mastodon", "Threads"]
    assert result["skipped"] == [{"name": "Bluesky", "why": "longer than its 300-character limit"}]
    assert len(setup.gate.pending()) == 2 and not setup.fake.sent  # nothing sent yet
    assert "Left out Bluesky" in report([{"tool": "account_post_everywhere", "result": {"success": True, "output": result},
                                          "verification": {"passed": True}}])


def test_post_everywhere_with_nothing_connected_says_how_to_connect(setup):
    with pytest.raises(PermissionError, match="Accounts & sign-in"):
        setup.tools.post_everywhere("hello")


# -- asking in chat -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("text, tool, provider, operation", [
    ("post to bluesky: hello world", "account_prepare", "bluesky", "post"),
    ("tweet: shipping today", "account_prepare", "x", "post"),
    ("toot: hi fediverse", "account_prepare", "mastodon", "post"),
    ("post to my facebook page: open today", "account_prepare", "facebook", "post"),
    ("post to discord: build passed", "account_prepare", "discord", "post_message"),
    ("telegram me: remind me at 5", "account_prepare", "telegram", "send"),
    ("check my bluesky notifications", "account_read", "bluesky", "notifications"),
    ("my mastodon timeline", "account_read", "mastodon", "home"),
    ("what is hot in r/python", "account_read", "reddit", "hot"),
    ("my youtube channel", "account_read", "google", "youtube_channel"),
    ("my instagram posts", "account_read", "facebook", "instagram"),
])
def test_social_requests_in_chat(text, tool, provider, operation):
    found = account_request(text)
    assert found["tool"] == tool and found["arguments"]["provider"] == provider and found["arguments"]["operation"] == operation


def test_post_everywhere_and_connecting_in_chat():
    assert account_request("post everywhere: we launched!") == {"tool": "account_post_everywhere", "arguments": {"text": "we launched!"}}
    assert account_request("connect twitter")["arguments"] == {"provider": "x"}
    assert account_request("connect my instagram")["arguments"] == {"provider": "facebook"}
    assert account_request("send an email to bob") is None


def test_every_provider_has_a_reader_and_every_write_a_writer():
    for key, provider in PROVIDERS.items():
        assert callable(getattr(AccountManager, f"_read_{key}"))
        if any(op.write for op in provider.operations):
            assert callable(getattr(AccountManager, f"_write_{key}"))


def test_accounts_that_can_post_everywhere_are_marked(setup):
    rows = {row["key"]: row["can_post"] for row in setup.manager.status()["accounts"]}
    assert all(rows[key] for key in ("bluesky", "mastodon", "x", "threads", "linkedin", "facebook"))
    assert not any(rows[key] for key in ("google", "github", "telegram", "reddit"))


def test_the_window_groups_in_order_names_the_secret_and_enables_post_everywhere(shared_tk, setup):
    import app.gui_accounts as window_module
    from app.gui_theme import Palette

    setup.manager.save_token("bluesky", "abcd-efgh-ijkl-mnop", {"handle": "mohsin.bsky.social"})

    class GUI(Palette):
        pass
    gui = GUI()
    gui.root, gui.runtime = shared_tk, SimpleNamespace(accounts=setup.manager, confirmations=setup.gate, email=None,
                                                       secret_broker=setup.broker)
    window = window_module.AccountsCenter(gui)
    try:
        assert list(window.group_widgets) == ["Mail & calendar", "Social", "Messaging", "Work"]
        assert window.everywhere_button.instate(["!disabled"])
        window.select("telegram")
        shared_tk.update()
        texts = []

        def walk(widget):
            for child in widget.winfo_children():
                try:
                    texts.append(child.cget("text"))
                except Exception:
                    pass
                walk(child)
        walk(window.connect_tab)
        assert "1. Paste a bot token" in texts and any(text.startswith("Make a bot token") for text in texts)
        window.filter.set("bluesky")
        shared_tk.update()
        assert window.card_widgets["bluesky"]["card"].winfo_ismapped() and not window.card_widgets["google"]["card"].winfo_ismapped()
    finally:
        window.destroy()
