"""Onion sites you can trust, what each one is, and what an unknown onion link is likely to be.

Knowing where to start was the missing part of the onion navigator: it read any public .onion page, but only
one whose address you already had. Now:

* **A verified directory.** *Real-World Onion Sites* (Alec Muffett, CC BY-SA 4.0) lists the onion services of
  mainstream organisations (BBC, The New York Times, ProPublica, Deutsche Welle, DuckDuckGo, Facebook, the Tor
  Project, the CIA, Privacy International, SecureDrop inboxes of newsrooms…), each with a *proof* link (the
  organisation's own page announcing the address) and a daily reachability check. Its rules exclude onion-only
  sites and "nudity, exploitation, drugs, copyright infringement or sketchy-content" sites. HAYAT reads it through
  Tor, keeps a copy for a week, and shows each site's category, what that category covers, and how often it was up.
* **The nature of an index result.** Search results from the Ahmia onion index are labelled by what they look
  like (news, forum, library, privacy tool, crypto…) from their own title and description, and marked
  *unverified* unless the address is in the directory. Results that offer illegal goods or services (drugs,
  weapons, stolen data, fraud, hacking for hire, abuse) are withheld: counted, never linked.
* **Checking a link.** A pasted address is matched against the directory. An address that starts like a listed
  one but differs is flagged as a probable phishing copy, the most common onion trap.
"""
from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

DIRECTORY_URL = "https://raw.githubusercontent.com/alecmuffett/real-world-onion-sites/master/README.md"
DIRECTORY_PAGE = "https://github.com/alecmuffett/real-world-onion-sites"
CREDIT = "Real-World Onion Sites by Alec Muffett (CC BY-SA 4.0)"
KEEP_SECONDS = 7 * 24 * 3600

ABOUT = {
    "Blogs": "Personal and technical blogs by people known in privacy and security.",
    "Civil Society and Community": "Human-rights, digital-rights and community groups (Privacy International, Riseup…).",
    "Education": "Learning material, such as BBC Learning English.",
    "Government": "Government bodies that run onion services (the US Central Intelligence Agency).",
    "News": "Major news organisations' own sites, reachable where they are blocked or watched.",
    "News BBC World Service": "The BBC World Service in its many languages.",
    "News Deutsche Welle World": "Deutsche Welle in its many languages.",
    "News RFERL & VOA": "Radio Free Europe / Radio Liberty and Voice of America services.",
    "Search Engines": "Search engines with onion addresses (DuckDuckGo…).",
    "Social Networks": "Social networks with onion addresses (Facebook…).",
    "Tech and Software": "Software projects and technology companies (the Tor Project, Debian, Proton…).",
    "Web and Internet": "Internet infrastructure and services.",
    "SecureDrop": "Newsrooms' SecureDrop inboxes for sending documents to journalists anonymously.",
    "Flaky Sites": "Listed organisations whose onion service is often down.",
}

# What an index result appears to be, from its own words. First match wins; illegal kinds are checked first.
_WITHHELD = re.compile(
    r"(?i)\b(?:drugs?|cocaine|heroin|meth(?:amphetamine)?|mdma|lsd|cannabis\s+shop|weed\s+shop|pills?\s+shop|opioids?|fentanyl|"
    r"guns?|weapons?|firearms?|ammo|ammunition|explosives?|carding|cvv|fullz|dumps?|skimmer|stolen|leaked\s+(?:data|databases?|"
    r"accounts?)|counterfeit|fake\s+(?:ids?|passports?|documents?|money|bills)|hitman|hire\s+a\s+hacker|hacker\s+for\s+hire|"
    r"ddos\s+(?:service|for\s+hire)|ransomware|malware\s+(?:shop|for\s+sale)|escrow\s+market|darknet\s+market|marketplace|"
    r"cp\b|child\s+porn|pedo|underage|lolita|jailbait|money\s+launder|bank\s+logs?|paypal\s+accounts?|gift\s+cards?\s+(?:shop|cheap))\b")
NATURES = (
    ("News & journalism", r"\b(?:news|journalis\w*|newspaper|magazine|press|reporting|investigat\w*|media)\b"),
    ("Whistleblowing", r"\b(?:securedrop|whistle-?blow\w*|tips?|leak\s+to\s+us|send\s+(?:us\s+)?documents)\b"),
    ("Search & directories", r"\b(?:search(?:\s+engine)?|directory|index|links?\s+list|onion\s+list|wiki)\b"),
    ("Library & archives", r"\b(?:library|books?|e-?books?|archive|papers|research|documents|scihub|zlib)\b"),
    ("Privacy & security", r"\b(?:privacy|security|anonym\w*|encrypt\w*|tor\s+project|vpn|tails|pgp|opsec)\b"),
    ("Email & messaging", r"\b(?:e-?mail|mail|messag\w*|chat|xmpp|jabber|matrix)\b"),
    ("Forum & community", r"\b(?:forum|community|board|discussion|social|talk)\b"),
    ("Crypto & finance", r"\b(?:bitcoin|monero|crypto\w*|wallet|exchange|mixer|tumbler)\b"),
    ("Hosting & tech", r"\b(?:hosting|host|server|software|linux|open[- ]source|git|code|developer)\b"),
    ("Blog & personal", r"\b(?:blog|personal|my\s+site|homepage)\b"),
)


def _host(url: str) -> str:
    return (urlsplit(str(url or "")).hostname or "").lower().removeprefix("www.")


def _onion_key(url_or_host: str) -> str:
    """The 56-character v3 address itself, whatever subdomain or path is around it."""
    found = re.search(r"([a-z2-7]{56})\.onion", str(url_or_host or "").lower())
    return found[1] if found else ""


def parse_directory(text: str) -> list[dict[str, Any]]:
    """The list's categories and entries: name, onion link, proof page and how often it was up (last checks)."""
    sites: list[dict[str, Any]] = []
    for section in re.split(r"^## ", text, flags=re.M)[1:]:
        category = section.split("\n", 1)[0].strip()
        if category not in ABOUT:
            continue
        for entry in re.split(r"^### ", section, flags=re.M)[1:]:
            head = re.match(r"\[(?P<name>[^\]]{1,120})\]\((?P<url>https?://[^)\s]+)\)", entry)
            if not head or not _onion_key(head["url"]):
                continue
            proof = re.search(r"^\* proof: \[[^\]]*\]\((https?://[^)\s]+)\)", entry, re.M)
            # exit=0: the daily check reached the site and it answered (Facebook answers that automated check with a
            # 500, so counting only HTTP 200 called it down every day); other exits are timeouts and failures.
            codes = re.findall(r"exit=(\d+)", entry)[:14]
            up = sum(1 for code in codes if code == "0")
            sites.append({"name": head["name"].strip(), "url": head["url"], "category": category, "about": ABOUT[category],
                          "proof": proof[1] if proof and ".onion" not in _host(proof[1]) else "",
                          "https": entry.lstrip().find("**HTTPS**") >= 0 or head["url"].startswith("https://"),
                          "checks": len(codes), "up": up})
    return sites


class OnionDirectory:
    """`fetch_text(url) -> str` reads through HAYAT's fetcher (Tor route); `cache` keeps a week-old copy."""

    def __init__(self, fetch_text: Callable[[str], str] | None, cache: Path | str | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.fetch_text, self.cache, self.clock = fetch_text, Path(cache) if cache else None, clock
        self._sites: list[dict[str, Any]] | None = None
        self.fetched_at = 0.0
        self.error = ""

    def sites(self, refresh: bool = False) -> list[dict[str, Any]]:
        if self._sites is not None and not refresh:
            return self._sites
        kept = self._read_cache()
        if kept and not refresh and self.clock() - kept["fetched_at"] < KEEP_SECONDS:
            self._sites, self.fetched_at = kept["sites"], kept["fetched_at"]
            return self._sites
        try:
            if self.fetch_text is None:
                raise RuntimeError("no network reader in this session")
            sites = parse_directory(self.fetch_text(DIRECTORY_URL))
            if len(sites) < 20:
                raise ValueError("the directory page did not have its usual list")
            self._sites, self.fetched_at, self.error = sites, self.clock(), ""
            if self.cache is not None:
                self.cache.parent.mkdir(parents=True, exist_ok=True)
                self.cache.write_text(json.dumps({"fetched_at": self.fetched_at, "sites": sites}, ensure_ascii=False),
                                      encoding="utf-8")
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"[:240]
            if not kept:
                raise
            self._sites, self.fetched_at = kept["sites"], kept["fetched_at"]  # an older copy beats nothing
        return self._sites

    def _read_cache(self) -> dict[str, Any] | None:
        if self.cache is None or not self.cache.exists():
            return None
        try:
            data = json.loads(self.cache.read_text(encoding="utf-8"))
            return data if isinstance(data.get("sites"), list) and data.get("fetched_at") else None
        except (OSError, ValueError):
            return None

    def categories(self) -> list[tuple[str, int]]:
        counts: dict[str, int] = {}
        for site in self.sites():
            counts[site["category"]] = counts.get(site["category"], 0) + 1
        return [(name, counts[name]) for name in ABOUT if name in counts]

    def find(self, text: str = "", category: str = "") -> list[dict[str, Any]]:
        words = [word for word in re.findall(r"\w+", text.casefold()) if len(word) > 1]
        rows = []
        for site in self.sites():
            if category and site["category"] != category:
                continue
            haystack = f"{site['name']} {site['category']} {site['about']} {_host(site['proof'])}".casefold()
            if all(word in haystack for word in words):
                rows.append(site)
        # Reliable ones first within the list's own order.
        return sorted(rows, key=lambda site: -(site["up"] / site["checks"] if site["checks"] else 0))

    def check(self, url: str) -> dict[str, Any]:
        """Is this onion address one the directory lists, a lookalike of one, or simply unknown?"""
        key = _onion_key(url)
        if not key:
            return {"verdict": "not_onion", "text": "That is not a v3 onion address (56 letters and digits before .onion)."}
        sites = self.sites()
        for site in sites:
            if _onion_key(site["url"]) == key:
                return {"verdict": "listed", "site": site,
                        "text": f"Listed: {site['name']} ({site['category']}). Proof published by the organisation: "
                                f"{site['proof'] or 'see the directory'}. Up {site['up']} of the last {site['checks']} daily checks."}
        for site in sites:
            other = _onion_key(site["url"])
            if other[:6] == key[:6] or other[-6:] == key[-6:]:
                return {"verdict": "lookalike", "site": site,
                        "text": f"Warning: this looks like {site['name']}'s address but is a different one. Copies of real "
                                f"onion sites made to steal logins or money are common. {site['name']}'s listed address is "
                                f"{site['url']}"}
        return {"verdict": "unknown", "text": "Not in the verified directory. Anyone can run an onion site and nothing proves who "
                                              "runs this one; treat what it says, and any login or payment it asks for, with care."}


# ------------------------------------------------------------------ index results
def nature(title: str, snippet: str = "") -> str:
    """What a result appears to be from its own words; "Withheld" when it offers illegal goods or services."""
    text = f"{title} {snippet}"
    if _WITHHELD.search(text):
        return "Withheld"
    for name, pattern in NATURES:
        if re.search(pattern, text, re.IGNORECASE):
            return name
    return "Unclear from its description"


def describe_results(results: list[Any], directory: OnionDirectory | None) -> dict[str, Any]:
    """Index results with their nature and whether the directory vouches for them; illegal ones only counted."""
    rows, withheld = [], 0
    for item in results:
        title = str(getattr(item, "title", "") or (item.get("title") if isinstance(item, dict) else "") or "")
        url = str(getattr(item, "url", "") or (item.get("url") if isinstance(item, dict) else "") or "")
        snippet = str(getattr(item, "snippet", "") or (item.get("snippet") if isinstance(item, dict) else "") or "")
        kind = nature(title, snippet)
        if kind == "Withheld":
            withheld += 1
            continue
        verdict = directory.check(url) if directory is not None else {"verdict": "unknown"}
        if verdict["verdict"] == "lookalike":
            withheld += 1  # a copy of a real site is not shown as a result
            continue
        listed = verdict.get("site") if verdict["verdict"] == "listed" else None
        rows.append({"name": listed["name"] if listed else (title[:160] or _host(url)), "url": url,
                     "nature": listed["category"] if listed else kind, "about": listed["about"] if listed else snippet[:300],
                     "verified": verdict["verdict"] == "listed",
                     "listed_as": (verdict.get("site") or {}).get("name", "") if verdict["verdict"] == "listed" else ""})
    return {"results": rows, "withheld": withheld,
            "note": "Index results are unverified unless marked: anyone can run an onion site. "
                    + (f"{withheld} result{'s' if withheld != 1 else ''} offering illegal goods or services, or copying a real "
                       "site's address, were left out." if withheld else "")}


# ------------------------------------------------------------------ chat
_CHECK_ASK = re.compile(r"(?i)\b(?:is|check|verify|safe|real|legit|genuine|trust)\b.*?(https?://\S*?[a-z2-7]{56}\.onion\S*|[a-z2-7]{56}\.onion\S*)")
_LIST_ASK = re.compile(
    r"(?i)^(?:(?:please\s+)?(?:show|list|give|find|get|recommend|suggest)(?:\s+me)?\s+)?(?:some\s+|the\s+|good\s+|safe\s+|verified\s+|"
    r"real\s+|official\s+|legit(?:imate)?\s+)*(?:onion|tor|dark\s*web|\.onion)\s+(?:sites?|links?|services?|addresses|websites?|pages?)"
    r"(?:\s+(?:for|about|on|of|related\s+to|in)\s+(?P<topic>.{2,60}?))?\s*[.?!]*$")


def request(text: str) -> dict[str, Any] | None:
    value = " ".join(str(text or "").split())
    if not value or len(value) > 400:
        return None
    if match := _CHECK_ASK.search(value):
        return {"check_url": match[1].rstrip(".,;)?!")}
    if match := _LIST_ASK.match(value):
        topic = (match["topic"] or "").strip()
        for name in ABOUT:  # "onion sites for news" -> the News category as well as the words
            if topic and topic.casefold() in name.casefold():
                return {"query": "", "category": name}
        return {"query": topic}
    return None


def plan(text: str, names: Any) -> dict[str, Any] | None:
    arguments = request(text)
    if arguments is None or "onion_sites" not in names:
        return None
    return {"description": "List verified onion sites and what they are, or check an onion address.", "tool": "onion_sites",
            "arguments": arguments, "expected": {"type": "tool_success"}, "max_retries": 0}


def report(history: list[dict[str, Any]]) -> str | None:
    if not history or any(item.get("tool") != "onion_sites" for item in history):
        return None
    result = history[-1].get("result") or {}
    if not result.get("success"):
        return "Onion sites could not be listed: " + str(result.get("error") or "no result.")
    value = result.get("output") or {}
    if value.get("onion_check"):
        return value.get("text", "")
    rows = value.get("directory") or []
    lines = [f"{len(rows)} verified onion site{'s' if len(rows) != 1 else ''}"
             + (f" for \"{value.get('query') or value.get('category')}\"" if value.get("query") or value.get("category") else "")
             + ". Each is the organisation's own onion service, with its published proof:"]
    for site in rows[:20]:
        lines.append(f"• {site['name']} ({site['category']}): {site['url']}")
        lines.append(f"  {site['about']} Proof: {site['proof'] or 'in the directory'} · reachable {site['up']} of {site['checks']} days")
    if len(rows) > 20:
        lines.append(f"…and {len(rows) - 20} more in Searches → Onion pages → Find onion sites.")
    if not rows:
        lines.append("None matched. Categories: " + ", ".join(name for name, _count in value.get("categories") or []) + ".")
    lines.append(f"Directory: {value.get('credit', CREDIT)}. Open them in the Onion navigator (Tor only, no scripts or sign-in).")
    return "\n".join(lines)
