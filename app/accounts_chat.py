"""Chat for connected accounts: "check my gmail", "my outlook calendar", "my github notifications",
"search notion for invoices", "which accounts are connected", "connect my google account".

Reads go straight to the account; a reply is written from what came back. Connecting is never
done from chat: signing in happens on the provider's page, started from Accounts & sign-in.
Sending stays with the confirmation window (account_prepare).
"""
from __future__ import annotations

import re
from typing import Any

TOOL_NAMES = ("account_status", "account_read", "account_prepare", "account_post_everywhere")
_LEAD = r"^(?:(?:hey\s+)?hayat[\s,.!:-]*)?(?:please\s+)?(?:(?:can|could|would)\s+(?:you|u)\s+)?(?:please\s+)?"
_END = r"(?:\s*,?\s*(?:please|pls|thanks|thank\s+you))?\s*[?.!]*$"
_SHOW = r"(?:(?:check|show(?:\s+me)?|open|read|list|get|what(?:'s|\s+is|\s+are)\s+(?:in\s+)?)\s+)?(?:my\s+|the\s+)?"
#: The start of a post command: "hayat, …", "can you …", "please go ahead and …".
_POST_LEAD = (r"^(?:(?:hey\s+)?hayat[\s,.!:-]*)?(?:please\s+)?(?:(?:can|could|would)\s+(?:you|u)\s+)?(?:please\s+)?"
              r"(?:(?:go\s+ahead\s+and|just|now)\s+)?")
#: "open outlook", "launch linkedin": the app or the site, not a read of the account. Opening counts as a read
#: only with what to read in it ("open my outlook inbox").
_OPEN_ONLY = re.compile(r"(?i)^(?:(?:hey\s+)?hayat[\s,.!:-]*)?(?:please\s+)?(?:open|launch|start|run|go\s+to)\b")
_CONTENT = re.compile(r"(?i)\b(?:inbox|e-?mails?|mail|messages|notifications|calendar|events|files|repos|repositories|issues|"
                      r"channels|posts|timeline|feed|uploads|videos|stats|statistics|subscribers|servers|subreddits|subscriptions)\b")


def _compile(pattern: str) -> re.Pattern[str]:
    return re.compile(_LEAD + pattern + _END, re.IGNORECASE)


ROUTES: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (_compile(_SHOW + r"(?:latest\s+|new\s+|unread\s+)?(?:gmail|google\s+mail)(?:\s+(?:inbox|emails?|messages|mail))?"), "google", "gmail_inbox"),
    (_compile(_SHOW + r"(?:latest\s+|new\s+|unread\s+)?(?:outlook|hotmail|microsoft)\s*(?:inbox|emails?|messages|mail)?"), "microsoft", "mail_inbox"),
    (_compile(_SHOW + r"google\s+calendar(?:\s+events)?|(?:upcoming\s+)?events\s+(?:in|on)\s+(?:my\s+)?google\s+calendar"), "google", "calendar_upcoming"),
    (_compile(_SHOW + r"(?:outlook|microsoft)\s+calendar(?:\s+events)?|(?:upcoming\s+)?events\s+(?:in|on)\s+(?:my\s+)?outlook\s+calendar"),
     "microsoft", "calendar_upcoming"),
    (_compile(_SHOW + r"(?:recent\s+)?(?:google\s+)?drive\s+files|(?:recent\s+)?files\s+(?:in|on)\s+(?:my\s+)?google\s+drive"), "google", "drive_recent"),
    (_compile(_SHOW + r"(?:recent\s+)?onedrive\s+files|(?:recent\s+)?files\s+(?:in|on)\s+(?:my\s+)?onedrive"), "microsoft", "files_recent"),
    (_compile(_SHOW + r"(?:unread\s+)?github\s+notifications"), "github", "notifications"),
    (_compile(_SHOW + r"github\s+(?:repos|repositories)"), "github", "repos"),
    (_compile(_SHOW + r"github\s+issues(?:\s+assigned\s+to\s+me)?|issues\s+assigned\s+to\s+me\s+on\s+github"), "github", "issues"),
    (_compile(_SHOW + r"slack\s+channels"), "slack", "channels"),
    (_compile(_SHOW + r"linkedin(?:\s+profile)?|who\s+am\s+i\s+on\s+linkedin"), "linkedin", "profile"),
    # Everyday ways of asking.
    (_compile(r"(?:do\s+i\s+have|are\s+there|any)\s+(?:new\s+|unread\s+)?(?:e-?mails?|mail|messages)\s+(?:in|on)\s+(?:my\s+)?gmail"),
     "google", "gmail_inbox"),
    (_compile(r"(?:do\s+i\s+have|are\s+there|any)\s+(?:new\s+|unread\s+)?(?:e-?mails?|mail|messages)\s+(?:in|on)\s+(?:my\s+)?"
              r"(?:outlook|hotmail)"), "microsoft", "mail_inbox"),
    (_compile(r"what(?:'s|\s+is)\s+(?:on|in)\s+my\s+google\s+calendar(?:\s+(?:today|tomorrow|this\s+week))?"), "google", "calendar_upcoming"),
    (_compile(r"what(?:'s|\s+is)\s+(?:on|in)\s+my\s+(?:outlook|microsoft)\s+calendar(?:\s+(?:today|tomorrow|this\s+week))?"),
     "microsoft", "calendar_upcoming"),
    (_compile(r"(?:anything|what(?:'s|\s+is))\s+new\s+on\s+(?:my\s+)?github"), "github", "notifications"),
    # Social and messaging accounts.
    (_compile(_SHOW + r"(?:bluesky|bsky)\s+notifications"), "bluesky", "notifications"),
    (_compile(_SHOW + r"(?:bluesky|bsky)(?:\s+(?:timeline|feed|home))?|(?:what(?:'s|\s+is)\s+)?new\s+on\s+(?:my\s+)?bluesky"),
     "bluesky", "timeline"),
    (_compile(_SHOW + r"mastodon\s+notifications"), "mastodon", "notifications"),
    (_compile(_SHOW + r"mastodon(?:\s+(?:timeline|feed|home))?|(?:what(?:'s|\s+is)\s+)?new\s+on\s+(?:my\s+)?mastodon"),
     "mastodon", "home"),
    (_compile(_SHOW + r"reddit\s+(?:inbox|messages|replies)|(?:unread\s+)?reddit\s+messages"), "reddit", "inbox"),
    (_compile(_SHOW + r"(?:subreddits|reddit\s+subscriptions)"), "reddit", "subreddits"),
    (_compile(_SHOW + r"telegram\s+(?:messages|inbox)|messages\s+to\s+my\s+(?:telegram\s+)?bot"), "telegram", "messages"),
    (_compile(_SHOW + r"discord\s+servers"), "discord", "servers"),
    (_compile(_SHOW + r"youtube\s+(?:channel|stats|statistics|subscribers)|how\s+(?:is|are)\s+my\s+youtube(?:\s+channel)?\s+doing"),
     "google", "youtube_channel"),
    (_compile(_SHOW + r"(?:latest\s+)?youtube\s+(?:videos|uploads)"), "google", "youtube_uploads"),
    (_compile(_SHOW + r"(?:facebook\s+)?page\s+posts|facebook\s+posts"), "facebook", "posts"),
    (_compile(_SHOW + r"(?:latest\s+)?instagram\s+posts|(?:my\s+)?instagram"), "facebook", "instagram"),
    (_compile(_SHOW + r"threads\s+posts"), "threads", "posts"),
    (_compile(_SHOW + r"(?:x|twitter)\s+profile|who\s+am\s+i\s+on\s+(?:x|twitter)"), "x", "profile"),
)
_NOTION = _compile(r"search\s+(?:my\s+)?notion\s+(?:for\s+)?(?P<query>.{1,120}?)")
_STATUS = _compile(r"(?:which|what)\s+(?:of\s+my\s+)?accounts\s+(?:are|is)\s+(?:connected|linked|signed\s+in)|(?:show\s+)?my\s+connected\s+accounts"
                   r"|account\s+status")
_CONNECT = _compile(r"(?:connect|link|sign\s+in\s+to|log\s*in\s+to|login\s+to|add)\s+(?:my\s+)?(?P<provider>google|gmail|microsoft|outlook|"
                    r"hotmail|github|notion|slack|linkedin|bluesky|mastodon|reddit|x|twitter|telegram|discord|facebook|instagram|"
                    r"threads|youtube)(?:\s+(?:account|page|bot))?")
PROVIDER_WORDS = {"gmail": "google", "outlook": "microsoft", "hotmail": "microsoft", "twitter": "x", "instagram": "facebook",
                  "youtube": "google"}
#: "post to bluesky: …", "tweet: …", "toot: …": the provider and its post operation.
_POST_TO = re.compile(_POST_LEAD + r"(?:(?:post|share|publish)\s+(?:this\s+)?(?:to|on)\s+(?:my\s+)?(?P<where>bluesky|bsky|mastodon|x|twitter|"
                      r"threads|facebook(?:\s+page)?|discord)|(?P<verb>tweet|toot|skeet)(?:\s+(?:this|that))?)\s*[:,-]\s*(?P<text>.{1,3000})$",
                      re.IGNORECASE | re.DOTALL)
_POST_WORDS = {"bsky": "bluesky", "skeet": "bluesky", "twitter": "x", "tweet": "x", "toot": "mastodon", "facebook page": "facebook"}
_TELEGRAM = re.compile(_POST_LEAD + r"(?:send\s+(?:me\s+)?(?:a\s+)?telegram(?:\s+message)?|telegram\s+me|message\s+me\s+on\s+telegram)"
                       r"\s*[:,-]\s*(?P<text>.{1,4000})$", re.IGNORECASE | re.DOTALL)
_EVERYWHERE = re.compile(_POST_LEAD + r"(?:post|share|publish)\s+(?:this\s+)?(?:everywhere|to\s+all\s+(?:my\s+)?(?:social\s+(?:media|accounts|networks)|"
                         r"accounts)|on\s+all\s+(?:my\s+)?social\s+(?:media|accounts|networks))\s*[:,-]\s*(?P<text>.{1,3000})$",
                         re.IGNORECASE | re.DOTALL)
_HOT = _compile(r"(?:(?:what(?:'s|\s+is|\s+are)|show(?:\s+me)?)\s+(?:the\s+)?)?(?:hot|trending|top)\s+(?:posts\s+)?(?:on|in)\s+r/(?P<sub>[A-Za-z0-9_]{2,21})")


_POST_LINKEDIN = re.compile(_POST_LEAD + r"(?:post|share|publish)\s+(?:this\s+)?(?:to|on)\s+(?:my\s+)?linkedin\s*[:,-]\s*(?P<text>.{3,2900})$",
                            re.IGNORECASE | re.DOTALL)
_POST_SLACK = re.compile(_POST_LEAD + r"(?:post|send|say)\s+(?:this\s+)?(?:to|in|on)\s+(?:slack\s+)?(?P<channel>#[\w-]{1,80})"
                         r"(?:\s+on\s+slack)?\s*[:,-]\s*(?P<text>.{1,3000})$", re.IGNORECASE | re.DOTALL)


def account_request(text: str) -> dict[str, Any] | None:
    raw = str(text or "").strip()
    # Posting is only prepared: the confirmation window shows it, and nothing is sent until the owner confirms.
    if match := _POST_LINKEDIN.match(raw):
        return {"tool": "account_prepare", "arguments": {"provider": "linkedin", "operation": "post", "params": {"text": match["text"].strip()}}}
    if match := _EVERYWHERE.match(raw):
        return {"tool": "account_post_everywhere", "arguments": {"text": match["text"].strip()}}
    if match := _POST_TO.match(raw):
        where = (match["where"] or match["verb"]).lower()
        provider = _POST_WORDS.get(where, where)
        operation = "post_message" if provider == "discord" else "post"
        return {"tool": "account_prepare", "arguments": {"provider": provider, "operation": operation,
                                                          "params": {"text": match["text"].strip()}}}
    if match := _TELEGRAM.match(raw):
        return {"tool": "account_prepare", "arguments": {"provider": "telegram", "operation": "send",
                                                          "params": {"text": match["text"].strip()}}}
    if (match := _POST_SLACK.match(raw)) and "slack" in raw.lower():
        return {"tool": "account_prepare", "arguments": {"provider": "slack", "operation": "post_message",
                                                          "params": {"channel": match["channel"], "text": match["text"].strip()}}}
    value = " ".join(raw.split())
    if not value or len(value) > 200:
        return None
    if len(re.findall(r"\w+", value)) < 2:
        return None  # a bare "instagram" or "outlook" names the app or the site, not a read
    if _OPEN_ONLY.match(value) and not _CONTENT.search(value):
        return None
    if _STATUS.match(value):
        return {"tool": "account_status", "arguments": {}}
    if match := _CONNECT.match(value):
        word = match["provider"].lower()
        return {"tool": "account_status", "arguments": {"provider": PROVIDER_WORDS.get(word, word)}}
    if match := _HOT.match(value):
        return {"tool": "account_read", "arguments": {"provider": "reddit", "operation": "hot", "params": {"subreddit": match["sub"]}}}
    if match := _NOTION.match(value):
        return {"tool": "account_read", "arguments": {"provider": "notion", "operation": "search", "params": {"query": match["query"].strip()}}}
    for pattern, provider, operation in ROUTES:
        if pattern.match(value):
            return {"tool": "account_read", "arguments": {"provider": provider, "operation": operation}}
    return None


def direct_plan(text: str, names: Any) -> dict[str, Any] | None:
    found = account_request(text)
    if not found or found["tool"] not in names:
        return None
    return {"description": "Read from the owner's connected account and report what came back.", "tool": found["tool"],
            "arguments": found["arguments"], "expected": {"type": "tool_success"}, "max_retries": 0}


def report(history: list[dict[str, Any]]) -> str | None:
    sections = []
    for event in history or []:
        if not str(event.get("tool", "")).startswith("account_"):
            continue
        result = event.get("result") or {}
        if not result.get("success") or not (event.get("verification") or {}).get("passed"):
            continue
        value = result.get("output") or {}
        if value.get("read_view"):
            rows = value["rows"]
            lines = [f"{value['name']} · {value['label']}: {len(rows)} item{'s' if len(rows) != 1 else ''}."]
            for item in rows[:10]:
                lines.append("  " + ("● " if item.get("unread") else "") + " · ".join(
                    str(part) for part in (item.get("title"), item.get("detail"), str(item.get("when") or "")[:16].replace("T", " ")) if part))
            sections.append("\n".join(lines))
        elif value.get("accounts_view") and "accounts" in value:
            lines = ["Connected: " + (", ".join(value["connected"]) or "none yet") + "."]
            for row in value["accounts"]:
                if not row["connected"]:
                    lines.append(f"  {row['name']} ({row['what']}): not connected · ways: " + ", ".join(m["label"] for m in row["methods"]))
            lines += ["Connect one from Studios → Accounts & sign-in. " + value["basis"]]
            sections.append("\n".join(lines))
        elif value.get("key") and "connected" in value:
            if value["connected"]:
                sections.append(f"{value['name']} is connected as {value['connected_as']}.")
            else:
                ways = ", ".join(method["label"] + ("" if method["ready"] else " (after the one-time app setup)") for method in value["methods"])
                sections.append(f"{value['name']} is not connected yet. Open Studios → Accounts & sign-in, choose {value['name']} and "
                                f"pick a way: {ways}. You sign in on {value['name']}'s own page; HAYAT never sees your password.")
        elif value.get("post_everywhere"):
            lines = [f"Prepared for {', '.join(item['name'] for item in value['prepared']) or 'no account'}. {value['how_to_confirm']}"]
            lines += [f"  Left out {item['name']}: {item['why']}" for item in value.get("skipped") or []]
            sections.append("\n".join(lines))
        elif value.get("how_to_confirm"):
            sections.append(f"Prepared: {value.get('title', '')}. {value['how_to_confirm']}")
    return "\n\n".join(sections) or None
