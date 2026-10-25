"""Web 4.0, read honestly: how ready a website is for AI agents (the "agentic web").

"Web 4.0" has no agreed technical definition. HAYAT uses it for the web that AI agents read and
act on, and checks the machine-readable surfaces sites publish for them, each a public convention
or proposal:

* robots.txt rules for AI crawlers (GPTBot, ClaudeBot, Google-Extended, CCBot and others), and
  Content-Signal lines (search / ai-input / ai-train preferences);
* llms.txt and llms-full.txt (a Markdown map of the site for language models);
* an A2A agent card (/.well-known/agent-card.json, or the older agent.json);
* an MCP server manifest (/.well-known/mcp.json, a proposal);
* the older AI plugin manifest (/.well-known/ai-plugin.json);
* schema.org structured data, feeds and sitemaps on the home page;
* security.txt, the contact for reporting problems.

Every file is read with HAYAT's public fetcher: robots.txt is honoured (a disallowed file is
reported as not read), the user agent is HAYAT's own, nothing is submitted and nothing is logged in.
"""
from __future__ import annotations

import json
import logging
import re
import urllib.robotparser
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from app.logging import log_swallowed

_log = logging.getLogger(__name__)

#: (user agent, who runs it, what it is for).
AI_AGENTS = (("GPTBot", "OpenAI", "model training"), ("OAI-SearchBot", "OpenAI", "search answers"),
             ("ChatGPT-User", "OpenAI", "fetching for a user"), ("ClaudeBot", "Anthropic", "model training"),
             ("Claude-User", "Anthropic", "fetching for a user"), ("Google-Extended", "Google", "Gemini training"),
             ("PerplexityBot", "Perplexity", "search answers"), ("CCBot", "Common Crawl", "open web archive"),
             ("Applebot-Extended", "Apple", "model training"), ("Bytespider", "ByteDance", "model training"),
             ("meta-externalagent", "Meta", "model training"), ("Amazonbot", "Amazon", "assistant answers"))
#: (key, label, path, kind, what it means). kind: text (llms.txt), json.
SURFACES = (("llms", "llms.txt", "/llms.txt", "text", "A Markdown guide to the site written for language models."),
            ("llms_full", "llms-full.txt", "/llms-full.txt", "text", "The site's full text in one file for language models."),
            ("agent_card", "A2A agent card", "/.well-known/agent-card.json", "json",
             "The site runs an AI agent that other agents can call (Agent2Agent protocol)."),
            ("agent_json", "A2A agent card (older name)", "/.well-known/agent.json", "json", "The earlier location of the agent card."),
            ("mcp", "MCP manifest (proposal)", "/.well-known/mcp.json", "json", "Tools an AI assistant can use through the Model Context Protocol."),
            ("ai_plugin", "AI plugin manifest (older)", "/.well-known/ai-plugin.json", "json", "The 2023 ChatGPT plugin format."),
            ("security", "security.txt", "/.well-known/security.txt", "text", "Who to contact about a security problem."))
#: The surfaces that count toward readiness (the older or duplicate ones are shown but not counted).
SCORED = ("robots", "ai_rules", "llms", "agent_card", "mcp", "structured_data", "sitemap", "feed")
SITE = re.compile(r"(?i)^(?:https?://)?(?P<host>[a-z0-9-]+(?:\.[a-z0-9-]+)+)(?::\d+)?(?:/.*)?$")


def normalise(site: str) -> str:
    match = SITE.match(str(site or "").strip())
    if not match:
        raise ValueError("Give a website such as example.com or https://example.com.")
    return "https://" + match["host"].lower()


def soft_404(content: bytes, kind: str) -> bool:
    """A page that answers every address with its own HTML is not the file asked for."""
    head = content[:400].lstrip().lower()
    if head.startswith((b"<!doctype html", b"<html")) or b"<head" in head:
        return True
    if kind == "json":
        try:
            return not isinstance(json.loads(content), dict)
        except ValueError:
            return True
    return False


def robots_rules(text: str) -> dict[str, Any]:
    """Each AI agent's access to the site root, Content-Signal preferences and the sitemaps listed."""
    parser = urllib.robotparser.RobotFileParser()
    parser.parse(text.splitlines())
    named = {line.split(":", 1)[1].strip().lower() for line in text.splitlines() if line.lower().startswith("user-agent:")}
    agents = []
    for agent, owner, purpose in AI_AGENTS:
        allowed = parser.can_fetch(agent, "/")
        agents.append({"agent": agent, "owner": owner, "purpose": purpose, "allowed": allowed,
                       "named": agent.lower() in named})
    signals = {}
    for line in text.splitlines():
        if line.lower().startswith("content-signal:"):
            for part in line.split(":", 1)[1].split(","):
                key, _, value = part.strip().partition("=")
                if key and value:
                    signals[key.strip().lower()] = value.strip().lower()
    sitemaps = [line.split(":", 1)[1].strip() for line in text.splitlines() if line.lower().startswith("sitemap:")]
    return {"agents": agents, "named_ai_rules": sum(item["named"] for item in agents),
            "blocked": [item["agent"] for item in agents if not item["allowed"]], "content_signals": signals, "sitemaps": sitemaps[:5]}


def page_signals(html: str) -> dict[str, Any]:
    """schema.org types, feeds and AI-related robots meta tags on a page."""
    types: list[str] = []
    for block in re.findall(r'(?is)<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html)[:20]:
        try:
            data = json.loads(block.strip())
        except ValueError:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                kind = item.get("@type")
                types += kind if isinstance(kind, list) else [kind] if kind else []
                stack += item.get("@graph") or []
    feeds = re.findall(r'(?is)<link[^>]+type=["\']application/(?:rss|atom)\+xml["\'][^>]*>', html)
    robots_meta = " ".join(re.findall(r'(?is)<meta[^>]+name=["\'](?:robots|googlebot)["\'][^>]+content=["\']([^"\']+)', html)).lower()
    title = re.search(r"(?is)<title[^>]*>(.*?)</title>", html)
    return {"structured_data": sorted({str(kind) for kind in types if kind})[:12], "feeds": len(feeds),
            "noai": [word for word in ("noai", "noimageai") if word in robots_meta], "title": " ".join((title[1] if title else "").split())[:120]}


class AgentWebInspector:
    def __init__(self, fetcher: Any) -> None:
        self.fetcher = fetcher

    def _read(self, url: str, max_bytes: int = 512 * 1024) -> tuple[str, bytes | None, str]:
        """(state, content, note): found, missing, refused (robots.txt) or unavailable."""
        try:
            resource = self.fetcher.fetch(url, max_bytes)
            return "found", resource.content, ""
        except PermissionError:
            return "refused", None, "robots.txt asks agents not to read it, so HAYAT did not"
        except Exception as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in (404, 410):
                return "missing", None, ""
            if "exceeds" in str(exc):
                return "large", None, f"published, larger than {max_bytes // 1024:,} KB, so not read in full"
            log_swallowed(_log, "reading an agent-web surface")
            if status in (401, 403):
                return "unavailable", None, f"the site refused HAYAT (HTTP {status})"
            return "unavailable", None, (f"the site answered HTTP {status}" if status else f"could not be read ({type(exc).__name__})")

    def inspect(self, site: str) -> dict[str, Any]:
        origin = normalise(site)
        checks: dict[str, dict[str, Any]] = {}
        state, content, note = self._read(origin + "/robots.txt", 256 * 1024)
        rules = robots_rules(content.decode("utf-8", "replace")) if state == "found" and not soft_404(content, "text") else None
        checks["robots"] = {"label": "robots.txt", "present": rules is not None, "note": note,
                            "meaning": "The site's rules for crawlers, including AI ones."}
        checks["ai_rules"] = {"label": "Rules naming AI agents", "present": bool(rules and (rules["named_ai_rules"] or rules["content_signals"])),
                              "meaning": "The site states its own position on AI crawlers (allow or block), or Content-Signal preferences.",
                              "note": (f"{rules['named_ai_rules']} AI agents named" if rules else "")}
        previews: dict[str, str] = {}
        details: dict[str, Any] = {}
        for key, label, path, kind, meaning in SURFACES:
            state, content, note = self._read(origin + path, 1024 * 1024 if key == "llms_full" else 256 * 1024)
            present = state == "large" or state == "found" and content is not None and not soft_404(content, kind)
            if state == "found" and not present:
                note = "the address answers with an ordinary web page, not this file"
            checks[key] = {"label": label, "present": present, "path": path, "meaning": meaning, "note": note, "state": state}
            if present and content is not None:
                text = content.decode("utf-8", "replace")
                previews[key] = text[:1200]
                if kind == "json":
                    data = json.loads(content)
                    details[key] = {"name": data.get("name") or data.get("name_for_human"), "description": str(data.get("description")
                                    or data.get("description_for_human") or "")[:300],
                                    "skills": len(data.get("skills") or []), "tools": len(data.get("tools") or [])}
                elif key in ("llms", "llms_full"):
                    heading = next((line.lstrip("# ").strip() for line in text.splitlines() if line.startswith("#")), "")
                    details[key] = {"title": heading[:120], "links": len(re.findall(r"\]\((https?://[^)]+|/[^)]*)\)", text)),
                                    "size_kb": round(len(content) / 1024, 1)}
        state, content, note = self._read(origin + "/", 2 * 1024 * 1024)
        page = page_signals(content.decode("utf-8", "replace")) if state == "found" and content else None
        checks["structured_data"] = {"label": "schema.org structured data", "present": bool(page and page["structured_data"]),
                                     "meaning": "Facts on the page labelled for machines (organisation, product, article...).",
                                     "note": ", ".join(page["structured_data"][:6]) if page else note}
        checks["feed"] = {"label": "RSS or Atom feed", "present": bool(page and page["feeds"]), "meaning": "New content announced in a feed agents can follow.",
                          "note": ""}
        sitemap = bool(rules and rules["sitemaps"])
        if not sitemap:
            state, content, _note = self._read(origin + "/sitemap.xml", 64 * 1024)
            sitemap = state == "found" and any(tag in (content or b"")[:2000] for tag in (b"<urlset", b"<sitemapindex"))
        checks["sitemap"] = {"label": "Sitemap", "present": sitemap, "meaning": "A list of the site's pages for crawlers.", "note": ""}
        ready = [key for key in SCORED if checks[key]["present"]]
        position = "no stated position on AI crawlers"
        if rules:
            blocked = rules["blocked"]
            position = ("blocks every AI agent checked" if len(blocked) == len(AI_AGENTS) else
                        f"blocks {len(blocked)} of {len(AI_AGENTS)} AI agents ({', '.join(blocked[:5])}{'…' if len(blocked) > 5 else ''})"
                        if blocked else "allows every AI agent checked")
        policy = crawler_policy(rules)
        return {"web4_view": True, "site": origin, "title": (page or {}).get("title", ""), "checks": checks, "details": details,
                "policy": policy, "recommendations": recommendations(checks, rules),
                "previews": previews, "robots": rules, "page": page, "ready": ready, "score": len(ready), "out_of": len(SCORED),
                "ai_position": position, "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "definition": "\"Web 4.0\" has no agreed definition. HAYAT uses it for the agentic web: sites that publish "
                              "machine-readable surfaces for AI agents. These checks are public conventions and proposals, "
                              "not a standard, and the score counts what is published, not its quality.",
                "basis": "Read with HAYAT's own user agent, honouring robots.txt; nothing was submitted and no account was used."}


#: What each AI agent's purpose amounts to, for the policy summary.
PURPOSES = {"model training": "training", "Gemini training": "training", "open web archive": "training",
            "search answers": "search", "assistant answers": "search", "fetching for a user": "on request"}


def crawler_policy(rules: dict[str, Any] | None) -> dict[str, Any]:
    """How the site treats AI by purpose: training, search answers, and fetching a page a person asked for."""
    if not rules:
        return {"summary": "No robots.txt rules, so nothing is stated for AI crawlers.", "by_purpose": {}}
    groups: dict[str, list[bool]] = {}
    for agent in rules["agents"]:
        groups.setdefault(PURPOSES.get(agent["purpose"], "other"), []).append(agent["allowed"])
    words = {}
    for purpose, allowed in groups.items():
        words[purpose] = "allowed" if all(allowed) else "blocked" if not any(allowed) else "mixed"
    summary = "; ".join(f"{purpose}: {state}" for purpose, state in words.items())
    return {"summary": "AI " + summary + ".", "by_purpose": words}


def recommendations(checks: dict[str, dict[str, Any]], rules: dict[str, Any] | None) -> list[str]:
    """What a site owner could add for agents, most useful first (only what is missing)."""
    advice = []
    if not checks["robots"]["present"]:
        advice.append("Publish a robots.txt, even a short one: it is where crawlers, including AI ones, look first.")
    if not checks["ai_rules"]["present"]:
        advice.append("Say what AI crawlers may do: name GPTBot, ClaudeBot or Google-Extended in robots.txt, or add a Content-Signal "
                      "line (search=yes, ai-train=no).")
    if not checks["llms"]["present"]:
        advice.append("Add /llms.txt: a short Markdown page listing the site's key pages for language models.")
    if not checks["structured_data"]["present"]:
        advice.append("Mark up key facts with schema.org JSON-LD (Organization, Product, Article) so agents read them reliably.")
    if not checks["sitemap"]["present"]:
        advice.append("Publish a sitemap and list it in robots.txt.")
    if not checks["agent_card"]["present"] and not checks["mcp"]["present"]:
        advice.append("If the site offers a service, describe it for agents: an A2A agent card or an MCP server lets an assistant "
                      "use it directly.")
    if not checks["security"]["present"]:
        advice.append("Add /.well-known/security.txt with a contact for security reports.")
    return advice


def host_of(site: str) -> str:
    return urlsplit(normalise(site)).hostname or ""
