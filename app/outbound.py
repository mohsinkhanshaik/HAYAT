"""What left this computer for one reply, in plain words.

Venice.ai labels each model with how private it is. HAYAT's model runs on the owner's own machine, but
some replies still send things out: a web search, the pages it reads, a message, a post. Until now nothing said
so per reply; the transfer log (app/transfers.py) held the pages but not which reply caused them, and not the
search words at all.

:func:`summary` gathers, for one turn (or one background research job), from records that already exist:

* **searches:** the words sent to each search engine (``TransferLog.search``, called by the search chain);
* **pages:** every request the public fetcher made, by site (``TransferLog.web``, now tagged with its turn);
* **actions:** tools that reach outside in a category of their own: messages, posts, form submissions, sign-ins,
  connector and MCP writes (``TransferLog.action``, called by the tool registry);
* **the model:** where the reply model runs, read from its address: "on this computer" only for a loopback
  address, otherwise the host it was sent to.

:func:`describe` says it in a line or two: "Answered on this computer; nothing was sent out." or "Sent out: 2 web
searches ("…", "…"); 6 pages from 4 sites (en.wikipedia.org ×2, …)." Nothing here sends anything, and search
words stay in this session's memory only, like the rest of the web log.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

#: Tool categories that act outside this computer beyond reading public pages.
ACTION_CATEGORIES = frozenset({
    "send_message", "external_post", "submit_form", "login_account", "connector_write", "mcp_write",
    "browser_click", "browser_type", "public_download", "browser_download", "install_package_project",
    "install_system_software",
})
_LOOPBACK = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def model_location(model: Any) -> str:
    """Where the reply model runs, from its own address; empty when it cannot be told."""
    for attribute in ("base_url", "url", "endpoint", "host"):
        value = str(getattr(model, attribute, "") or "")
        if value:
            host = urlsplit(value if "://" in value else "http://" + value).hostname or ""
            if host:
                return "this computer" if host in _LOOPBACK else host
    return ""


def summary(*, turn_id: str | None = None, task_id: str | None = None, model: Any = None) -> dict[str, Any]:
    from app.transfers import transfers

    log = transfers()
    rows = [row for row in log.web_recent(limit=500) + log.outbound_recent(limit=200)
            if (turn_id and row.get("turn") == turn_id) or (task_id and row.get("task") == task_id)]
    searches: list[dict[str, Any]] = []
    sites: dict[str, int] = {}
    actions: list[str] = []
    for row in rows:
        kind = row.get("kind")
        if kind == "search":
            entry = next((item for item in searches if item["query"] == row.get("query")), None)
            if entry is None:
                searches.append({"query": row.get("query", ""), "engines": list(row.get("engines") or [])})
            else:
                entry["engines"] = sorted(set(entry["engines"]) | set(row.get("engines") or []))
        elif kind == "action":
            actions.append(row.get("what", ""))
        elif row.get("host"):
            sites[row["host"]] = sites.get(row["host"], 0) + 1
    where = model_location(model) if model is not None else ""
    return {"searches": searches, "sites": dict(sorted(sites.items(), key=lambda item: (-item[1], item[0]))),
            "pages": sum(sites.values()), "actions": actions, "model_runs_on": where,
            "nothing_sent": not (searches or sites or actions) and where in ("this computer", "")}


def describe(report: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    where = report.get("model_runs_on")
    if report.get("nothing_sent"):
        lines.append("Answered on this computer; nothing was sent out." if where == "this computer"
                     else "Nothing was sent out for this reply.")
        return lines
    if where and where != "this computer":
        lines.append(f"The reply model runs at {where}: what you wrote was sent there.")
    elif where == "this computer":
        lines.append("The reply model ran on this computer.")
    parts = []
    searches = report.get("searches") or []
    if searches:
        shown = ", ".join(f"“{item['query'][:60]}”" for item in searches[:3])
        more = f" and {len(searches) - 3} more" if len(searches) > 3 else ""
        parts.append(f"{len(searches)} web search{'es' if len(searches) != 1 else ''} ({shown}{more})")
    sites = report.get("sites") or {}
    if sites:
        named = ", ".join(f"{host}{f' ×{count}' if count > 1 else ''}" for host, count in list(sites.items())[:4])
        more = f", +{len(sites) - 4} more" if len(sites) > 4 else ""
        parts.append(f"{report['pages']} page{'s' if report['pages'] != 1 else ''} from {len(sites)} "
                     f"site{'s' if len(sites) != 1 else ''} ({named}{more})")
    actions = report.get("actions") or []
    if actions:
        parts.append("; ".join(actions[:3]) + (f"; +{len(actions) - 3} more" if len(actions) > 3 else ""))
    if parts:
        lines.append("Sent out: " + "; ".join(parts) + ".")
    return lines


def action_text(tool: str, category: str, arguments: dict[str, Any]) -> str:
    """One line for an outward action, naming where it went but never what it said."""
    target = ""
    for key in ("url", "address", "site"):
        if isinstance(arguments.get(key), str) and arguments[key]:
            target = urlsplit(arguments[key]).hostname or ""
            break
    recipients = arguments.get("to") or arguments.get("recipients") or arguments.get("recipient")
    if isinstance(recipients, (list, tuple)):
        target = f"{len(recipients)} recipient{'s' if len(recipients) != 1 else ''}"
    elif isinstance(recipients, str) and recipients:
        target = "1 recipient"
    platforms = arguments.get("platforms") or arguments.get("platform")
    if platforms and not target:
        target = ", ".join(platforms) if isinstance(platforms, (list, tuple)) else str(platforms)
    words = {"send_message": "sent a message", "external_post": "posted", "submit_form": "submitted a form",
             "login_account": "signed in", "connector_write": "wrote to a connected service",
             "mcp_write": "wrote through an MCP server", "browser_click": "clicked in the browser",
             "browser_type": "typed in the browser", "public_download": "downloaded a file",
             "browser_download": "downloaded a file", "install_package_project": "installed a package",
             "install_system_software": "installed software"}.get(category, category.replace("_", " "))
    return f"{tool}: {words}" + (f" ({target})" if target else "")
