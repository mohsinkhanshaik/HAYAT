"""Bluesky, Facebook, Instagram, Threads, Discord and Telegram: signing in the easy way, kept safely, renewed."""
from __future__ import annotations

import base64
import json
import threading
import time
import urllib.request
from urllib.parse import parse_qs, unquote, urlsplit

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

import app.accounts as accounts
from app.accounts import AccountManager
from app.accounts_atproto import localhost_client_id, new_key, proof
from tests.test_accounts import Broker


class Reply:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code, self.body, self.headers = status, body if body is not None else {}, headers or {}
        self.content = json.dumps(self.body).encode()

    def json(self):
        return self.body


def unb64(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def verify(token, method, url, nonce="", access=""):
    """A DPoP proof that really is signed by the key it carries, for this request."""
    head, body, signature = token.split(".")
    header, payload = json.loads(unb64(head)), json.loads(unb64(body))
    assert header["typ"] == "dpop+jwt" and header["alg"] == "ES256"
    numbers = ec.EllipticCurvePublicNumbers(int.from_bytes(unb64(header["jwk"]["x"]), "big"),
                                            int.from_bytes(unb64(header["jwk"]["y"]), "big"), ec.SECP256R1())
    raw = unb64(signature)
    numbers.public_key().verify(encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")),
                                f"{head}.{body}".encode(), ec.ECDSA(hashes.SHA256()))
    assert payload["htm"] == method and payload["htu"] == url.split("?")[0] and payload.get("nonce", "") == nonce
    if access:
        assert payload["ath"] == base64.urlsafe_b64encode(__import__("hashlib").sha256(access.encode()).digest()).rstrip(b"=").decode()
    return header["jwk"]


def test_a_dpop_proof_is_signed_with_its_own_key():
    key = new_key()
    jwk = verify(proof(key, "POST", "https://pds.example/xrpc/x?y=1", "n1", "tok"), "POST", "https://pds.example/xrpc/x", "n1", "tok")
    assert jwk["crv"] == "P-256"
    assert localhost_client_id("http://127.0.0.1:5000/").startswith("http://localhost?redirect_uri=http%3A%2F%2F127.0.0.1%3A5000%2F")


class Bluesky:
    """bsky.social, plc.directory, the account's PDS and its authorization server, with DPoP nonces."""

    def __init__(self, did="did:plc:owner"):
        self.did, self.calls, self.jwk, self.par_form = did, [], None, None

    def __call__(self, method, url, **kwargs):
        self.calls.append((method, url))
        headers = kwargs.get("headers") or {}
        if url.startswith("https://bsky.social/xrpc/com.atproto.identity.resolveHandle"):
            return Reply(body={"did": "did:plc:owner"})
        if url == "https://plc.directory/did:plc:owner":
            return Reply(body={"service": [{"id": "#atproto_pds", "serviceEndpoint": "https://pds.example"}]})
        if url == "https://pds.example/.well-known/oauth-protected-resource":
            return Reply(body={"authorization_servers": ["https://auth.example"]})
        if url == "https://auth.example/.well-known/oauth-authorization-server":
            return Reply(body={"pushed_authorization_request_endpoint": "https://auth.example/par",
                               "authorization_endpoint": "https://auth.example/authorize", "token_endpoint": "https://auth.example/token"})
        if url in ("https://auth.example/par", "https://auth.example/token"):
            nonce = "as-nonce"
            if f"\"nonce\":\"{nonce}\"" not in unb64(headers["DPoP"].split(".")[1]).decode():
                return Reply(400, {"error": "use_dpop_nonce"}, {"DPoP-Nonce": nonce})
            jwk = verify(headers["DPoP"], "POST", url, nonce)
            assert self.jwk in (None, jwk)  # one key for the whole sign-in
            self.jwk = jwk
            form = kwargs["data"]
            assert form["client_id"].startswith("http://localhost?redirect_uri=")
            if url.endswith("/par"):
                assert form["code_challenge_method"] == "S256" and form["login_hint"] == "owner.bsky.social"
                self.par_form = form
                return Reply(201, {"request_uri": "urn:ietf:params:oauth:request_uri:abc"})
            if form["grant_type"] == "refresh_token":
                return Reply(body={"access_token": "at-2", "refresh_token": "rt-2", "sub": self.did, "expires_in": 3600})
            assert form["code"] == "the-code" and form["code_verifier"]
            return Reply(body={"access_token": "at-1", "refresh_token": "rt-1", "sub": self.did, "expires_in": 3600})
        if url.startswith("https://pds.example/xrpc/"):
            token = headers["Authorization"].removeprefix("DPoP ")
            if "\"nonce\":\"pds-nonce\"" not in unb64(headers["DPoP"].split(".")[1]).decode():
                return Reply(401, {"error": "use_dpop_nonce"}, {"DPoP-Nonce": "pds-nonce", "WWW-Authenticate": 'DPoP error="use_dpop_nonce"'})
            verify(headers["DPoP"], method, url, "pds-nonce", token)
            if url.endswith("com.atproto.server.getSession"):
                return Reply(body={"handle": "owner.bsky.social", "did": "did:plc:owner"})
            if url.endswith("app.bsky.feed.getTimeline"):
                return Reply(body={"feed": [{"post": {"author": {"handle": "friend.bsky.social"}, "record": {"text": "hello"},
                                                      "uri": "at://x/app.bsky.feed.post/1"}}]})
            if url.endswith("com.atproto.repo.createRecord"):
                assert kwargs["json"]["repo"] == "did:plc:owner"
                return Reply(body={"uri": "at://did:plc:owner/app.bsky.feed.post/9"})
        raise AssertionError("unexpected " + url)


def approve(link, pages=None):
    query = parse_qs(urlsplit(link).query)
    request_uri = query.get("request_uri", [""])[0]
    redirect = query.get("redirect_uri", [""])[0] or unquote(query["client_id"][0]).split("redirect_uri=")[1].split("&")[0]
    state = query.get("state", [""])[0]
    return redirect, state, request_uri


def browser_for(server):
    def open_link(link):
        redirect, _state, request_uri = approve(link)
        assert request_uri == "urn:ietf:params:oauth:request_uri:abc" and link.startswith("https://auth.example/authorize?")
        state = server.par_form["state"]
        threading.Thread(target=lambda: urllib.request.urlopen(f"{redirect}?code=the-code&state={state}&iss=https://auth.example",
                                                               timeout=20).read(), daemon=True).start()
    return open_link


def test_bluesky_signs_in_with_the_browser_and_dpop_with_no_setup(tmp_path):
    server, broker = Bluesky(), Broker()
    manager = AccountManager(broker, tmp_path / "a.json", request=server, open_browser=browser_for(server))
    status = manager.status("bluesky")
    assert status["methods"][0]["key"] == "browser" and status["methods"][0]["ready"] and not status["needs_app"]
    done = manager.sign_in_browser("bluesky", timeout=10, server="@Owner.bsky.social")
    assert done["connected_as"] == "@owner.bsky.social" and done["method"] == "browser"
    kept = json.loads(broker.values["account.bluesky"])
    assert kept["method"] == "oauth" and kept["pds"] == "https://pds.example" and "PRIVATE KEY" in kept["dpop_key"]
    assert "at-1" not in (tmp_path / "a.json").read_text(encoding="utf-8")  # tokens and key only in the credential store
    assert manager.read("bluesky", "timeline")["rows"][0]["title"] == "hello"
    title, details, run = manager.prepare("bluesky", "post", {"text": "Hi from HAYAT"})
    assert run()["posted"]
    # A restart, then an expired token: renewed with the same DPoP key, and still working.
    again = AccountManager(broker, tmp_path / "a.json", request=server, open_browser=lambda link: None)
    value = json.loads(broker.values["account.bluesky"])
    value["expires_at"] = time.time()
    broker.values["account.bluesky"] = json.dumps(value)
    assert again.check("bluesky")["check_ok"] and json.loads(broker.values["account.bluesky"])["access_token"] == "at-2"


def test_bluesky_refuses_tokens_for_another_account(tmp_path):
    server = Bluesky(did="did:plc:someone-else")
    manager = AccountManager(Broker(), tmp_path / "a.json", request=server, open_browser=browser_for(server))
    with pytest.raises(PermissionError, match="different account"):
        manager.sign_in_browser("bluesky", timeout=10, server="owner.bsky.social")
    assert not manager.status("bluesky")["connected"]


def code_browser(link):
    query = parse_qs(urlsplit(link).query)
    threading.Thread(target=lambda: urllib.request.urlopen(f"{query['redirect_uri'][0]}?code=the-code&state={query['state'][0]}",
                                                           timeout=20).read(), daemon=True).start()


def test_facebook_signs_in_and_keeps_the_page_token(tmp_path):
    def meta(method, url, **kwargs):
        params = kwargs.get("params") or {}
        if url == "https://graph.facebook.com/v21.0/oauth/access_token" and method == "POST":
            assert kwargs["data"]["client_secret"] == "fb-secret"
            return Reply(body={"access_token": "short-user"})
        if url == "https://graph.facebook.com/v21.0/oauth/access_token":
            assert params["grant_type"] == "fb_exchange_token" and params["fb_exchange_token"] == "short-user"
            return Reply(body={"access_token": "long-user"})
        if url == "https://graph.facebook.com/v21.0/me/accounts":
            assert params["access_token"] == "long-user"
            return Reply(body={"data": [{"id": "111", "name": "My Shop", "access_token": "page-token"}]})
        if url == "https://graph.facebook.com/111":
            assert kwargs["headers"]["Authorization"] == "Bearer page-token"
            return Reply(body={"name": "My Shop"})
        raise AssertionError(url)
    manager = AccountManager(Broker(), tmp_path / "a.json", request=meta, open_browser=code_browser)
    manager._builtin_apps = {"facebook": {"client_id": "app-1", "client_secret": "fb-secret"}}
    link_scope = []
    manager.open_browser = lambda link: (link_scope.append(parse_qs(urlsplit(link).query)["scope"][0]), code_browser(link))
    done = manager.sign_in_browser("facebook", timeout=10)
    assert done["connected_as"] == "Page: My Shop" and link_scope == ["pages_show_list,pages_read_engagement,pages_manage_posts,"
                                                                       "instagram_basic,business_management"]
    assert manager._records()["facebook"]["settings"]["page"] == "111"


def test_threads_keeps_a_60_day_token_and_renews_it_in_its_last_week(tmp_path):
    def meta(method, url, **kwargs):
        params = kwargs.get("params") or {}
        if url == "https://graph.threads.net/oauth/access_token":
            return Reply(body={"access_token": "short", "user_id": "9"})
        if url == "https://graph.threads.net/access_token":
            assert params["grant_type"] == "th_exchange_token" and params["access_token"] == "short"
            return Reply(body={"access_token": "long", "expires_in": 5_184_000})
        if url == "https://graph.threads.net/refresh_access_token":
            return Reply(body={"access_token": "long-2", "expires_in": 5_184_000})
        if url == "https://graph.threads.net/v1.0/me":
            return Reply(body={"username": "owner"})
        raise AssertionError(url)
    broker = Broker()
    manager = AccountManager(broker, tmp_path / "a.json", request=meta, open_browser=code_browser)
    manager._builtin_apps = {"threads": {"client_id": "t-app", "client_secret": "t-secret"}}
    assert manager.sign_in_browser("threads", timeout=10)["connected_as"] == "@owner"
    value = json.loads(broker.values["account.threads"])
    assert value["access_token"] == "long" and value["expires_at"] - time.time() > 50 * 86400
    value["expires_at"] = time.time() + 3 * 86400
    broker.values["account.threads"] = json.dumps(value)
    assert manager._token("threads") == "long-2"


def test_discord_picks_a_channel_in_the_browser_and_posts_through_its_webhook(tmp_path):
    posted = []

    def discord(method, url, **kwargs):
        if url == "https://discord.com/api/oauth2/token":
            assert kwargs["data"]["client_secret"] == "d-secret"
            return Reply(body={"access_token": "x", "webhook": {"url": "https://discord.com/api/webhooks/1/abc", "channel_id": "5"}})
        if url == "https://discord.com/api/webhooks/1/abc":
            posted.append(kwargs["json"]["content"])
            return Reply(204, {})
        raise AssertionError(url)
    manager = AccountManager(Broker(), tmp_path / "a.json", request=discord, open_browser=code_browser)
    manager._builtin_apps = {"discord": {"client_id": "d-app", "client_secret": "d-secret"}}
    done = manager.sign_in_browser("discord", timeout=10)
    assert done["connected"] and done["method"] == "webhook"
    manager.prepare("discord", "post_message", {"text": "Hello"})[2]()
    assert posted == ["Hello"]


def test_telegram_links_the_chat_that_tapped_start(tmp_path, monkeypatch):
    bot = "123456789:" + "A" * 35
    sent = []

    def telegram(method, url, **kwargs):
        if url.endswith("/getMe"):
            return Reply(body={"ok": True, "result": {"username": "hayat_owner_bot", "first_name": "HAYAT"}})
        if url.endswith("/getUpdates"):
            return Reply(body={"ok": True, "result": [
                {"message": {"text": "hi", "chat": {"id": 7, "type": "group", "title": "Family"}}},
                {"message": {"text": "/start hayat", "chat": {"id": 42, "type": "private", "first_name": "Owner"}}}]})
        if url.endswith("/sendMessage"):
            sent.append(kwargs["json"])
            return Reply(body={"ok": True, "result": {"message_id": 1}})
        raise AssertionError(url)
    manager = AccountManager(Broker(), tmp_path / "a.json", request=telegram, open_browser=lambda link: None)
    manager.save_token("telegram", bot)
    assert manager.telegram_bot_link() == "https://t.me/hayat_owner_bot?start=hayat"
    assert manager.telegram_link_chat()["linked_chat"] == "Owner"
    manager.prepare("telegram", "send", {"text": "Reminder"})[2]()
    assert sent == [{"chat_id": "42", "text": "Reminder"}]


def test_every_platform_says_how_it_signs_in(tmp_path):
    manager = AccountManager(Broker(), tmp_path / "a.json", request=lambda *a, **k: Reply(), open_browser=lambda link: None)
    titles = {key: manager.status(key)["browser_title"] for key in ("bluesky", "facebook", "threads", "discord")}
    assert titles == {"bluesky": "Sign in with Bluesky in your browser", "facebook": "Sign in with Facebook in your browser",
                      "threads": "Sign in with Threads in your browser", "discord": "Choose a server and channel in your browser"}
    assert accounts.redirect_address(accounts.PROVIDERS["facebook"]) == "http://localhost:8799/"
    assert accounts.redirect_address(accounts.PROVIDERS["discord"]) == "http://127.0.0.1:8795/"
