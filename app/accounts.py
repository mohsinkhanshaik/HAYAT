"""Accounts & sign-in: the owner connects their own accounts, and HAYAT works with them.

Ways to sign in, easiest first. In every one of them the owner signs in on the provider's own
page; HAYAT never sees or stores a password.

1. **Sign in with a code** (OAuth 2.0 device authorization, RFC 8628): HAYAT shows a short code
   and opens the provider's sign-in page; the owner signs in there, types the code and approves.
2. **Sign in with your browser** (OAuth 2.0 authorization code with PKCE, RFC 7636, and a
   loopback redirect, RFC 8252): the provider's page opens; after the owner approves it
   returns to a one-time listener on 127.0.0.1 that checks the state value and closes.
3. **Paste a token**: a personal access token or integration token the owner made on the
   provider's site. It is checked with one read before it is kept.

Tokens are kept by the secret broker (Windows Credential Manager, or an encrypted file) and
reach a request only through a single-use, scoped lease. They never enter a log, the model's
context or a tool result. Access tokens are refreshed before they expire. Sign out deletes them
and revokes the token where the provider allows it.

Reading (inbox, calendar, files, repositories, notifications, search) runs directly. Anything
that sends or changes something is only *prepared* here; it runs once the owner confirms it in
the confirmation window (app/safety/confirmations.py), never on a model's say-so.

The OAuth apps (client IDs) are the owner's own: HAYAT shows the steps and opens the page, and
the owner registers the app. HAYAT does not register accounts or apps for the owner.
"""
from __future__ import annotations

import base64
import hashlib
import html
import json
import logging
import re
import secrets
import threading
import time
import webbrowser
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, quote, urlencode, urlsplit

import httpx

from app.accounts_mail import MailAccounts, mail_provider
from app.accounts_social import NOT_OFFERED, SocialAccounts, social_providers
from app.logging import log_swallowed

_log = logging.getLogger(__name__)
SIGN_IN_SECONDS = 300


@dataclass(frozen=True)
class Operation:
    key: str
    label: str
    write: bool = False
    #: (name, plain label, required) for the values the owner gives.
    fields: tuple[tuple[str, str, bool], ...] = ()


@dataclass(frozen=True)
class Provider:
    key: str
    name: str
    what: str
    methods: tuple[str, ...]
    scopes: tuple[str, ...] = ()
    device_url: str = ""
    authorize_url: str = ""
    token_url: str = ""
    revoke_url: str = ""
    #: The browser sign-in sends the app's client secret too (Google desktop apps, GitHub OAuth apps).
    browser_needs_secret: bool = False
    loopback_host: str = "127.0.0.1"
    app_page: str = ""
    app_steps: tuple[str, ...] = ()
    token_page: str = ""
    token_steps: tuple[str, ...] = ()
    operations: tuple[Operation, ...] = ()
    extra_authorize: dict[str, str] = field(default_factory=dict)
    #: LinkedIn's standard apps take no PKCE and need the exact redirect address, port included.
    pkce: bool = True
    loopback_port: int = 0
    webhook_page: str = ""
    webhook_steps: tuple[str, ...] = ()
    #: What the pasted secret is called on the provider's site ("token", "app password", "bot token").
    token_label: str = "token"
    #: Non-secret values given with the token: (name, label), e.g. a Mastodon server or a Bluesky handle.
    token_fields: tuple[tuple[str, str], ...] = ()
    #: The Authorization scheme ("Bearer"; "Bot" for Discord; "" when the token travels in the address, Telegram).
    auth_scheme: str = "Bearer"
    webhook_prefixes: tuple[str, ...] = ("https://hooks.slack.com/services/",)
    #: The token exchange authenticates the app with HTTP Basic (Reddit).
    token_basic_auth: bool = False
    #: Where the account sits in the window: Mail & calendar, Social, Messaging or Work.
    group: str = "Work"
    #: The sign-in card's title for a "token" provider when "Paste a token" is wrong (email: your normal login).
    token_title: str = ""
    #: The app registers itself with the owner's server at sign-in (Mastodon): browser sign-in needs no setup.
    dynamic_app: bool = False


PROVIDERS: dict[str, Provider] = {provider.key: provider for provider in (
    Provider("google", "Google", "Gmail, Google Calendar, Google Drive and your YouTube channel", ("browser",), group="Mail & calendar",
             scopes=("openid", "email", "profile", "https://www.googleapis.com/auth/gmail.readonly",
                     "https://www.googleapis.com/auth/gmail.send", "https://www.googleapis.com/auth/calendar.events",
                     "https://www.googleapis.com/auth/drive.readonly", "https://www.googleapis.com/auth/youtube.readonly"),
             authorize_url="https://accounts.google.com/o/oauth2/v2/auth", token_url="https://oauth2.googleapis.com/token",
             revoke_url="https://oauth2.googleapis.com/revoke", browser_needs_secret=True,
             app_page="https://console.cloud.google.com/apis/credentials",
             app_steps=("Open the Google Cloud console (button below) and create a project, e.g. \"HAYAT\".",
                        "APIs & Services → Library: enable the Gmail API, Google Calendar API, Google Drive API and "
                        "YouTube Data API v3.",
                        "OAuth consent screen: choose External, fill in the app name and your email, and add your own "
                        "address under Test users.",
                        "Credentials → Create credentials → OAuth client ID → Application type: Desktop app.",
                        "Copy the Client ID and Client secret into the boxes below and press Save app.",
                        "When you sign in, Google warns the app is unverified: it is your own app, so choose Continue. "
                        "While the consent screen is in Testing, Google ends the sign-in after 7 days; press Publish app on "
                        "the consent screen for a lasting one."),
             operations=(Operation("profile", "Who am I signed in as"),
                         Operation("gmail_inbox", "Latest Gmail messages", fields=(("query", "Search (optional, Gmail syntax)", False),)),
                         Operation("calendar_upcoming", "Upcoming calendar events"),
                         Operation("drive_recent", "Recently changed Drive files"),
                         Operation("youtube_channel", "My YouTube channel"),
                         Operation("youtube_uploads", "My latest YouTube uploads"),
                         Operation("gmail_send", "Send an email from Gmail", True,
                                   (("to", "To", True), ("subject", "Subject", True), ("body", "Message", True))),
                         Operation("calendar_create", "Add a calendar event", True,
                                   (("title", "Title", True), ("start", "Starts (e.g. 2026-10-14 15:00)", True),
                                    ("end", "Ends (e.g. 2026-10-14 16:00)", True))))),
    Provider("microsoft", "Microsoft", "Outlook mail, Outlook calendar and OneDrive (work, school or personal)",
             ("device", "browser"), group="Mail & calendar",
             scopes=("offline_access", "User.Read", "Mail.Read", "Mail.Send", "Calendars.ReadWrite", "Files.Read"),
             device_url="https://login.microsoftonline.com/common/oauth2/v2.0/devicecode",
             authorize_url="https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
             token_url="https://login.microsoftonline.com/common/oauth2/v2.0/token", loopback_host="localhost",
             app_page="https://entra.microsoft.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade",
             app_steps=("Open Microsoft Entra app registrations (button below) and choose New registration.",
                        "Name it \"HAYAT\"; Supported accounts: any organizational directory and personal Microsoft "
                        "accounts.",
                        "Redirect URI: platform \"Public client/native\", address http://localhost — then Register.",
                        "Authentication: set \"Allow public client flows\" to Yes and save.",
                        "API permissions → Microsoft Graph → Delegated: User.Read, Mail.Read, Mail.Send, "
                        "Calendars.ReadWrite, Files.Read, offline_access.",
                        "Copy the Application (client) ID into the box below and press Save app. No secret is needed."),
             operations=(Operation("profile", "Who am I signed in as"),
                         Operation("mail_inbox", "Latest Outlook messages"),
                         Operation("calendar_upcoming", "Upcoming calendar events"),
                         Operation("files_recent", "Recent OneDrive files"),
                         Operation("mail_send", "Send an email from Outlook", True,
                                   (("to", "To", True), ("subject", "Subject", True), ("body", "Message", True))),
                         Operation("event_create", "Add a calendar event", True,
                                   (("title", "Title", True), ("start", "Starts (e.g. 2026-10-14 15:00)", True),
                                    ("end", "Ends (e.g. 2026-10-14 16:00)", True))))),
    Provider("github", "GitHub", "Repositories, issues and notifications", ("device", "browser", "token"),
             scopes=("read:user", "repo", "notifications"),
             device_url="https://github.com/login/device/code", authorize_url="https://github.com/login/oauth/authorize",
             token_url="https://github.com/login/oauth/access_token", browser_needs_secret=True,
             app_page="https://github.com/settings/applications/new?oauth_application%5Bname%5D=HAYAT"
                      "&oauth_application%5Burl%5D=https%3A%2F%2Fgithub.com%2Fmohsinkhanshaik%2FHAYAT"
                      "&oauth_application%5Bcallback_url%5D=http%3A%2F%2F127.0.0.1%2F",
             app_steps=("Open GitHub's new OAuth app page (button below).",
                        "Application name \"HAYAT\"; Homepage URL http://127.0.0.1; Authorization callback URL "
                        "http://127.0.0.1/",
                        "Tick \"Enable Device Flow\" and press Register application.",
                        "Copy the Client ID into the box below. For browser sign-in also generate a client secret and "
                        "paste it; the code sign-in needs only the Client ID."),
             token_page="https://github.com/settings/personal-access-tokens/new",
             token_steps=("Press Make a token to open GitHub's fine-grained token page.",
                          "Give it a name and an expiry; choose the repositories it may read.",
                          "Repository permissions: Contents, Issues and Metadata (read; Issues read and write to create "
                          "issues). Account permissions: none needed.",
                          "Generate the token, copy it and paste it in the box above."),
             operations=(Operation("profile", "Who am I signed in as"),
                         Operation("repos", "My repositories, recently updated"),
                         Operation("notifications", "Unread notifications"),
                         Operation("issues", "Issues assigned to me"),
                         Operation("create_issue", "Open an issue", True,
                                   (("repo", "Repository (owner/name)", True), ("title", "Title", True), ("body", "Description", False))))),
    Provider("linkedin", "LinkedIn", "Your profile, and posting to your feed once you confirm", ("browser",), group="Social",
             scopes=("openid", "profile", "email", "w_member_social"),
             authorize_url="https://www.linkedin.com/oauth/v2/authorization", token_url="https://www.linkedin.com/oauth/v2/accessToken",
             browser_needs_secret=True, pkce=False, loopback_port=8796,
             app_page="https://www.linkedin.com/developers/apps/new",
             app_steps=("Open LinkedIn's new app page (button below). LinkedIn asks for a company Page to attach the app to: "
                        "pick yours, or create a small one for it.",
                        "Products tab: add \"Sign In with LinkedIn using OpenID Connect\" and \"Share on LinkedIn\".",
                        "Auth tab → Authorized redirect URLs: add exactly http://127.0.0.1:8796/",
                        "Copy the Client ID and Primary Client Secret into the boxes below and press Save app.",
                        "LinkedIn lets self-made apps read your own profile and post as you; it does not let them read your feed, "
                        "messages or connections. A LinkedIn sign-in lasts about 60 days."),
             operations=(Operation("profile", "Who am I signed in as"),
                         Operation("post", "Post to my LinkedIn feed", True, (("text", "Post", True),)))),
    Provider("notion", "Notion", "Pages and databases you share with the integration", ("token",),
             token_page="https://www.notion.so/profile/integrations",
             token_steps=("Press Make a token to open Notion's integrations page, and create a new internal integration.",
                          "Give it read content capability; copy the Internal Integration Secret.",
                          "In Notion, open each page HAYAT may read → ••• → Connections → add your integration.",
                          "Paste the secret in the box above."),
             operations=(Operation("profile", "Which integration is connected"),
                         Operation("search", "Search shared pages", fields=(("query", "Words to find", False),)))),
    Provider("slack", "Slack", "Channels and messages in your workspace", ("token", "webhook"), group="Messaging",
             webhook_page="https://api.slack.com/messaging/webhooks",
             webhook_steps=("Press Make a webhook: Slack's guide opens. Create an app in your workspace (From scratch).",
                            "Incoming Webhooks → switch On → Add New Webhook to Workspace → choose the channel → Allow.",
                            "Copy the Webhook URL (https://hooks.slack.com/services/…) and paste it in the box above.",
                            "A webhook can only post to that one channel; paste a bot token as well to read channels."),
             token_page="https://api.slack.com/apps",
             token_steps=("Press Make a token to open Slack's app page → Create New App → From scratch, in your workspace.",
                          "OAuth & Permissions → Bot Token Scopes: channels:read, channels:history, chat:write.",
                          "Install to Workspace, then copy the Bot User OAuth Token (starts with xoxb-).",
                          "Invite the bot to a channel (/invite @yourapp) before posting there, and paste the token in the box above."),
             operations=(Operation("profile", "Which workspace and bot"),
                         Operation("channels", "Channels"),
                         Operation("history", "Latest messages in a channel", fields=(("channel", "Channel (e.g. #general)", True),)),
                         Operation("post_message", "Post a message to a channel", True,
                                   (("channel", "Channel (e.g. #general or its ID)", True), ("text", "Message", True))))),
    *social_providers(Provider, Operation),
    mail_provider(Provider, Operation),
)}
#: The order the window shows the groups in.
GROUPS = ("Mail & calendar", "Social", "Messaging", "Work")


def builtin_apps(path: Path | None = None) -> dict[str, dict[str, str]]:
    """HAYAT's own app registrations, made once by the project (config/oauth_apps.yaml), and the owner's local ones
    (data/integrations/oauth-apps.json, which git ignores). With one, browser sign-in is a single click: no one has to
    register a developer app. Public and desktop client IDs are not secrets; a client secret only goes in the local
    file. An owner's own app (saved in the window) always wins over these."""
    import yaml

    from app.config import PROJECT_ROOT

    apps: dict[str, dict[str, str]] = {}
    for source in (PROJECT_ROOT / "config" / "oauth_apps.yaml", path or PROJECT_ROOT / "data" / "integrations" / "oauth-apps.json"):
        try:
            data = (json.loads(source.read_text(encoding="utf-8")) if source.suffix == ".json"
                    else yaml.safe_load(source.read_text(encoding="utf-8"))) if source.is_file() else {}
        except (OSError, ValueError, yaml.YAMLError):
            log_swallowed(_log, f"reading built-in app registrations from {source.name}")
            continue
        for key, value in (data or {}).items():
            if isinstance(value, dict) and str(value.get("client_id") or "").strip():
                apps.setdefault(str(key), {}).update({name: str(item).strip() for name, item in value.items() if item})
    return apps


def redirect_address(provider: Provider) -> str:
    """What to paste as the app's redirect (callback) address when registering it."""
    if "browser" not in provider.methods or provider.dynamic_app:
        return ""
    if provider.loopback_port:
        return f"http://{provider.loopback_host}:{provider.loopback_port}/"
    if provider.key == "google":
        return ""  # a Google "Desktop app" client takes any 127.0.0.1 address without listing one
    return f"http://{provider.loopback_host}" + ("" if provider.loopback_host == "localhost" else "/")


read_builtin_apps = builtin_apps  # the reader itself; tests replace builtin_apps so they never see the real files
METHOD_LABELS = {"device": "Sign in with a code", "browser": "Sign in with your browser", "token": "Paste a token",
                 "webhook": "Paste an incoming webhook"}


_PRIVATE_SUFFIXES = (".local", ".localhost", ".localdomain", ".lan", ".home", ".internal", ".intranet", ".corp",
                     ".private", ".test", ".invalid", ".example", ".arpa", ".nip.io", ".sslip.io", ".xip.io")


def public_server(host: str, resolve: Callable[[str], list[str]] | None = None) -> str:
    """A server name that reaches the public internet only: no local, private or link-local address.

    The OAuth providers are fixed addresses; a Mastodon server is typed by the owner, and "router.lan.home" or
    "169.254.169.254.nip.io" (a cloud metadata address) would send the token to this network instead.
    """
    import ipaddress
    import socket

    host = str(host or "").strip().lower().rstrip(".")
    if not host or host.endswith(_PRIVATE_SUFFIXES) or host in ("localhost",):
        raise ValueError(f"{host or 'That'} is not a public internet server.")
    try:
        ipaddress.ip_address(host)
        raise ValueError("Give the server's name, not a number address.")
    except ValueError as exc:
        if "server's name" in str(exc):
            raise
    if resolve is None:
        def resolve(name: str) -> list[str]:
            return [item[4][0] for item in socket.getaddrinfo(name, 443, proto=socket.IPPROTO_TCP)]
    try:
        addresses = resolve(host)
    except OSError:
        raise ValueError(f"{host} could not be found on the internet.") from None
    if not addresses or any(not ipaddress.ip_address(address.split("%")[0]).is_global for address in addresses):
        raise ValueError(f"{host} points to a private or local address, so it is not used.")
    return host


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    return verifier, challenge


def when(text: str) -> str:
    """'2026-10-14 15:00' (local time) → an ISO time with the local offset."""
    text = str(text or "").strip().replace("T", " ")
    for pattern in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            moment = datetime.strptime(text, pattern)
            return moment.astimezone().isoformat(timespec="seconds")
        except ValueError:
            continue
    raise ValueError(f"Give the time as 2026-10-14 15:00, not {text!r}.")


class AccountManager(SocialAccounts, MailAccounts):
    def __init__(self, broker: Any, path: Path | str, *, request: Callable[..., Any] | None = None,
                 open_browser: Callable[[str], Any] | None = None) -> None:
        self.broker = broker
        self.path = Path(path)
        self._request = request or httpx.request
        self.open_browser = open_browser or webbrowser.open
        self._lock = threading.RLock()
        self._devices: dict[str, dict[str, Any]] = {}
        self._browsers: dict[str, dict[str, Any]] = {}  # browser sign-ins waiting: listener and link

    # ------------------------------------------------------------------ local records (no secrets)
    def _records(self) -> dict[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_records(self, records: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(records, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    @staticmethod
    def provider(key: str) -> Provider:
        if key not in PROVIDERS:
            raise ValueError(f"HAYAT can connect {', '.join(item.name for item in PROVIDERS.values())}; not {key!r}.")
        return PROVIDERS[key]

    def _need_broker(self) -> None:
        if self.broker is None:
            raise RuntimeError("The secure credential store is not available in this session (safe mode), so no account can be "
                               "connected.")

    # ------------------------------------------------------------------ the owner's app (client ID)
    def set_app(self, key: str, client_id: str, client_secret: str = "") -> dict[str, Any]:
        self.provider(key)
        client_id = str(client_id or "").strip()
        if not 8 <= len(client_id) <= 200 or any(character.isspace() for character in client_id):
            raise ValueError("Paste the Client ID exactly as the provider shows it.")
        with self._lock:
            records = self._records()
            record = records.setdefault(key, {})
            record["client_id"] = client_id
            if client_secret.strip():
                self._need_broker()
                self.broker.store(f"account.{key}.client", client_secret.strip(), scopes=[f"account:{key}"])
                record["has_client_secret"] = True
            self._save_records(records)
        return self.status(key)

    def _builtin(self, key: str) -> dict[str, str]:
        if getattr(self, "_builtin_apps", None) is None:
            self._builtin_apps = builtin_apps(getattr(self, "builtin_apps_path", None))
        return self._builtin_apps.get(key) or {}

    def _client(self, key: str, *, need_secret: bool) -> tuple[str, str]:
        """The owner's own app when saved, otherwise HAYAT's built-in one."""
        record = self._records().get(key) or {}
        if not record.get("client_id"):
            builtin = self._builtin(key)
            if builtin.get("client_id") and (builtin.get("client_secret") or not need_secret):
                return builtin["client_id"], builtin.get("client_secret", "") if need_secret else ""
            raise ValueError(f"{self.provider(key).name} needs an app registration first: follow the one-time app setup.")
        secret = ""
        if need_secret:
            if not record.get("has_client_secret"):
                raise ValueError(f"Browser sign-in to {self.provider(key).name} needs the app's client secret too: paste it in "
                                 "the app setup.")
            secret = self._secret(f"account.{key}.client", key)
        return record["client_id"], secret

    def _refresh_secret(self, key: str) -> str:
        record = self._records().get(key) or {}
        if record.get("client_id"):
            return self._secret(f"account.{key}.client", key) if record.get("has_client_secret") else ""
        return self._builtin(key).get("client_secret", "")

    def _secret(self, name: str, key: str) -> str:
        self._need_broker()
        scope = f"account:{key}"
        return self.broker.redeem(self.broker.issue_lease(name, scope), scope)

    # ------------------------------------------------------------------ status
    def status(self, key: str = "") -> dict[str, Any]:
        records = self._records()
        rows = []
        for provider in PROVIDERS.values():
            if key and provider.key != key:
                continue
            record = records.get(provider.key) or {}
            builtin = self._builtin(provider.key)
            own = bool(record.get("client_id"))
            ready = {"device": own or bool(builtin.get("client_id")),
                     "browser": provider.dynamic_app or (own and bool(record.get("has_client_secret") or not provider.browser_needs_secret))
                     or (not own and bool(builtin.get("client_id")) and bool(builtin.get("client_secret") or not provider.browser_needs_secret)),
                     "token": True, "webhook": True}
            rows.append({"key": provider.key, "name": provider.name, "what": provider.what, "connected": bool(record.get("connected")),
                         "connected_as": record.get("connected_as", ""), "method": record.get("method", ""),
                         "connected_at": record.get("connected_at", ""), "client_id_set": bool(record.get("client_id")),
                         "builtin_app": bool(builtin.get("client_id")) and not own, "dynamic_app": provider.dynamic_app,
                         "redirect_address": redirect_address(provider),
                         # The last real check of the sign-in (check): when, whether it worked, and why not.
                         "checked_at": record.get("checked_at", ""), "check_ok": record.get("check_ok"),
                         "check_error": record.get("check_error", ""), "browser_waiting": provider.key in self._browsers,
                         "methods": [{"key": method, "label": METHOD_LABELS[method], "ready": ready[method]} for method in provider.methods],
                         "operations": [{"key": op.key, "label": op.label, "write": op.write,
                                         "fields": [{"name": name, "label": label, "required": required} for name, label, required in op.fields]}
                                        for op in provider.operations],
                         "needs_app": not provider.dynamic_app and not builtin.get("client_id")
                         and any(method in provider.methods for method in ("device", "browser")),
                         "app_page": provider.app_page, "app_steps": list(provider.app_steps), "token_page": provider.token_page,
                         "token_steps": list(provider.token_steps), "browser_needs_secret": provider.browser_needs_secret,
                         "webhook_page": provider.webhook_page, "webhook_steps": list(provider.webhook_steps),
                         "group": provider.group, "token_label": provider.token_label, "token_title": provider.token_title,
                         "token_fields": [{"name": name, "label": label, "value": (record.get("settings") or {}).get(name, "")}
                                          for name, label in provider.token_fields],
                         # Whether "post everywhere" can use it: its plain-text post operation exists.
                         "can_post": POST_OPERATIONS.get(provider.key) in {op.key for op in provider.operations if op.write}})
        result = {"accounts_view": True, "accounts": rows, "connected": [row["name"] for row in rows if row["connected"]],
                  "groups": list(GROUPS), "not_offered": list(NOT_OFFERED),
                  "basis": "You sign in on each provider's own page; HAYAT never sees your password. Tokens stay in Windows "
                           "Credential Manager. Anything that sends or changes something waits for your confirmation."}
        return rows[0] if key and rows else result

    # ------------------------------------------------------------------ tokens
    def _store_token(self, key: str, token: dict[str, Any], method: str) -> dict[str, Any]:
        self._need_broker()
        if not token.get("access_token"):
            raise ValueError(f"{self.provider(key).name} did not return a token.")
        expires_in = token.get("expires_in")
        value = {"access_token": token["access_token"], "refresh_token": token.get("refresh_token", ""), "method": method,
                 "token_type": token.get("token_type", "Bearer"), "scope": token.get("scope", ""),
                 "expires_at": (time.time() + float(expires_in)) if expires_in else None}
        value.update({name: token[name] for name in ("did", "handle") if token.get(name)})  # Bluesky's account and handle
        self.broker.store(f"account.{key}", json.dumps(value), scopes=[f"account:{key}"])
        try:
            who = self._who(key, value["access_token"])
        except Exception:
            self.broker.delete(f"account.{key}")
            raise
        with self._lock:
            records = self._records()
            record = records.setdefault(key, {})
            now = datetime.now(UTC).isoformat(timespec="seconds")
            # Reading the account just now is the first check, so the window can say "checked" straight away.
            record.update(connected=True, connected_as=who, method=method, connected_at=now, checked_at=now, check_ok=True,
                          check_error="")
            self._save_records(records)
        return self.status(key)

    def _token(self, key: str) -> str:
        """A valid access token: refreshed first when it is about to expire."""
        if not (self._records().get(key) or {}).get("connected"):
            raise PermissionError(f"{self.provider(key).name} is not connected. Open Accounts & sign-in to connect it.")
        value = json.loads(self._secret(f"account.{key}", key))
        if value.get("expires_at") and value["expires_at"] - time.time() < 90:
            if not value.get("refresh_token"):
                raise PermissionError(f"The {self.provider(key).name} sign-in has expired; sign in again.")
            if key == "bluesky":  # Bluesky renews its session with the refresh token itself, not an OAuth form
                value = self._bluesky_refresh(value)
                self.broker.store(f"account.{key}", json.dumps(value), scopes=[f"account:{key}"])
                return value["access_token"]
            provider = self.provider(key)
            client_id, _none = self._client(key, need_secret=False)
            # Google and GitHub want the app's secret on a refresh too; Microsoft's public client has none.
            client_secret = self._refresh_secret(key)
            form = {"grant_type": "refresh_token", "refresh_token": value["refresh_token"], "client_id": client_id}
            if client_secret:
                form["client_secret"] = client_secret
            fresh = self._json("POST", provider.token_url, data=form, headers=self._token_headers(provider, client_id, client_secret))
            if not fresh.get("access_token"):
                raise PermissionError(f"{provider.name} refused to refresh the sign-in ({fresh.get('error', 'no token')}); sign in again.")
            value.update(access_token=fresh["access_token"], refresh_token=fresh.get("refresh_token") or value["refresh_token"],
                         expires_at=time.time() + float(fresh.get("expires_in") or 3600))
            self.broker.store(f"account.{key}", json.dumps(value), scopes=[f"account:{key}"])
        return value["access_token"]

    def _call(self, method: str, url: str, **kwargs: Any) -> Any:
        """One request. A failure names only the host: Telegram's token and webhook keys live in the address, and
        network errors quote the address they failed on (a ConnectError carried the whole bot token)."""
        try:
            return self._request(method, url, timeout=30, follow_redirects=False, **kwargs)
        except httpx.HTTPError as exc:
            raise RuntimeError(f"{urlsplit(url).netloc} could not be reached ({type(exc).__name__})") from None

    def _json(self, method: str, url: str, **kwargs: Any) -> Any:
        if urlsplit(url).scheme != "https":
            raise ValueError("Accounts are reached over HTTPS only.")
        response = self._call(method, url, **kwargs)
        if getattr(response, "status_code", 200) >= 400 and not url.endswith(("/token", "/access_token", "/devicecode", "/device/code")):
            detail = ""
            try:
                body = response.json()
                detail = str((body.get("error") or {}).get("message") if isinstance(body.get("error"), dict) else body.get("error")
                             or body.get("message") or "")[:200]
            except (ValueError, AttributeError):
                pass
            raise RuntimeError(f"{urlsplit(url).netloc} answered {response.status_code}" + (f": {detail}" if detail else ""))
        if not getattr(response, "content", b"x"):
            return {}
        try:
            return response.json()
        except ValueError:
            raise RuntimeError(f"{urlsplit(url).netloc} answered with something that is not JSON "
                               f"(HTTP {getattr(response, 'status_code', '?')})") from None

    @staticmethod
    def _token_headers(provider: Provider, client_id: str, client_secret: str) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if provider.token_basic_auth:  # Reddit: the app authenticates with HTTP Basic (an installed app has no secret)
            pair = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode("ascii")
            headers.update({"Authorization": f"Basic {pair}", "User-Agent": "windows:hayat-assistant:1.0 (personal assistant)"})
        return headers

    def _api(self, key: str, method: str, url: str, *, token: str = "", **kwargs: Any) -> Any:
        scheme = self.provider(key).auth_scheme
        headers = {"Accept": "application/json", "User-Agent": "HAYAT-Accounts/1.0", **kwargs.pop("headers", {})}
        if scheme:
            headers["Authorization"] = f"{scheme} {token or self._token(key)}"
        if key == "notion":
            headers["Notion-Version"] = "2022-06-28"
        if key == "github":
            headers["Accept"] = "application/vnd.github+json"
        return self._json(method, url, headers=headers, **kwargs)

    def _who(self, key: str, token: str) -> str:
        """The account a token belongs to, read once to check it works."""
        if key == "mail":
            return self._mail_who()
        social = self._who_social(key, token)
        if social:
            return social
        if key == "google":
            data = self._api(key, "GET", "https://openidconnect.googleapis.com/v1/userinfo", token=token)
            return data.get("email") or data.get("name") or "Google account"
        if key == "microsoft":
            data = self._api(key, "GET", "https://graph.microsoft.com/v1.0/me?$select=displayName,mail,userPrincipalName", token=token)
            return data.get("mail") or data.get("userPrincipalName") or data.get("displayName") or "Microsoft account"
        if key == "github":
            data = self._api(key, "GET", "https://api.github.com/user", token=token)
            return data.get("login") or "GitHub account"
        if key == "linkedin":
            data = self._api(key, "GET", "https://api.linkedin.com/v2/userinfo", token=token)
            return data.get("name") or data.get("email") or "LinkedIn member"
        if key == "notion":
            data = self._api(key, "GET", "https://api.notion.com/v1/users/me", token=token)
            return data.get("name") or (data.get("bot") or {}).get("workspace_name") or "Notion integration"
        data = self._api(key, "POST", "https://slack.com/api/auth.test", token=token)
        if not data.get("ok"):
            raise PermissionError(f"Slack refused the token ({data.get('error', 'unknown error')}).")
        return f"{data.get('user', 'bot')} in {data.get('team', 'a workspace')}"

    def check(self, key: str) -> dict[str, Any]:
        """Confirm a saved sign-in really works: one read of whose account it is, recorded with the time.

        "Connected" used to be only a note written at sign-in. A sign-in missing from Credential Manager, revoked
        on the provider's site or expired still showed as connected, and nothing confirmed it.
        """
        provider = self.provider(key)
        record = self._records().get(key) or {}
        if not record.get("connected"):
            raise PermissionError(f"{provider.name} is not connected yet.")
        who, problem, lost = record.get("connected_as", ""), "", False
        stored = {row["name"] for row in self.broker.list()} if self.broker is not None else set()
        if f"account.{key}" not in stored:
            lost, problem = True, "the saved sign-in is missing from Windows Credential Manager; connect again"
        elif record.get("method") != "webhook":  # a webhook can only be tried by posting, so it is kept as saved
            try:
                who = self._who(key, self._token(key)) or who
            except Exception as exc:  # errors from _call name only the host, never a token
                problem = str(exc).strip().rstrip(".")[:240] or type(exc).__name__
        with self._lock:
            records = self._records()
            record = records.setdefault(key, {})
            record.update(checked_at=datetime.now(UTC).isoformat(timespec="seconds"), check_ok=not problem,
                          check_error=problem)
            if lost:
                record.update(connected=False, connected_as="")
            elif not problem:
                record["connected_as"] = who
            self._save_records(records)
        return self.status(key)

    def check_all(self, key: str = "") -> dict[str, Any]:
        """Check one connected account, or every connected one, and report each result."""
        keys = [key] if key else [row["key"] for row in self.status()["accounts"] if row["connected"]]
        results = []
        for name in keys:
            try:
                results.append(self.check(name))
            except PermissionError:
                results.append(self.status(name))
        return {"check_view": True, "results": results}

    # ------------------------------------------------------------------ way 1: a code
    def start_device(self, key: str) -> dict[str, Any]:
        provider = self.provider(key)
        if "device" not in provider.methods:
            raise ValueError(f"{provider.name} does not offer sign-in with a code; use {', '.join(METHOD_LABELS[m] for m in provider.methods)}.")
        self._need_broker()
        client_id, _secret = self._client(key, need_secret=False)
        data = self._json("POST", provider.device_url, data={"client_id": client_id, "scope": " ".join(provider.scopes)},
                          headers={"Accept": "application/json"})
        if not data.get("device_code"):
            reason = str(data.get("error_description") or data.get("error") or data.get("message") or "no code")
            hint = ""
            if reason.lower() in ("not found", "unauthorized_client", "invalid_client") or "not a valid application" in reason:
                hint = (" Check the Client ID was copied exactly" + (" and that \"Enable Device Flow\" is ticked in the app"
                                                                     if key == "github" else " and that public client flows are allowed")
                        + ".")
            raise RuntimeError(f"{provider.name} did not start the sign-in: {reason.split(' Trace ID')[0]}.{hint}")
        with self._lock:
            self._devices[key] = {"device_code": data["device_code"], "interval": int(data.get("interval") or 5),
                                  "expires": time.monotonic() + int(data.get("expires_in") or 900), "client_id": client_id}
        return {"provider": key, "user_code": data["user_code"],
                "verification_uri": data.get("verification_uri") or data.get("verification_url"),
                "interval": int(data.get("interval") or 5), "expires_in": int(data.get("expires_in") or 900),
                "message": f"Open {data.get('verification_uri') or data.get('verification_url')} and enter {data['user_code']}, "
                           f"then approve HAYAT."}

    def poll_device(self, key: str) -> dict[str, Any]:
        """One check: 'waiting', 'slow_down', 'connected', or an error that ends the sign-in."""
        provider = self.provider(key)
        with self._lock:
            pending = self._devices.get(key)
        if not pending:
            raise ValueError("No sign-in with a code is in progress; start it again.")
        if time.monotonic() > pending["expires"]:
            self._devices.pop(key, None)
            raise TimeoutError("The code expired before it was approved; start again.")
        data = self._json("POST", provider.token_url, headers={"Accept": "application/json"},
                          data={"grant_type": "urn:ietf:params:oauth:grant-type:device_code", "device_code": pending["device_code"],
                                "client_id": pending["client_id"]})
        error = data.get("error")
        if error in ("authorization_pending", "slow_down"):
            if error == "slow_down":
                pending["interval"] += 5
            return {"state": "waiting", "interval": pending["interval"]}
        self._devices.pop(key, None)
        if error:
            raise PermissionError({"access_denied": "You declined the sign-in.", "expired_token": "The code expired; start again."}
                                  .get(error, f"{provider.name} ended the sign-in: {data.get('error_description') or error}."))
        return {"state": "connected", **self._store_token(key, {**data, "method": "device"}, "device")}

    def cancel_device(self, key: str) -> None:
        with self._lock:
            self._devices.pop(key, None)

    # ------------------------------------------------------------------ way 2: the browser
    def sign_in_browser(self, key: str, timeout: float = SIGN_IN_SECONDS, server: str = "") -> dict[str, Any]:
        """Open the provider's page and wait (up to five minutes) for it to come back to 127.0.0.1.

        While it waits, `browser_link` gives the sign-in address to open in any browser tab, `finish_browser_from_address`
        takes the address a tab ended on (when the tab cannot reach this computer), and `cancel_browser` stops.
        """
        provider = self.provider(key)
        if "browser" not in provider.methods:
            raise ValueError(f"{provider.name} does not offer browser sign-in here; use {', '.join(METHOD_LABELS[m] for m in provider.methods)}.")
        self._need_broker()
        if provider.dynamic_app:
            server = self._sign_in_server(key, server)
        else:
            client_id, client_secret = self._client(key, need_secret=provider.browser_needs_secret)
        authorize_url, token_url = provider.authorize_url, provider.token_url
        scopes = " ".join(provider.scopes)
        verifier, challenge = _pkce()
        state = secrets.token_urlsafe(24)
        try:
            listener = Loopback(state, provider.loopback_port)
        except OSError as exc:
            raise RuntimeError(f"Port {provider.loopback_port} on this computer is busy, and {provider.name} only returns to that exact "
                               "address. Close what is using it and try again.") from exc
        try:
            redirect = f"http://{provider.loopback_host}:{listener.port}/"
            if provider.dynamic_app:
                client_id, client_secret, authorize_url, token_url, scopes = self._register_dynamic_app(key, server, redirect)
            query = {"client_id": client_id, "response_type": "code", "redirect_uri": redirect, "scope": scopes,
                     "state": state, **provider.extra_authorize}
            if provider.pkce:
                query.update(code_challenge=challenge, code_challenge_method="S256")
            if key == "google":
                query.update(access_type="offline", prompt="consent")
            link = authorize_url + "?" + urlencode(query, quote_via=quote)
            with self._lock:
                self._browsers[key] = {"listener": listener, "link": link}
            try:
                self.open_browser(link)
            except Exception:  # no default browser: the window offers the link to open in any tab
                log_swallowed(_log, "opening the sign-in page in the default browser")
            code = listener.wait(timeout)
            try:
                form = {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect, "client_id": client_id}
                if provider.pkce:
                    form["code_verifier"] = verifier
                if client_secret:
                    form["client_secret"] = client_secret
                if provider.dynamic_app:
                    form["scope"] = scopes
                token = self._json("POST", token_url, data=form,
                                   headers=self._token_headers(provider, client_id, client_secret))
                if not token.get("access_token"):
                    raise PermissionError(f"{provider.name} did not issue a token: "
                                          f"{token.get('error_description') or token.get('error') or 'unknown'}.")
                result = self._store_token(key, token, "browser")
            except Exception as exc:
                listener.finish(False, f"HAYAT could not finish connecting {provider.name}: {exc}")
                raise
            listener.finish(True, f"HAYAT is connected to {provider.name} as {result['connected_as']}. You can close this tab.")
            return result
        finally:
            with self._lock:
                self._browsers.pop(key, None)
            listener.close()

    def _sign_in_server(self, key: str, server: str) -> str:
        """The owner's server for a self-registering provider (Mastodon), checked and kept with the account."""
        if not server:
            try:
                server = self._setting(key, "server")  # the server used last time
            except PermissionError:
                server = ""
        value = str(server or "")
        value = re.sub(r"^https?://", "", value).strip().strip("/").lower()
        if not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", value):
            raise ValueError("Type your server's name first, such as mastodon.social.")
        public_server(value)
        with self._lock:
            records = self._records()
            records.setdefault(key, {}).setdefault("settings", {})["server"] = value
            self._save_records(records)
        return value

    def _register_dynamic_app(self, key: str, server: str, redirect: str) -> tuple[str, str, str, str, str]:
        """Mastodon lets any app register itself with a server (POST /api/v1/apps): no developer account, no setup."""
        from app.identity import PROJECT_URL

        scopes = "read write:statuses"
        app = self._json("POST", f"https://{server}/api/v1/apps",
                         data={"client_name": "HAYAT", "redirect_uris": redirect, "scopes": scopes, "website": PROJECT_URL})
        if not app.get("client_id") or not app.get("client_secret"):
            raise RuntimeError(f"{server} did not register HAYAT for sign-in ({app.get('error') or 'no app came back'}).")
        return (app["client_id"], app["client_secret"], f"https://{server}/oauth/authorize", f"https://{server}/oauth/token",
                scopes)

    def browser_link(self, key: str) -> str:
        """The address of a browser sign-in in progress, to open in any browser tab (or another browser)."""
        with self._lock:
            pending = self._browsers.get(key)
        return pending["link"] if pending else ""

    def finish_browser_from_address(self, key: str, address: str) -> bool:
        """When a tab cannot come back to this computer, the address it ended on carries the code: take it from there."""
        with self._lock:
            pending = self._browsers.get(key)
        if not pending:
            raise ValueError(f"No {self.provider(key).name} browser sign-in is waiting; start it again.")
        return pending["listener"].feed(address, pasted=True)

    def cancel_browser(self, key: str) -> None:
        with self._lock:
            pending = self._browsers.get(key)
        if pending:
            pending["listener"].cancel()

    # ------------------------------------------------------------------ way 3: a token
    def save_token(self, key: str, token: str, extras: dict[str, str] | None = None) -> dict[str, Any]:
        provider = self.provider(key)
        if "token" not in provider.methods:
            raise ValueError(f"{provider.name} is connected by signing in, not with a pasted token.")
        if key == "mail":  # a password, not a token: it may be short, and app passwords come in groups of four
            raw = str(token or "").strip()
            # Google shows app passwords as "abcd efgh ijkl mnop"; any other password is kept exactly as typed.
            password = raw.replace(" ", "") if re.fullmatch(r"(?:[A-Za-z]{4} ){3}[A-Za-z]{4}", raw) else raw
            if not password:
                raise ValueError("Type your email password (or the app password your provider asks for).")
            return self._mail_login(str((extras or {}).get("address") or ""), password)
        token = str(token or "").strip()
        if len(token) < 10 or any(character.isspace() for character in token):
            raise ValueError(f"Paste the whole {provider.token_label}, without spaces.")
        settings = self._token_settings(provider, extras or {})
        if key == "telegram" and not re.fullmatch(r"\d{5,}:[A-Za-z0-9_-]{30,}", token):
            raise ValueError("A Telegram bot token looks like 123456789:AA… (numbers, a colon, then letters).")
        if settings:
            with self._lock:
                records = self._records()
                records.setdefault(key, {})["settings"] = settings
                self._save_records(records)
        if key == "bluesky":  # the app password opens a session; only the session is kept
            return self._store_token(key, self._bluesky_session(settings["handle"], token), "token")
        return self._store_token(key, {"access_token": token}, "token")

    @staticmethod
    def _token_settings(provider: Provider, extras: dict[str, str]) -> dict[str, str]:
        """The values given with a token, checked: a Mastodon server name, a Bluesky handle, a Facebook Page ID."""
        settings = {}
        for name, label in provider.token_fields:
            value = str(extras.get(name) or "").strip()
            if name == "server":
                value = re.sub(r"^https?://", "", value).strip("/").lower()
                if not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", value):
                    raise ValueError("Give your Mastodon server's name, such as mastodon.social.")
                public_server(value)
            elif name == "handle":
                value = value.lstrip("@").lower()
                if not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", value):
                    raise ValueError("Give your Bluesky handle, such as name.bsky.social.")
            elif name == "page":
                if not value.isdigit():
                    raise ValueError("The Page ID is a number (Page → About → Page transparency).")
            if not value:
                raise ValueError(f"Fill in: {label}.")
            settings[name] = value
        return settings

    def save_webhook(self, key: str, url: str) -> dict[str, Any]:
        """A Slack incoming webhook: posts to one channel, reads nothing. Kept like a token."""
        provider = self.provider(key)
        if "webhook" not in provider.methods:
            raise ValueError(f"{provider.name} is not connected with a webhook.")
        url = str(url or "").strip()
        if not url.startswith(provider.webhook_prefixes) or len(url) > 300 or any(character.isspace() for character in url):
            raise ValueError(f"Paste the whole Webhook URL; it starts with {provider.webhook_prefixes[0]}.")
        self._need_broker()
        self.broker.store(f"account.{key}", json.dumps({"webhook": url, "method": "webhook", "access_token": ""}), scopes=[f"account:{key}"])
        with self._lock:
            records = self._records()
            record = records.setdefault(key, {})
            record.update(connected=True, connected_as="a channel webhook (posting only)", method="webhook",
                          connected_at=datetime.now(UTC).isoformat(timespec="seconds"))
            self._save_records(records)
        return self.status(key)

    # ------------------------------------------------------------------ sign out
    def sign_out(self, key: str) -> dict[str, Any]:
        provider = self.provider(key)
        if provider.revoke_url and self.broker is not None:
            try:
                token = json.loads(self._secret(f"account.{key}", key))
                self._request("POST", provider.revoke_url, data={"token": token.get("refresh_token") or token["access_token"]},
                              timeout=15, follow_redirects=False)
            except Exception:  # the local copy is deleted either way
                log_swallowed(_log, "revoking an account token")
        if self.broker is not None:
            self.broker.delete(f"account.{key}")
        with self._lock:
            records = self._records()
            record = records.get(key) or {}
            for name in ("connected", "connected_as", "method", "connected_at", "checked_at", "check_ok", "check_error"):
                record.pop(name, None)
            records[key] = record
            self._save_records(records)
        return self.status(key)

    # ------------------------------------------------------------------ working with an account
    def _operation(self, key: str, name: str) -> Operation:
        for operation in self.provider(key).operations:
            if operation.key == name:
                return operation
        raise ValueError(f"{self.provider(key).name} has no operation {name!r}.")

    def read(self, key: str, operation: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Run a read-only operation and return plain rows."""
        op = self._operation(key, operation)
        if op.write:
            raise PermissionError("That operation sends or changes something: prepare it for your confirmation instead.")
        if not (self._records().get(key) or {}).get("connected"):
            # Before any provider read: one that needs a saved page or server would otherwise blame the setting.
            raise PermissionError(f"{self.provider(key).name} is not connected. Open Accounts & sign-in to connect it.")
        try:
            rows = getattr(self, f"_read_{key}")(operation, dict(params or {}))
        except (AttributeError, TypeError, KeyError, IndexError) as exc:
            # A reply in a shape the provider's documentation does not promise: said plainly, not a traceback.
            raise RuntimeError(f"{self.provider(key).name} answered in an unexpected shape ({type(exc).__name__}); "
                               "try again later.") from None
        return {"accounts_view": True, "read_view": True, "provider": key, "name": self.provider(key).name, "operation": operation,
                "label": op.label, "rows": rows, "fetched_at": datetime.now(UTC).isoformat(timespec="seconds")}

    def prepare(self, key: str, operation: str, params: dict[str, Any]) -> tuple[str, list[tuple[str, str]], Callable[[], dict]]:
        """(title, every value that will be sent, the one-shot executor) for a write. Nothing is sent here."""
        op = self._operation(key, operation)
        if not op.write:
            raise ValueError("That operation only reads; run it directly.")
        params = {name: str(params.get(name) or "").strip() for name, _label, _required in op.fields}
        missing = [label for name, label, required in op.fields if required and not params[name]]
        if missing:
            raise ValueError("Fill in: " + ", ".join(missing) + ".")
        if not (self._records().get(key) or {}).get("connected"):
            raise PermissionError(f"{self.provider(key).name} is not connected. Open Accounts & sign-in to connect it.")
        who =self._records()[key].get("connected_as", "")
        details = [("Account", f"{self.provider(key).name} · {who}")] + [(label, params[name]) for name, label, _required in op.fields]
        def run() -> dict[str, Any]:
            try:
                return getattr(self, f"_write_{key}")(operation, params)
            except (AttributeError, TypeError, KeyError, IndexError) as exc:
                raise RuntimeError(f"{self.provider(key).name} answered in an unexpected shape ({type(exc).__name__}); "
                                   "check on the site whether it went through.") from None
        return f"{op.label} ({self.provider(key).name})", details, run

    # ---- Google
    def _read_google(self, operation: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        if operation.startswith("youtube_"):
            return self._read_youtube(operation, params)
        if operation == "profile":
            data = self._api("google", "GET", "https://openidconnect.googleapis.com/v1/userinfo")
            return [{"title": data.get("name", ""), "detail": data.get("email", "")}]
        if operation == "gmail_inbox":
            listing = self._api("google", "GET", "https://gmail.googleapis.com/gmail/v1/users/me/messages",
                                params={"maxResults": 10, "q": params.get("query") or "in:inbox"})
            rows = []
            for item in (listing.get("messages") or [])[:10]:
                message = self._api("google", "GET", f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{item['id']}",
                                    params={"format": "metadata", "metadataHeaders": ["Subject", "From", "Date"]})
                headers = {header["name"]: header["value"] for header in (message.get("payload") or {}).get("headers") or []}
                rows.append({"title": headers.get("Subject") or "(no subject)", "detail": headers.get("From", ""),
                             "when": headers.get("Date", ""), "extra": message.get("snippet", "")[:200],
                             "unread": "UNREAD" in (message.get("labelIds") or [])})
            return rows
        if operation == "calendar_upcoming":
            data = self._api("google", "GET", "https://www.googleapis.com/calendar/v3/calendars/primary/events",
                             params={"timeMin": datetime.now(UTC).isoformat(), "singleEvents": "true", "orderBy": "startTime",
                                     "maxResults": 10})
            return [{"title": item.get("summary") or "(no title)", "when": (item.get("start") or {}).get("dateTime")
                     or (item.get("start") or {}).get("date", ""), "detail": item.get("location", "")} for item in data.get("items") or []]
        data = self._api("google", "GET", "https://www.googleapis.com/drive/v3/files",
                         params={"orderBy": "modifiedTime desc", "pageSize": 10, "fields": "files(name,mimeType,modifiedTime,webViewLink)"})
        return [{"title": item.get("name", ""), "when": item.get("modifiedTime", ""), "detail": item.get("mimeType", "").split(".")[-1],
                 "link": item.get("webViewLink", "")} for item in data.get("files") or []]

    def _write_google(self, operation: str, params: dict[str, str]) -> dict[str, Any]:
        if operation == "gmail_send":
            message = EmailMessage()
            message["To"], message["Subject"] = params["to"], params["subject"]
            message.set_content(params["body"])
            raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
            sent = self._api("google", "POST", "https://gmail.googleapis.com/gmail/v1/users/me/messages/send", json={"raw": raw})
            return {"sent": True, "id": sent.get("id"), "where": "Gmail · Sent"}
        event = self._api("google", "POST", "https://www.googleapis.com/calendar/v3/calendars/primary/events",
                          json={"summary": params["title"], "start": {"dateTime": when(params["start"])},
                                "end": {"dateTime": when(params["end"])}})
        return {"created": True, "id": event.get("id"), "url": event.get("htmlLink", "")}

    # ---- Microsoft
    def _read_microsoft(self, operation: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        graph = "https://graph.microsoft.com/v1.0"
        if operation == "profile":
            data = self._api("microsoft", "GET", graph + "/me")
            return [{"title": data.get("displayName", ""), "detail": data.get("mail") or data.get("userPrincipalName", "")}]
        if operation == "mail_inbox":
            data = self._api("microsoft", "GET", graph + "/me/mailFolders/inbox/messages",
                             params={"$top": 10, "$select": "subject,from,receivedDateTime,isRead,bodyPreview"})
            return [{"title": item.get("subject") or "(no subject)", "detail": ((item.get("from") or {}).get("emailAddress") or {}).get("address", ""),
                     "when": item.get("receivedDateTime", ""), "extra": item.get("bodyPreview", "")[:200], "unread": not item.get("isRead")}
                    for item in data.get("value") or []]
        if operation == "calendar_upcoming":
            start = datetime.now(UTC)
            data = self._api("microsoft", "GET", graph + "/me/calendarView",
                             params={"startDateTime": start.isoformat(), "endDateTime": (start + timedelta(days=30)).isoformat(),
                                     "$top": 10, "$orderby": "start/dateTime", "$select": "subject,start,location"})
            return [{"title": item.get("subject") or "(no title)", "when": (item.get("start") or {}).get("dateTime", ""),
                     "detail": (item.get("location") or {}).get("displayName", "")} for item in data.get("value") or []]
        data = self._api("microsoft", "GET", graph + "/me/drive/recent", params={"$top": 10})
        return [{"title": item.get("name", ""), "when": item.get("lastModifiedDateTime", ""), "link": item.get("webUrl", ""),
                 "detail": "folder" if item.get("folder") else "file"} for item in data.get("value") or []]

    def _write_microsoft(self, operation: str, params: dict[str, str]) -> dict[str, Any]:
        graph = "https://graph.microsoft.com/v1.0"
        if operation == "mail_send":
            self._api("microsoft", "POST", graph + "/me/sendMail", json={"message": {
                "subject": params["subject"], "body": {"contentType": "Text", "content": params["body"]},
                "toRecipients": [{"emailAddress": {"address": address.strip()}} for address in params["to"].split(",") if address.strip()]},
                "saveToSentItems": True})
            return {"sent": True, "where": "Outlook · Sent Items"}
        event = self._api("microsoft", "POST", graph + "/me/events", json={
            "subject": params["title"], "start": {"dateTime": utc(params["start"]), "timeZone": "UTC"},
            "end": {"dateTime": utc(params["end"]), "timeZone": "UTC"}})
        return {"created": True, "id": event.get("id"), "url": event.get("webLink", "")}

    # ---- GitHub
    def _read_github(self, operation: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        api = "https://api.github.com"
        if operation == "profile":
            data = self._api("github", "GET", api + "/user")
            return [{"title": data.get("login", ""), "detail": data.get("name") or "", "extra": f"{data.get('public_repos', 0)} public repositories",
                     "link": data.get("html_url", "")}]
        if operation == "repos":
            data = self._api("github", "GET", api + "/user/repos", params={"sort": "updated", "per_page": 15})
            return [{"title": item["full_name"], "when": item.get("updated_at", ""), "detail": ("private" if item.get("private") else "public")
                     + (f" · {item['language']}" if item.get("language") else ""), "extra": item.get("description") or "",
                     "link": item.get("html_url", "")} for item in data]
        if operation == "notifications":
            data = self._api("github", "GET", api + "/notifications", params={"per_page": 20})
            return [{"title": (item.get("subject") or {}).get("title", ""), "detail": (item.get("repository") or {}).get("full_name", ""),
                     "when": item.get("updated_at", ""), "extra": item.get("reason", ""), "unread": item.get("unread", False)} for item in data]
        data = self._api("github", "GET", api + "/issues", params={"filter": "assigned", "state": "open", "per_page": 20})
        return [{"title": item.get("title", ""), "detail": (item.get("repository") or {}).get("full_name", ""), "when": item.get("updated_at", ""),
                 "link": item.get("html_url", "")} for item in data]

    def _write_github(self, operation: str, params: dict[str, str]) -> dict[str, Any]:
        repo = params["repo"].strip().strip("/")
        if repo.count("/") != 1:
            raise ValueError("Give the repository as owner/name.")
        issue = self._api("github", "POST", f"https://api.github.com/repos/{repo}/issues", json={"title": params["title"], "body": params.get("body", "")})
        return {"created": True, "number": issue.get("number"), "url": issue.get("html_url", "")}

    # ---- LinkedIn
    def _read_linkedin(self, operation: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        data = self._api("linkedin", "GET", "https://api.linkedin.com/v2/userinfo")
        return [{"title": data.get("name", ""), "detail": data.get("email", ""), "extra": data.get("locale", {}).get("country", "")
                 if isinstance(data.get("locale"), dict) else ""}]

    def _write_linkedin(self, operation: str, params: dict[str, str]) -> dict[str, Any]:
        member = self._api("linkedin", "GET", "https://api.linkedin.com/v2/userinfo").get("sub")
        if not member:
            raise RuntimeError("LinkedIn did not say which member is signed in; sign in again.")
        post = {"author": f"urn:li:person:{member}", "lifecycleState": "PUBLISHED",
                "specificContent": {"com.linkedin.ugc.ShareContent": {"shareCommentary": {"text": params["text"]},
                                                                      "shareMediaCategory": "NONE"}},
                "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"}}
        made = self._api("linkedin", "POST", "https://api.linkedin.com/v2/ugcPosts", json=post,
                         headers={"X-Restli-Protocol-Version": "2.0.0"})
        return {"posted": True, "id": made.get("id", ""), "where": "your LinkedIn feed (public)"}

    # ---- Notion
    def _read_notion(self, operation: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        if operation == "profile":
            data = self._api("notion", "GET", "https://api.notion.com/v1/users/me")
            return [{"title": data.get("name", ""), "detail": (data.get("bot") or {}).get("workspace_name", "")}]
        data = self._api("notion", "POST", "https://api.notion.com/v1/search", json={"query": params.get("query", ""), "page_size": 15})
        rows = []
        for item in data.get("results") or []:
            title = ""
            for value in (item.get("properties") or {}).values():
                if value.get("type") == "title":
                    title = "".join(part.get("plain_text", "") for part in value.get("title") or [])
            title = title or "".join(part.get("plain_text", "") for part in item.get("title") or [])
            rows.append({"title": title or "(untitled)", "detail": item.get("object", ""), "when": item.get("last_edited_time", ""),
                         "link": item.get("url", "")})
        return rows

    # ---- Slack
    def _read_slack(self, operation: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        if (self._records().get("slack") or {}).get("method") == "webhook":
            raise PermissionError("Slack is connected with a webhook, which can only post. Paste a bot token to read channels.")
        if operation == "history":
            channel = str(params.get("channel") or "").strip()
            if channel.startswith("#"):
                listing = self._api("slack", "GET", "https://slack.com/api/conversations.list", params={"limit": 200, "types": "public_channel"})
                channel = next((item["id"] for item in listing.get("channels") or [] if item.get("name") == channel[1:]), channel)
            data = self._api("slack", "GET", "https://slack.com/api/conversations.history", params={"channel": channel, "limit": 15})
            if not data.get("ok"):
                raise RuntimeError(f"Slack: {data.get('error', 'request refused')}" + (" (invite the bot to that channel first)"
                                                                                      if data.get("error") == "not_in_channel" else ""))
            return [{"title": str(item.get("text") or "")[:160] or "(attachment)", "detail": item.get("user") or item.get("username") or "",
                     "when": datetime.fromtimestamp(float(item.get("ts", 0)), UTC).isoformat(timespec="minutes")} for item in data.get("messages") or []]
        if operation == "profile":
            data = self._api("slack", "POST", "https://slack.com/api/auth.test")
            return [{"title": data.get("team", ""), "detail": data.get("user", ""), "link": data.get("url", "")}]
        data = self._api("slack", "GET", "https://slack.com/api/conversations.list", params={"limit": 50, "types": "public_channel"})
        if not data.get("ok"):
            raise RuntimeError(f"Slack: {data.get('error', 'request refused')}")
        return [{"title": "#" + item.get("name", ""), "detail": f"{item.get('num_members', 0)} members"
                 + (" · bot is a member" if item.get("is_member") else ""), "extra": (item.get("purpose") or {}).get("value", "")}
                for item in data.get("channels") or []]

    def _write_slack(self, operation: str, params: dict[str, str]) -> dict[str, Any]:
        if (self._records().get("slack") or {}).get("method") == "webhook":
            hook = json.loads(self._secret("account.slack", "slack"))["webhook"]
            response = self._call("POST", hook, json={"text": params["text"]})
            if getattr(response, "status_code", 200) >= 400:
                raise RuntimeError(f"Slack did not post it (HTTP {response.status_code}).")
            return {"sent": True, "where": "the webhook's channel"}
        data = self._api("slack", "POST", "https://slack.com/api/chat.postMessage",
                         json={"channel": params["channel"].lstrip("#") if params["channel"].startswith("#") else params["channel"],
                               "text": params["text"]})
        if not data.get("ok"):
            raise RuntimeError(f"Slack did not post it: {data.get('error', 'unknown error')}")
        return {"sent": True, "channel": data.get("channel"), "ts": data.get("ts")}


def utc(text: str) -> str:
    """A local time as UTC without an offset, the form Outlook takes with timeZone UTC."""
    return datetime.fromisoformat(when(text)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S")


class Loopback:
    """A one-time listener on 127.0.0.1 (and ::1 when it is free) for a sign-in's redirect.

    The tab is answered only once HAYAT has exchanged the code and read the account (`finish`). It used to say
    "HAYAT is connected" the moment the provider sent it back, before the exchange, which could still fail: the
    tab claimed success while HAYAT stayed unconnected. If the tab cannot reach this computer at all, the
    address it ended on can be pasted into HAYAT instead (`feed`).
    """

    def __init__(self, state: str, port: int = 0) -> None:
        self.state, self.code, self.error = state, None, None
        self.done = threading.Event()
        self.finished = threading.Event()
        self.outcome = (False, "")
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 (http.server's name)
                if not parse_qs(urlsplit(self.path).query):
                    self.send_response(204)
                    self.end_headers()
                    return
                outer.feed(self.path)
                if outer.code:
                    # Hold the tab until the token is exchanged and checked, then say what really happened.
                    outer.finished.wait(60)
                    ok, text = outer.outcome if outer.finished.is_set() else (
                        False, "HAYAT is still checking the sign-in; the HAYAT window shows the result.")
                else:
                    ok, text = False, "The sign-in did not finish: " + (outer.error or "no code") + "."
                colour = "#1d7a46" if ok else "#a3262f"
                body = (f"<!doctype html><meta charset=utf-8><title>HAYAT</title><body style='font-family:Segoe UI;"
                        f"padding:40px'><h2 style='color:{colour}'>{html.escape(text)}</h2>")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(body.encode("utf-8"))
                outer.done.set()

            def log_message(self, *args: Any) -> None:  # nothing about a sign-in reaches a log
                return

        self.servers = [ThreadingHTTPServer(("127.0.0.1", port), Handler)]
        self.port = self.servers[0].server_address[1]
        try:
            import socket

            class V6(ThreadingHTTPServer):
                address_family = socket.AF_INET6
            self.servers.append(V6(("::1", self.port), Handler))
        except OSError:
            pass
        for server in self.servers:
            threading.Thread(target=server.serve_forever, name="hayat-sign-in", daemon=True).start()

    def feed(self, address: str, *, pasted: bool = False) -> bool:
        """Take the provider's answer: the redirect itself, or the address a tab ended on, pasted by the owner.

        A pasted address from another sign-in is refused (ValueError) and the sign-in keeps waiting; a redirect
        with the wrong state ends the attempt, as a forged one would.
        """
        if self.done.is_set():
            return bool(self.code)
        text = str(address or "").strip()
        query = parse_qs(urlsplit(text).query if "?" in text else text.lstrip("?"))
        if query.get("state", [""])[0] != self.state:
            if pasted:
                raise ValueError("That address is not from this sign-in. Paste the whole address shown in the tab this "
                                 "sign-in opened (it starts with http://127.0.0.1 or http://localhost and has code= in it).")
            self.error = "The sign-in came back with the wrong state value; it was ignored."
        elif query.get("error"):
            self.error = query.get("error_description", query["error"])[0]
        else:
            self.code, self.error = query.get("code", [""])[0] or None, None
        self.done.set()
        return bool(self.code)

    def cancel(self) -> None:
        if not self.done.is_set():
            self.error = "You cancelled the sign-in."
            self.done.set()

    def finish(self, ok: bool, text: str) -> None:
        """What the waiting tab shows: the real result of the exchange and check."""
        self.outcome = (ok, text)
        self.finished.set()

    def wait(self, timeout: float) -> str:
        if not self.done.wait(timeout):
            raise TimeoutError("The sign-in was not finished within five minutes; start it again.")
        if self.error or not self.code:
            raise PermissionError(self.error or "The provider returned no code.")
        return self.code

    def close(self) -> None:
        for server in self.servers:
            server.shutdown()
            server.server_close()


class AccountTools:
    """Tools for chat: status, reads, and writes prepared for the owner's confirmation."""

    def __init__(self, manager: AccountManager, gate: Any) -> None:
        self.manager, self.gate = manager, gate

    def prepare(self, provider: str, operation: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        title, details, run = self.manager.prepare(provider, operation, dict(params or {}))
        from app.safety.confirmations import fingerprint
        waiting = [{"field": name, "value": value} for name, value in details]
        for pending in self.gate.pending():
            if pending["kind"] == "account" and pending["title"] == title and pending["details"] == waiting:
                # Asking twice must not queue two identical posts for two confirmations.
                return {**pending, "already_waiting": True,
                        "how_to_confirm": "This exact action is already waiting in the confirmation window; nothing new was added."}
        return self.gate.propose("account", title, PROVIDERS[provider].name, details, lambda _secrets: run(),
                                 notes=["This acts as you through your connected account. Nothing has been sent yet."],
                                 fingerprint_value=fingerprint(details))

    def post_everywhere(self, text: str) -> dict[str, Any]:
        """One prepared post per connected social account; each waits for its own confirmation."""
        text = str(text or "").strip()
        prepared, skipped = [], []
        records = self.manager._records()
        for key, operation in POST_OPERATIONS.items():
            provider = PROVIDERS.get(key)
            if provider is None or not (records.get(key) or {}).get("connected"):
                continue
            limit = POST_LIMITS.get(key)
            if limit and post_length(key, text) > limit:
                skipped.append({"name": provider.name, "why": f"longer than its {limit}-character limit"})
                continue
            try:
                result = self.prepare(key, operation, {"text": text})
            except (ValueError, PermissionError) as exc:
                skipped.append({"name": provider.name, "why": str(exc)})
                continue
            prepared.append({"name": provider.name, **result})
        if not prepared and not skipped:
            raise PermissionError("No social account that can post is connected yet: connect one in Accounts & sign-in.")
        return {"post_everywhere": True, "prepared": prepared, "skipped": skipped,
                "how_to_confirm": f"{len(prepared)} post{'s' if len(prepared) != 1 else ''} wait in the confirmation window, "
                                  "one per account; nothing is sent until you confirm each."}


#: The plain-text post of each social account that has one, and its length limit where it is short.
POST_OPERATIONS = {"bluesky": "post", "mastodon": "post", "x": "post", "threads": "post", "linkedin": "post", "facebook": "post"}
POST_LIMITS = {"bluesky": 300, "x": 280, "threads": 500, "mastodon": 500}


def post_length(key: str, text: str) -> int:
    """The length each network counts: Bluesky by visible characters, X by its own weights, the rest by characters."""
    from app.accounts_social import graphemes, x_weight

    return x_weight(text) if key == "x" else graphemes(text) if key == "bluesky" else len(text)


def register_account_tools(registry: Any, manager: AccountManager, gate: Any) -> AccountTools:
    tools = AccountTools(manager, gate)
    keys = list(PROVIDERS)

    def obj(properties: dict[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
        return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}
    names = ", ".join(provider.name for provider in PROVIDERS.values())
    registry.register("account_status", f"Which of the owner's accounts ({names}) are connected, as "
                      "whom, and how to connect the others. Never shows tokens.",
                      obj({"provider": {"enum": ["", *keys]}}), "low", "system_read",
                      lambda provider="": manager.status(provider))
    registry.register("account_check", "Check that the owner's connected accounts really work: one live read of whose account "
                      "each sign-in belongs to, with the time. Use for 'is my bluesky connected', 'check my accounts'.",
                      obj({"provider": {"enum": ["", *keys]}}), "low", "system_read",
                      lambda provider="": manager.check_all(provider))
    registry.register("account_read", "Read from a connected account: Gmail or Outlook inbox, calendar events, Drive or OneDrive "
                      "files, YouTube channel and uploads, GitHub repositories, notifications and issues, LinkedIn profile, Notion "
                      "search, Slack and Discord channels, Bluesky and Mastodon timelines and notifications, Reddit inbox and "
                      "subreddits, Telegram messages to the bot, Facebook Page and Instagram posts, Threads posts.",
                      obj({"provider": {"enum": keys}, "operation": {"type": "string", "maxLength": 40},
                           "params": {"type": "object", "additionalProperties": {"type": "string", "maxLength": 400}}},
                          ("provider", "operation")), "medium", "connector_read",
                      lambda provider, operation, params=None: manager.read(provider, operation, params))
    registry.register("account_prepare", "Prepare sending or creating something through a connected account (an email, a calendar "
                      "event, a GitHub issue, a post on LinkedIn, Bluesky, Mastodon, X, Threads, a Facebook Page or a subreddit, a "
                      "Slack, Discord or Telegram message). Nothing is sent: the owner confirms it in the confirmation window.",
                      obj({"provider": {"enum": keys}, "operation": {"type": "string", "maxLength": 40},
                           "params": {"type": "object", "additionalProperties": {"type": "string", "maxLength": 20000}}},
                          ("provider", "operation", "params")), "medium", "connector_write", tools.prepare)
    registry.register("account_post_everywhere", "Prepare the same short post for every connected social account that can post "
                      "(Bluesky, Mastodon, X, Threads, LinkedIn, a Facebook Page): one confirmation per account, each sent only "
                      "when the owner confirms it. Accounts it does not fit (too long) are named with the reason.",
                      obj({"text": {"type": "string", "minLength": 1, "maxLength": 3000}}, ("text",)), "medium", "connector_write",
                      tools.post_everywhere)
    return tools
