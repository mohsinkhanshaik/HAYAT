"""Social accounts: Bluesky, Mastodon, Reddit, X, Telegram, Discord, Facebook Pages (with Instagram), Threads,
and YouTube through Google.

Same rules as every account (app/accounts.py): the owner signs in on the provider's own page or pastes a
token the provider made for apps (Bluesky's app password is one: made for a single app, revocable at
any time, not the account password); tokens live in the secret broker; reading runs directly; anything
that posts or sends is only prepared and waits for the owner in the confirmation window.

Only what each platform allows a personal, no-cost app is offered. The ones that do not (TikTok and
Pinterest audit or approve apps before they may post; Snapchat has no posting API for individuals) are
listed with the reason instead of being faked.
"""
from __future__ import annotations

import html
import json
import re
import time
from datetime import UTC, datetime
from typing import Any

BSKY = "https://bsky.social/xrpc/"
REDDIT = "https://oauth.reddit.com"
REDDIT_AGENT = "windows:hayat-assistant:1.0 (personal assistant)"
X_API = "https://api.x.com/2"
DISCORD = "https://discord.com/api/v10"
GRAPH = "https://graph.facebook.com"
THREADS = "https://graph.threads.net/v1.0"

#: Platforms people ask for that cannot be connected honestly at no cost, and why.
NOT_OFFERED = (
    {"name": "TikTok", "why": "TikTok lets only apps it has audited post videos; a personal app's posts stay private drafts."},
    {"name": "Pinterest", "why": "Pinterest approves each app before it may create pins on a real account."},
    {"name": "Snapchat", "why": "Snapchat offers no posting or messaging API for individuals."},
    {"name": "Instagram posting", "why": "Instagram publishes only from a public image web address on a business account; "
                                         "HAYAT reads your Instagram posts through your Facebook Page instead."},
)


def social_providers(Provider: Any, Operation: Any) -> tuple[Any, ...]:
    post = (("text", "Post", True),)
    return (
        Provider("bluesky", "Bluesky", "Your timeline and notifications, and posting once you confirm", ("browser", "token"),
                 # The browser first: AT Protocol OAuth as a localhost client needs no app registration (app/accounts_atproto.py).
                 dynamic_app=True, browser_title="Sign in with Bluesky in your browser",
                 token_label="app password", token_fields=(("handle", "Your handle, e.g. name.bsky.social"),),
                 token_page="https://bsky.app/settings/app-passwords", group="Social",
                 token_steps=("Press Make a token: Bluesky's App passwords page opens (Settings → Privacy and security → "
                              "App passwords).",
                              "Add app password, name it \"HAYAT\" and copy it. It is made for one app and can be removed any time; "
                              "it is not your account password.",
                              "Type your handle and paste the app password above. HAYAT keeps only the session Bluesky gives "
                              "back, not the app password."),
                 operations=(Operation("profile", "Who am I signed in as"),
                             Operation("timeline", "Latest posts from people I follow"),
                             Operation("notifications", "My notifications"),
                             Operation("post", "Post to Bluesky", True, post))),
        Provider("mastodon", "Mastodon", "Your home timeline and notifications on your server, and posting once you confirm",
                 # The browser first: HAYAT registers itself with your server, so it is one click with no setup.
                 ("browser", "token"), token_fields=(("server", "Your server, e.g. mastodon.social"),), dynamic_app=True,
                 token_page="https://mastodon.social/settings/applications/new", group="Social",
                 token_steps=("On your server open Preferences → Development → New application (Make a token opens it on "
                              "mastodon.social; use your own server's address if it is another).",
                              "Name it \"HAYAT\"; scopes read and write:statuses; Submit.",
                              "Open the application, copy \"Your access token\", type your server above and paste the token."),
                 operations=(Operation("profile", "Who am I signed in as"),
                             Operation("home", "My home timeline"),
                             Operation("notifications", "My notifications"),
                             Operation("post", "Post to Mastodon", True, post))),
        Provider("reddit", "Reddit", "Your inbox, subscriptions and subreddits, and posting once you confirm", ("browser",),
                 scopes=("identity", "read", "privatemessages", "submit", "mysubreddits"),
                 authorize_url="https://www.reddit.com/api/v1/authorize", token_url="https://www.reddit.com/api/v1/access_token",
                 revoke_url="https://www.reddit.com/api/v1/revoke_token", pkce=False, loopback_port=8798,
                 extra_authorize={"duration": "permanent"}, token_basic_auth=True, group="Social",
                 app_page="https://www.reddit.com/prefs/apps",
                 app_steps=("Open Reddit's app page (button below) and choose \"create another app…\".",
                            "Name \"HAYAT\"; type: installed app; redirect uri exactly http://127.0.0.1:8798/ — create app.",
                            "Copy the ID under the app's name (a short string) into the Client ID box below and press Save app. "
                            "An installed app has no secret.",
                            "Reddit's free API is for personal, non-commercial use, about 100 requests a minute."),
                 operations=(Operation("profile", "Who am I signed in as"),
                             Operation("inbox", "Unread messages and replies"),
                             Operation("subreddits", "Subreddits I've joined"),
                             Operation("hot", "Hot posts in a subreddit", fields=(("subreddit", "Subreddit, e.g. python", True),)),
                             Operation("submit", "Post to a subreddit", True,
                                       (("subreddit", "Subreddit, e.g. test", True), ("title", "Title", True),
                                        ("text", "Text", False))))),
        Provider("x", "X (Twitter)", "Your profile, and posting once you confirm", ("browser",),
                 scopes=("tweet.read", "tweet.write", "users.read", "offline.access"),
                 authorize_url="https://x.com/i/oauth2/authorize", token_url="https://api.x.com/2/oauth2/token",
                 revoke_url="https://api.x.com/2/oauth2/revoke", loopback_port=8797, group="Social",
                 app_page="https://developer.x.com/en/portal/dashboard",
                 app_steps=("Open the X developer portal (button below); the Free tier is enough. Create a project and app.",
                            "User authentication settings: OAuth 2.0 on; type of app: Native App; permissions: Read and write.",
                            "Callback URI exactly http://127.0.0.1:8797/ and any website address; save.",
                            "Copy the OAuth 2.0 Client ID into the box below and press Save app. A native app has no secret.",
                            "X's free tier lets an app post (about 500 posts a month) and read your own profile; reading "
                            "timelines needs a paid X tier, so HAYAT does not offer it."),
                 operations=(Operation("profile", "Who am I signed in as"),
                             Operation("post", "Post to X", True, post))),
        Provider("telegram", "Telegram", "Messages sent to your bot, and the bot messaging you once you confirm", ("token",),
                 token_label="bot token", token_page="https://t.me/BotFather", auth_scheme="", group="Messaging",
                 token_title="Connect your Telegram bot",
                 token_steps=("Press Make a token: Telegram opens @BotFather. Send /newbot and pick a name and a username "
                              "ending in bot.",
                              "BotFather replies with a token like 123456789:AA…; paste it above and press Enter.",
                              "Press Open my bot and tap Start: HAYAT links that chat by itself, so messages reach you "
                              "without a chat ID."),
                 operations=(Operation("profile", "Which bot"),
                             Operation("messages", "Latest messages to my bot"),
                             Operation("send", "Send a Telegram message from my bot", True,
                                       (("chat", "Chat ID (empty: whoever wrote to the bot last)", False), ("text", "Message", True))))),
        Provider("discord", "Discord", "Posting to a channel, and reading channels with a bot", ("browser", "webhook", "token"),
                 # The browser: you pick a server and channel on Discord's page and HAYAT gets a posting webhook for it.
                 scopes=("webhook.incoming",), authorize_url="https://discord.com/oauth2/authorize",
                 token_url="https://discord.com/api/oauth2/token", browser_needs_secret=True, pkce=False, loopback_port=8795,
                 browser_title="Choose a server and channel in your browser",
                 app_page="https://discord.com/developers/applications",
                 app_steps=("Open the Discord Developer Portal (button below) → New Application, name it \"HAYAT\".",
                            "OAuth2 → Redirects: add http://127.0.0.1:8795/ and save.",
                            "Copy the Client ID and (Reset Secret) the Client Secret into the boxes below and press Save app.",
                            "Then Choose a server and channel: Discord shows your servers; pick one and a channel, authorize, "
                            "and HAYAT can post there once you confirm each message."),
                 token_label="bot token", auth_scheme="Bot", group="Messaging",
                 webhook_prefixes=("https://discord.com/api/webhooks/", "https://discordapp.com/api/webhooks/"),
                 webhook_page="https://support.discord.com/hc/en-us/articles/228383668",
                 webhook_steps=("In your server: Channel settings → Integrations → Webhooks → New Webhook.",
                                "Name it \"HAYAT\", Copy Webhook URL, and paste it above. It posts to that one channel only."),
                 token_page="https://discord.com/developers/applications",
                 token_steps=("Press Make a token: New Application → Bot → Reset Token, and copy it.",
                              "Bot → Privileged Gateway Intents: switch on Message Content Intent, so channels can be read.",
                              "OAuth2 → URL Generator: scope bot, permissions Read Message History and Send Messages; open the "
                              "link and add the bot to your server.",
                              "Paste the token above. Channel IDs: User settings → Advanced → Developer Mode, then right-click "
                              "a channel → Copy Channel ID."),
                 operations=(Operation("profile", "Which bot"),
                             Operation("servers", "Servers the bot is in"),
                             Operation("history", "Latest messages in a channel", fields=(("channel", "Channel ID", True),)),
                             Operation("post_message", "Post a message to a channel", True,
                                       (("channel", "Channel ID (empty with a webhook)", False), ("text", "Message", True))))),
        Provider("facebook", "Facebook Page & Instagram", "Your Page's posts and your Instagram posts, and posting to the "
                 "Page once you confirm", ("browser", "token"), token_label="Page access token",
                 # Facebook Login in the browser: HAYAT keeps a Page token for your Page (and its linked Instagram).
                 scopes=("pages_show_list", "pages_read_engagement", "pages_manage_posts", "instagram_basic",
                         "business_management"), scope_separator=",",
                 authorize_url="https://www.facebook.com/v21.0/dialog/oauth",
                 token_url="https://graph.facebook.com/v21.0/oauth/access_token", browser_needs_secret=True, pkce=False,
                 loopback_host="localhost", loopback_port=8799, browser_title="Sign in with Facebook in your browser",
                 app_page="https://developers.facebook.com/apps/creation/",
                 app_steps=("Create an app at developers.facebook.com (button below): type Business. It stays in development "
                            "mode, which is enough for your own Pages and Instagram; no review is needed.",
                            "Add the product Facebook Login; under its Settings add http://localhost:8799/ to Valid OAuth "
                            "Redirect URIs (development mode allows localhost).",
                            "App settings → Basic: copy the App ID and App Secret into the boxes below and press Save app.",
                            "Sign in with Facebook: choose your Page (and its Instagram) on Facebook's page; HAYAT keeps that "
                            "Page's token, which does not expire."),
                 token_fields=(("page", "Page ID (Page → About → Page transparency, or the number in its address)"),),
                 token_page="https://developers.facebook.com/tools/explorer/", group="Social",
                 token_steps=("Create an app at developers.facebook.com (type Business; it stays in development mode, which "
                              "is enough for your own Page).",
                              "Press Make a token: the Graph API Explorer opens. Choose your app, then User Token with "
                              "pages_show_list, pages_read_engagement, pages_manage_posts and instagram_basic; Generate.",
                              "In the explorer choose your Page under \"User or Page\" to get its Page access token; for one "
                              "that lasts, open the Access Token Debugger and press Extend.",
                              "Paste the Page token and type the Page ID above. Instagram is read through the Page when your "
                              "professional Instagram account is linked to it."),
                 operations=(Operation("profile", "Which Page"),
                             Operation("posts", "My Page's latest posts"),
                             Operation("instagram", "My latest Instagram posts"),
                             Operation("post", "Post to my Facebook Page", True, (("text", "Post", True),)))),
        Provider("threads", "Threads", "Your Threads posts, and posting once you confirm", ("browser", "token"),
                 # Threads login in the browser; the 60-day token is renewed by itself while HAYAT is used.
                 scopes=("threads_basic", "threads_content_publish"), scope_separator=",",
                 authorize_url="https://threads.net/oauth/authorize", token_url="https://graph.threads.net/oauth/access_token",
                 browser_needs_secret=True, pkce=False, loopback_host="localhost", loopback_port=8794,
                 browser_title="Sign in with Threads in your browser",
                 app_page="https://developers.facebook.com/apps/creation/",
                 app_steps=("Create an app at developers.facebook.com (button below) with the Threads use case; add yourself as a "
                            "Threads tester and accept the invite in Threads (Settings → Account → Website permissions).",
                            "Threads API → Settings: Redirect Callback URL http://localhost:8794/ (if Threads only accepts "
                            "https, use the Make a token way below instead).",
                            "Copy the Threads App ID and App Secret into the boxes below and press Save app."),
                 token_page="https://developers.facebook.com/apps/", group="Social",
                 token_steps=("At developers.facebook.com create an app with the Threads use case; add yourself as a Threads "
                              "tester and accept the invite in Threads (Settings → Account → Website permissions).",
                              "Use cases → Threads API → permissions threads_basic and threads_content_publish; in the "
                              "User Token Generator press Generate access token for your account.",
                              "Paste the token above (it lasts 60 days; generate a new one then)."),
                 operations=(Operation("profile", "Who am I signed in as"),
                             Operation("posts", "My latest Threads posts"),
                             Operation("post", "Post to Threads", True, post))),
    )


def graphemes(text: str) -> int:
    """What people see as characters: one family emoji is one, though it is seven code points."""
    try:
        import regex

        return len(regex.findall(r"\X", text))
    except ImportError:  # the fallback over-counts joined emoji, which only errs on the side of refusing
        return len(text)


def x_weight(text: str) -> int:
    """X's own count (twitter-text): Latin and common punctuation weigh 1, other scripts and emoji 2, links 23."""
    links = re.findall(r"https?://\S+", text)
    rest = re.sub(r"https?://\S+", "", text)
    light = ((0, 4351), (8192, 8205), (8208, 8223), (8242, 8247))
    try:
        import regex

        clusters = regex.findall(r"\X", rest)
    except ImportError:
        clusters = list(rest)
    weight = 0
    for cluster in clusters:
        first = ord(cluster[0])
        weight += 1 if len(cluster) == 1 and any(low <= first <= high for low, high in light) else 2
    return weight + 23 * len(links)


def _plain(text: Any) -> str:
    """Mastodon gives HTML: the words only."""
    return html.unescape(re.sub(r"<[^>]+>", " ", str(text or ""))).replace("  ", " ").strip()


def _bluesky_link(uri: str, handle: str) -> str:
    rkey = str(uri or "").rsplit("/", 1)[-1]
    return f"https://bsky.app/profile/{handle}/post/{rkey}" if rkey and handle else ""


class SocialAccounts:
    """Reads and writes for the social providers; mixed into AccountManager (app/accounts.py)."""

    # ---- helpers the manager calls
    def _setting(self, key: str, name: str) -> str:
        value = ((self._records().get(key) or {}).get("settings") or {}).get(name, "")
        if not value:
            raise PermissionError(f"{self.provider(key).name} has no {name} saved; connect it again.")
        return value

    def _account_value(self, key: str) -> dict[str, Any]:
        return json.loads(self._secret(f"account.{key}", key))

    def _bluesky_session(self, handle: str, app_password: str) -> dict[str, Any]:
        session = self._json("POST", BSKY + "com.atproto.server.createSession",
                             json={"identifier": handle, "password": app_password}, headers={"Accept": "application/json"})
        if not session.get("accessJwt"):
            raise PermissionError("Bluesky did not open a session with that handle and app password.")
        return {"access_token": session["accessJwt"], "refresh_token": session.get("refreshJwt", ""), "did": session.get("did", ""),
                "handle": session.get("handle", handle), "expires_in": 6000}

    def _bsky(self, method: str, nsid: str, *, token: str = "", **kwargs: Any) -> Any:
        """A Bluesky call: the account's own server with a DPoP proof for a browser sign-in, bsky.social otherwise."""
        if self._account_value("bluesky").get("method") == "oauth":
            return self._bluesky_xrpc(method, nsid, **kwargs)
        return self._api("bluesky", method, BSKY + nsid, token=token, **kwargs)

    def _bluesky_refresh(self, value: dict[str, Any]) -> dict[str, Any]:
        if value.get("method") == "oauth":
            return self._bluesky_oauth_refresh(value)
        fresh = self._json("POST", BSKY + "com.atproto.server.refreshSession",
                           headers={"Authorization": f"Bearer {value['refresh_token']}", "Accept": "application/json"})
        if not fresh.get("accessJwt"):
            raise PermissionError("The Bluesky session has ended; paste a new app password.")
        value.update(access_token=fresh["accessJwt"], refresh_token=fresh.get("refreshJwt") or value["refresh_token"],
                     expires_at=time.time() + 6000)
        return value

    def _who_social(self, key: str, token: str) -> str | None:
        if key == "bluesky":
            try:
                data = self._bsky("GET", "com.atproto.server.getSession", token=token)
            except RuntimeError:
                if self._account_value("bluesky").get("method") != "oauth":
                    raise
                # Some servers answer getSession only for app-password sessions: the profile names the account too.
                data = self._bsky("GET", "app.bsky.actor.getProfile", params={"actor": self._account_value("bluesky")["did"]})
            return "@" + (data.get("handle") or "bluesky user")
        if key == "mastodon":
            server = self._mastodon_server()
            data = self._api(key, "GET", f"https://{server}/api/v1/accounts/verify_credentials", token=token)
            return f"@{data.get('acct', 'you')}@{server}"
        if key == "reddit":
            data = self._api(key, "GET", REDDIT + "/api/v1/me", token=token, headers={"User-Agent": REDDIT_AGENT})
            return "u/" + (data.get("name") or "you")
        if key == "x":
            data = self._api(key, "GET", X_API + "/users/me", token=token)
            return "@" + ((data.get("data") or {}).get("username") or "you")
        if key == "telegram":
            data = self._json("GET", f"https://api.telegram.org/bot{token}/getMe")
            if not data.get("ok"):
                raise PermissionError("Telegram refused that bot token.")
            return "@" + data["result"].get("username", "bot")
        if key == "discord":
            data = self._api(key, "GET", DISCORD + "/users/@me", token=token)
            return "bot " + (data.get("username") or "")
        if key == "facebook":
            page = self._setting("facebook", "page")
            data = self._api(key, "GET", f"{GRAPH}/{page}", token=token, params={"fields": "name"})
            return "Page: " + (data.get("name") or page)
        if key == "threads":
            data = self._api(key, "GET", THREADS + "/me", token=token, params={"fields": "username"})
            return "@" + (data.get("username") or "you")
        return None

    # ---- Bluesky
    def _read_bluesky(self, operation: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        handle = self._account_value("bluesky").get("handle", "")
        if operation == "profile":
            data = self._bsky("GET", "app.bsky.actor.getProfile", params={"actor": handle})
            return [{"title": data.get("displayName") or "@" + handle, "detail": "@" + handle,
                     "extra": f"{data.get('followersCount', 0)} followers · {data.get('postsCount', 0)} posts"}]
        if operation == "notifications":
            data = self._bsky("GET", "app.bsky.notification.listNotifications", params={"limit": 20})
            return [{"title": f"{item.get('reason', '')} from @{(item.get('author') or {}).get('handle', '')}",
                     "when": item.get("indexedAt", ""), "unread": not item.get("isRead", True),
                     "extra": str((item.get("record") or {}).get("text", ""))[:200]} for item in data.get("notifications") or []]
        data = self._bsky("GET", "app.bsky.feed.getTimeline", params={"limit": 20})
        rows = []
        for item in data.get("feed") or []:
            post = item.get("post") or {}
            author = (post.get("author") or {}).get("handle", "")
            rows.append({"title": str((post.get("record") or {}).get("text", ""))[:160] or "(image or link)",
                         "detail": "@" + author, "when": post.get("indexedAt", ""), "link": _bluesky_link(post.get("uri", ""), author)})
        return rows

    def _write_bluesky(self, operation: str, params: dict[str, str]) -> dict[str, Any]:
        text = params["text"]
        if graphemes(text) > 300:
            raise ValueError("A Bluesky post holds at most 300 characters.")
        value = self._account_value("bluesky")
        made = self._bsky("POST", "com.atproto.repo.createRecord",
                         json={"repo": value["did"], "collection": "app.bsky.feed.post",
                               "record": {"$type": "app.bsky.feed.post", "text": text,
                                          "createdAt": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")}})
        return {"posted": True, "where": "Bluesky", "link": _bluesky_link(made.get("uri", ""), value.get("handle", ""))}

    # ---- Mastodon
    def _read_mastodon(self, operation: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        base = f"https://{self._mastodon_server()}/api/v1"
        if operation == "profile":
            data = self._api("mastodon", "GET", base + "/accounts/verify_credentials")
            return [{"title": data.get("display_name") or data.get("acct", ""), "detail": "@" + data.get("acct", ""),
                     "extra": f"{data.get('followers_count', 0)} followers · {data.get('statuses_count', 0)} posts",
                     "link": data.get("url", "")}]
        if operation == "notifications":
            data = self._api("mastodon", "GET", base + "/notifications", params={"limit": 20})
            return [{"title": f"{item.get('type', '')} from @{(item.get('account') or {}).get('acct', '')}",
                     "when": item.get("created_at", ""), "extra": _plain((item.get("status") or {}).get("content"))[:200]}
                    for item in data or []]
        data = self._api("mastodon", "GET", base + "/timelines/home", params={"limit": 20})
        return [{"title": _plain(item.get("content"))[:160] or "(media)", "detail": "@" + (item.get("account") or {}).get("acct", ""),
                 "when": item.get("created_at", ""), "link": item.get("url", "")} for item in data or []]

    def _mastodon_server(self) -> str:
        """Checked again at every use: a name can be pointed somewhere else after it was saved."""
        from app.accounts import public_server

        return public_server(self._setting("mastodon", "server"))

    def _write_mastodon(self, operation: str, params: dict[str, str]) -> dict[str, Any]:
        base = f"https://{self._mastodon_server()}/api/v1"
        made = self._api("mastodon", "POST", base + "/statuses", data={"status": params["text"]})
        return {"posted": True, "where": "Mastodon", "link": made.get("url", "")}

    # ---- Reddit
    def _reddit(self, method: str, path: str, **kwargs: Any) -> Any:
        return self._api("reddit", method, REDDIT + path, headers={"User-Agent": REDDIT_AGENT}, **kwargs)

    def _read_reddit(self, operation: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        if operation == "profile":
            data = self._reddit("GET", "/api/v1/me")
            return [{"title": "u/" + data.get("name", ""), "extra": f"{data.get('link_karma', 0)} post karma · "
                     f"{data.get('comment_karma', 0)} comment karma"}]
        if operation == "inbox":
            data = self._reddit("GET", "/message/unread", params={"limit": 15})
            return [{"title": item["data"].get("subject") or "(reply)", "detail": "u/" + str(item["data"].get("author", "")),
                     "when": datetime.fromtimestamp(float(item["data"].get("created_utc", 0)), UTC).isoformat(timespec="minutes"),
                     "extra": str(item["data"].get("body", ""))[:200], "unread": True}
                    for item in (data.get("data") or {}).get("children") or []]
        if operation == "subreddits":
            data = self._reddit("GET", "/subreddits/mine/subscriber", params={"limit": 50})
            return [{"title": item["data"].get("display_name_prefixed", ""), "detail": f"{item['data'].get('subscribers', 0):,} members"}
                    for item in (data.get("data") or {}).get("children") or []]
        subreddit = re.sub(r"^/?r/", "", str(params.get("subreddit") or "").strip())
        if not re.fullmatch(r"[A-Za-z0-9_]{2,21}", subreddit):
            raise ValueError("Give a subreddit name such as python.")
        data = self._reddit("GET", f"/r/{subreddit}/hot", params={"limit": 15})
        return [{"title": item["data"].get("title", ""), "detail": f"{item['data'].get('score', 0)} points",
                 "link": "https://www.reddit.com" + str(item["data"].get("permalink", ""))}
                for item in (data.get("data") or {}).get("children") or []]

    def _write_reddit(self, operation: str, params: dict[str, str]) -> dict[str, Any]:
        subreddit = re.sub(r"^/?r/", "", params["subreddit"].strip())
        if not re.fullmatch(r"[A-Za-z0-9_]{2,21}", subreddit):
            raise ValueError("Give a subreddit name such as test.")
        made = self._reddit("POST", "/api/submit", data={"sr": subreddit, "kind": "self", "title": params["title"],
                                                        "text": params.get("text", ""), "api_type": "json"})
        result = made.get("json") or {}
        if result.get("errors"):
            raise RuntimeError("Reddit refused the post: " + "; ".join(" ".join(map(str, error)) for error in result["errors"]))
        return {"posted": True, "where": f"r/{subreddit}", "link": (result.get("data") or {}).get("url", "")}

    # ---- X
    def _read_x(self, operation: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        data = (self._api("x", "GET", X_API + "/users/me", params={"user.fields": "public_metrics,description"}).get("data") or {})
        metrics = data.get("public_metrics") or {}
        return [{"title": data.get("name", ""), "detail": "@" + data.get("username", ""),
                 "extra": f"{metrics.get('followers_count', 0)} followers · {metrics.get('tweet_count', 0)} posts"}]

    def _write_x(self, operation: str, params: dict[str, str]) -> dict[str, Any]:
        if x_weight(params["text"]) > 280:
            raise ValueError("A post on X holds at most 280 characters; X counts emoji, Chinese, Arabic and similar "
                             "letters as two, and every link as 23.")
        made = self._api("x", "POST", X_API + "/tweets", json={"text": params["text"]}).get("data") or {}
        return {"posted": True, "where": "X", "link": f"https://x.com/i/web/status/{made.get('id', '')}" if made.get("id") else ""}

    # ---- Telegram
    def _telegram(self, method: str, **kwargs: Any) -> Any:
        data = self._json("POST" if kwargs.get("json") else "GET", f"https://api.telegram.org/bot{self._token('telegram')}/{method}",
                          **kwargs)
        if not data.get("ok"):
            raise RuntimeError(f"Telegram: {data.get('description', 'request refused')}")
        return data["result"]

    def _read_telegram(self, operation: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        if operation == "profile":
            data = self._telegram("getMe")
            return [{"title": data.get("first_name", ""), "detail": "@" + data.get("username", "")}]
        rows = []
        for update in reversed(self._telegram("getUpdates", params={"limit": 20})):
            message = update.get("message") or update.get("channel_post") or {}
            if not message:
                continue
            sender = message.get("from") or message.get("chat") or {}
            rows.append({"title": str(message.get("text") or "(photo or file)")[:160],
                         "detail": " ".join(str(part) for part in (sender.get("first_name"), sender.get("last_name")) if part)
                         or sender.get("title", ""),
                         "when": datetime.fromtimestamp(int(message.get("date", 0)), UTC).isoformat(timespec="minutes"),
                         "extra": f"chat {(message.get('chat') or {}).get('id', '')}"})
        return rows

    def telegram_link_chat(self) -> dict[str, Any]:
        """The chat that tapped Start in the bot (or wrote to it last) becomes where HAYAT's messages go."""
        updates = self._telegram("getUpdates", params={"limit": 50})
        chats = []
        for update in updates:
            message = update.get("message") or {}
            chat = message.get("chat") or {}
            if chat.get("type") == "private" and chat.get("id"):
                chats.append((str(message.get("text") or "").startswith("/start"), chat))
        if not chats:
            raise ValueError("Nobody has opened the bot yet: press Open my bot, tap Start, then Link my chat.")
        started = [chat for is_start, chat in chats if is_start]
        chat = (started or [chat for _is_start, chat in chats])[-1]
        name = " ".join(str(part) for part in (chat.get("first_name"), chat.get("last_name")) if part) or chat.get("username", "")
        with self._lock:
            records = self._records()
            settings = records.setdefault("telegram", {}).setdefault("settings", {})
            settings.update(chat=str(chat["id"]), chat_name=name)
            self._save_records(records)
        return {**self.status("telegram"), "linked_chat": name or str(chat["id"])}

    def telegram_bot_link(self) -> str:
        """https://t.me/<bot>?start=hayat: opens the bot in Telegram with a Start button."""
        username = self._telegram("getMe").get("username", "")
        return f"https://t.me/{username}?start=hayat" if username else ""

    def _write_telegram(self, operation: str, params: dict[str, str]) -> dict[str, Any]:
        chat = params.get("chat", "").strip()
        if not chat:
            chat = ((self._records().get("telegram") or {}).get("settings") or {}).get("chat", "")
        if not chat:
            updates = self._telegram("getUpdates", params={"limit": 20})
            chats = [((update.get("message") or {}).get("chat") or {}).get("id") for update in updates]
            chats = [value for value in chats if value]
            if not chats:
                raise ValueError("Nobody has written to the bot yet: send it a message from your phone first, or give a chat ID.")
            chat = str(chats[-1])
        sent = self._telegram("sendMessage", json={"chat_id": chat, "text": params["text"]})
        return {"sent": True, "where": f"Telegram chat {chat}", "message_id": sent.get("message_id")}

    # ---- Discord
    def _read_discord(self, operation: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        if (self._records().get("discord") or {}).get("method") == "webhook":
            raise PermissionError("Discord is connected with a webhook, which can only post. Paste a bot token to read channels.")
        if operation == "profile":
            data = self._api("discord", "GET", DISCORD + "/users/@me")
            return [{"title": data.get("username", ""), "detail": "bot"}]
        if operation == "servers":
            return [{"title": item.get("name", ""), "detail": "owner" if item.get("owner") else ""}
                    for item in self._api("discord", "GET", DISCORD + "/users/@me/guilds") or []]
        channel = str(params.get("channel") or "").strip()
        if not channel.isdigit():
            raise ValueError("Give the channel ID (a long number): right-click the channel → Copy Channel ID.")
        return [{"title": str(item.get("content") or "(attachment or embed)")[:160],
                 "detail": (item.get("author") or {}).get("username", ""), "when": item.get("timestamp", "")}
                for item in self._api("discord", "GET", f"{DISCORD}/channels/{channel}/messages", params={"limit": 15}) or []]

    def _write_discord(self, operation: str, params: dict[str, str]) -> dict[str, Any]:
        if (self._records().get("discord") or {}).get("method") == "webhook":
            hook = self._account_value("discord")["webhook"]
            response = self._call("POST", hook, json={"content": params["text"]})
            if getattr(response, "status_code", 200) >= 400:
                raise RuntimeError(f"Discord did not post it (HTTP {response.status_code}).")
            return {"sent": True, "where": "the webhook's channel"}
        channel = params.get("channel", "").strip()
        if not channel.isdigit():
            raise ValueError("Give the channel ID to post to.")
        made = self._api("discord", "POST", f"{DISCORD}/channels/{channel}/messages", json={"content": params["text"]})
        return {"sent": True, "where": f"Discord channel {channel}", "id": made.get("id")}

    # ---- Facebook Page and Instagram
    def _read_facebook(self, operation: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        page = self._setting("facebook", "page")
        if operation == "profile":
            data = self._api("facebook", "GET", f"{GRAPH}/{page}", params={"fields": "name,followers_count,link"})
            return [{"title": data.get("name", ""), "detail": f"{data.get('followers_count', 0)} followers", "link": data.get("link", "")}]
        if operation == "instagram":
            linked = (self._api("facebook", "GET", f"{GRAPH}/{page}",
                                params={"fields": "instagram_business_account{id,username}"}).get("instagram_business_account") or {})
            if not linked.get("id"):
                raise PermissionError("No professional Instagram account is linked to this Page (Page settings → Linked accounts).")
            data = self._api("facebook", "GET", f"{GRAPH}/{linked['id']}/media",
                             params={"fields": "caption,timestamp,permalink,media_type", "limit": 15})
            return [{"title": str(item.get("caption") or "(no caption)")[:160], "detail": item.get("media_type", "").lower(),
                     "when": item.get("timestamp", ""), "link": item.get("permalink", "")} for item in data.get("data") or []]
        data = self._api("facebook", "GET", f"{GRAPH}/{page}/posts",
                         params={"fields": "message,created_time,permalink_url", "limit": 15})
        return [{"title": str(item.get("message") or "(photo or link)")[:160], "when": item.get("created_time", ""),
                 "link": item.get("permalink_url", "")} for item in data.get("data") or []]

    def _write_facebook(self, operation: str, params: dict[str, str]) -> dict[str, Any]:
        page = self._setting("facebook", "page")
        made = self._api("facebook", "POST", f"{GRAPH}/{page}/feed", data={"message": params["text"]})
        post_id = str(made.get("id", ""))
        return {"posted": True, "where": "your Facebook Page", "link": f"https://www.facebook.com/{post_id}" if post_id else ""}

    # ---- Threads
    def _read_threads(self, operation: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        if operation == "profile":
            data = self._api("threads", "GET", THREADS + "/me", params={"fields": "username,name"})
            return [{"title": data.get("name") or data.get("username", ""), "detail": "@" + data.get("username", "")}]
        data = self._api("threads", "GET", THREADS + "/me/threads", params={"fields": "text,timestamp,permalink", "limit": 15})
        return [{"title": str(item.get("text") or "(media)")[:160], "when": item.get("timestamp", ""), "link": item.get("permalink", "")}
                for item in data.get("data") or []]

    def _write_threads(self, operation: str, params: dict[str, str]) -> dict[str, Any]:
        if len(params["text"]) > 500:
            raise ValueError("A Threads post holds at most 500 characters.")
        container = self._api("threads", "POST", THREADS + "/me/threads", data={"media_type": "TEXT", "text": params["text"]})
        if not container.get("id"):
            raise RuntimeError("Threads did not accept the post.")
        made = self._api("threads", "POST", THREADS + "/me/threads_publish", data={"creation_id": container["id"]})
        return {"posted": True, "where": "Threads", "id": made.get("id", "")}

    # ---- YouTube, through the Google sign-in
    def _read_youtube(self, operation: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        try:
            data = self._api("google", "GET", "https://www.googleapis.com/youtube/v3/channels",
                             params={"part": "snippet,statistics,contentDetails", "mine": "true"})
        except RuntimeError as exc:
            if "403" in str(exc):
                raise PermissionError("Google has not allowed YouTube for HAYAT yet: enable the YouTube Data API v3 in your "
                                      "Google Cloud project and sign in to Google again.") from exc
            raise
        channels = data.get("items") or []
        if not channels:
            return [{"title": "This Google account has no YouTube channel."}]
        channel = channels[0]
        if operation == "youtube_channel":
            stats = channel.get("statistics") or {}
            return [{"title": (channel.get("snippet") or {}).get("title", ""),
                     "detail": f"{int(stats.get('subscriberCount', 0)):,} subscribers · {int(stats.get('videoCount', 0)):,} videos",
                     "extra": f"{int(stats.get('viewCount', 0)):,} views", "link": f"https://www.youtube.com/channel/{channel.get('id', '')}"}]
        uploads = ((channel.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads")
        data = self._api("google", "GET", "https://www.googleapis.com/youtube/v3/playlistItems",
                         params={"part": "snippet", "playlistId": uploads, "maxResults": 15})
        return [{"title": (item.get("snippet") or {}).get("title", ""), "when": (item.get("snippet") or {}).get("publishedAt", ""),
                 "link": "https://www.youtube.com/watch?v=" + (((item.get("snippet") or {}).get("resourceId") or {}).get("videoId", ""))}
                for item in data.get("items") or []]
