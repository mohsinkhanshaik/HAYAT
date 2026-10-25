"""Chat and tools for Web3 (read-only chain data) and Web 4.0 (how ready a site is for AI agents).

Questions such as "look up vitalik.eth", "latest bitcoin blocks", "how much is locked in DeFi" or
"is stripe.com ready for AI agents" are read from the sources, never recalled or guessed, and the
reply is written from the data. Nothing here signs, sends or logs in.
"""
from __future__ import annotations

import re
from typing import Any

from app.web3 import BTC_ADDRESS, BTC_TX, ENS, EVM_ADDRESS, EVM_TX

TOOL_NAMES = ("web3_networks", "web3_blocks", "web3_lookup", "web3_defi", "web3_watchlist", "web4_inspect")
_LEAD = r"^(?:(?:hey\s+)?hayat[\s,.!:-]*)?(?:please\s+)?(?:(?:can|could|would)\s+(?:you|u)\s+)?(?:please\s+)?"
_END = r"\s*[?.!]*$"
_SITE = r"(?P<site>(?:https?://)?[a-z0-9-]+(?:\.[a-z0-9-]+)+(?:/\S*)?)"
_ID = (r"(?P<id>" + EVM_TX.pattern + "|" + EVM_ADDRESS.pattern + "|" + BTC_TX.pattern + "|" + BTC_ADDRESS.pattern
       + "|" + ENS.pattern + r"|(?:(?:bitcoin|btc|ethereum|eth|optimism|zksync)\s+)?block\s+#?\d{1,10})")


def _compile(pattern: str) -> re.Pattern[str]:
    return re.compile(_LEAD + pattern + _END, re.IGNORECASE)


_LOOKUP = _compile(r"(?:(?:look\s*up|lookup|check|show(?:\s+me)?|explore|inspect|find|what(?:'s|\s+is)\s+(?:in|at|the\s+balance\s+of)?|"
                   r"balance\s+of|who\s+(?:is|owns))\s+)?(?:the\s+)?(?:(?:bitcoin|btc|ethereum|eth|wallet|crypto)\s+(?!block\b))?"
                   r"(?:address|wallet|transaction|tx|ens(?:\s+name)?\s+)?\s*" + _ID)
_BLOCKS = _compile(r"(?:show(?:\s+me)?\s+|list\s+|what\s+are\s+)?(?:the\s+)?(?:latest|recent|newest|last)\s+"
                   r"(?P<chain>bitcoin|btc|ethereum|eth|optimism|zksync)\s+blocks(?:\s+(?:now|today))?")
_NETWORKS = _compile(r"(?:show(?:\s+me)?\s+|what(?:'s|\s+is|\s+are)\s+(?:the\s+)?|how\s+(?:is|are)\s+(?:the\s+)?|check\s+(?:the\s+)?)?"
                     r"(?:web\s*3(?:\.0)?\s+networks?|blockchain\s+networks|layer[\s-]*2s?(?:\s+gas|\s+networks)?|l2s?(?:\s+gas)?|"
                     r"lightning\s+network(?:\s+(?:size|stats|capacity))?|bitcoin\s+(?:hashrate|hash\s+rate|mining(?:\s+pools)?|difficulty(?:\s+adjustment)?)|"
                     r"(?:the\s+)?next\s+difficulty\s+adjustment|mining\s+pools|optimism\s+gas|zksync\s+gas)(?:\s+(?:now|today|doing|like))?")
_DEFI = _compile(r"(?:show(?:\s+me)?\s+|what(?:'s|\s+is|\s+are)\s+(?:the\s+)?|how\s+(?:much|big)\s+(?:is|are)\s+(?:the\s+)?|how\s+much\s+(?:money\s+)?is\s+)?"
                 r"(?:defi(?:\s+(?:tvl|stats|total|totals|market|overview))?|total\s+value\s+locked(?:\s+in\s+defi)?|tvl(?:\s+by\s+chain)?|"
                 r"locked\s+in\s+defi|stablecoins?(?:\s+(?:supply|market|market\s+cap))?|biggest\s+stablecoins|"
                 r"which\s+chains?\s+(?:has|have)\s+the\s+most\s+(?:tvl|value\s+locked|defi))(?:\s+(?:now|today))?")
_ADDRESS = (r"(?P<id>" + EVM_ADDRESS.pattern + "|" + BTC_ADDRESS.pattern + "|" + ENS.pattern + ")")
_WATCH_ADD = _compile(r"(?:start\s+)?(?:watch|track|follow|monitor)(?:ing)?\s+(?:the\s+)?(?:(?:bitcoin|btc|ethereum|eth)\s+)?"
                      r"(?:address|wallet\s+address)?\s*" + _ADDRESS + r"(?:\s+as\s+(?P<label>[\w .'-]{1,40}))?")
_WATCH_REMOVE = _compile(r"(?:stop\s+(?:watching|tracking|following|monitoring)|unwatch|remove)\s+(?:the\s+)?(?:address\s+)?" + _ADDRESS
                         + r"(?:\s+from\s+(?:my\s+)?watch(?:ed)?\s*(?:list|addresses))?")
_WATCH_LIST = _compile(r"(?:show|list|what\s+are|check|refresh)\s+(?:me\s+)?(?:my\s+|the\s+)?(?:watched|tracked)\s+(?:addresses|wallets)"
                       r"|(?:show|check)\s+(?:my\s+)?(?:address|wallet)\s+watch\s*list")
_WEB4 = (_compile(r"(?:is|how)\s+" + _SITE + r"\s+(?:ready\s+for|open\s+to|friendly\s+to)\s+(?:ai\s+)?agents"),
         _compile(r"(?:check|inspect|test|scan|rate)\s+" + _SITE + r"\s+(?:for\s+)?(?:web\s*4(?:\.0)?|agent(?:ic)?[\s-]*(?:web|readiness|ready)|ai[\s-]*(?:agents?|readiness|crawlers?))"),
         _compile(r"(?:web\s*4(?:\.0)?|agentic\s+web|agent[\s-]*readiness|ai[\s-]*readiness)\s+(?:check|test|scan|report)?\s*(?:for|of|on)?\s+" + _SITE),
         _compile(r"does\s+" + _SITE + r"\s+(?:have|publish)\s+(?:an?\s+)?(?:llms\.txt|agent\s+card|mcp(?:\s+manifest)?)"),
         _compile(r"does\s+" + _SITE + r"\s+(?:block|allow)\s+(?:ai|gptbot|claudebot|ai\s+crawlers|ai\s+bots|chatgpt|claude)"),
         _compile(r"(?:which|what)\s+ai\s+(?:crawlers|bots|agents)\s+does\s+" + _SITE + r"\s+(?:block|allow)"))


def web_request(text: str) -> dict[str, Any] | None:
    """{"tool", "arguments"} for a Web3 or Web 4.0 question, or None."""
    value = " ".join(str(text or "").split())
    if not value or len(value) > 200:
        return None
    for pattern in _WEB4:
        if match := pattern.match(value):
            return {"tool": "web4_inspect", "arguments": {"site": match["site"].rstrip(".,")}}
    if match := _WATCH_ADD.match(value):
        return {"tool": "web3_watchlist", "arguments": {"action": "add", "query": match["id"],
                                                         **({"label": match["label"].strip()} if match["label"] else {})}}
    if match := _WATCH_REMOVE.match(value):
        return {"tool": "web3_watchlist", "arguments": {"action": "remove", "query": match["id"]}}
    if _WATCH_LIST.match(value):
        return {"tool": "web3_watchlist", "arguments": {"action": "check" if value.lower().startswith(("check", "refresh")) else "list"}}
    if match := _BLOCKS.match(value):
        chain = {"btc": "bitcoin", "eth": "ethereum"}.get(match["chain"].lower(), match["chain"].lower())
        return {"tool": "web3_blocks", "arguments": {"chain": chain}}
    if _DEFI.match(value):
        return {"tool": "web3_defi", "arguments": {}}
    if _NETWORKS.match(value):
        return {"tool": "web3_networks", "arguments": {}}
    if match := _LOOKUP.match(value):
        query = match["id"]
        # A bare number or a word is not an explorer question ("check 2024" stays ordinary).
        if not re.search(r"(?i)block|0x|\.eth$|^[13]|^bc1|^[0-9a-f]{64}$", query):
            return None
        return {"tool": "web3_lookup", "arguments": {"query": query}}
    return None


def direct_plan(text: str, names: Any) -> dict[str, Any] | None:
    found = web_request(text)
    if not found or found["tool"] not in names:
        return None
    what = "a site's readiness for AI agents" if found["tool"] == "web4_inspect" else "public blockchain data"
    return {"description": f"Read {what} and report it with its source and time.", "tool": found["tool"],
            "arguments": found["arguments"], "expected": {"type": "tool_success"}, "max_retries": 0}


def register_web3_tools(registry: Any, web3: Any, inspector: Any) -> None:
    def obj(properties, required=()):
        return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}
    read = " Read-only: HAYAT has no wallet, never signs and never sends a transaction."
    registry.register("web3_networks", "Blockchain networks now: Bitcoin fees, mempool, mining hashrate, pools, the next difficulty "
                      "adjustment and the Lightning network; Ethereum, Optimism and zkSync gas, block time, load and activity." + read,
                      obj({}), "low", "public_api_get", web3.networks)
    registry.register("web3_blocks", "The latest blocks on Bitcoin, Ethereum, Optimism or zkSync: height, time, transactions, fees, "
                      "miner or gas used." + read,
                      obj({"chain": {"enum": ["bitcoin", "ethereum", "optimism", "zksync"]},
                           "limit": {"type": "integer", "minimum": 1, "maximum": 15}}), "low", "public_api_get", web3.blocks)
    registry.register("web3_lookup", "Blockchain explorer: public facts about a Bitcoin or Ethereum address (balance, recent "
                      "transactions), a transaction (status, value, fee, confirmations), a block, or an ENS name such as vitalik.eth."
                      + read,
                      obj({"query": {"type": "string", "minLength": 3, "maxLength": 120},
                           "chain": {"enum": ["", "bitcoin", "ethereum", "optimism", "zksync"]}}, ["query"]),
                      "low", "public_api_get", web3.lookup)
    registry.register("web3_defi", "DeFi totals: value locked per blockchain and the largest stablecoins by supply (DefiLlama)." + read,
                      obj({"limit": {"type": "integer", "minimum": 1, "maximum": 40}}), "low", "public_api_get", web3.defi)
    registry.register("web3_watchlist", "The owner's local list of watched blockchain addresses: list, add (an address or ENS "
                      "name, with an optional label), remove, or check balances now and say what changed." + read,
                      obj({"action": {"enum": ["list", "add", "remove", "check"]}, "query": {"type": "string", "maxLength": 120},
                           "label": {"type": "string", "maxLength": 60}}), "low", "filesystem_write_project", web3.watchlist)
    registry.register("web4_inspect", "Web 4.0 / agentic web check of a website: its rules for AI crawlers (GPTBot, ClaudeBot and "
                      "others), llms.txt, A2A agent card, MCP manifest, structured data, feeds and sitemap. Reads public files only, "
                      "honouring robots.txt.",
                      obj({"site": {"type": "string", "minLength": 3, "maxLength": 200}}, ["site"]),
                      "low", "public_web_read", inspector.inspect)


# ---------------------------------------------------------------------- replies
def amount(value: Any, unit: str = "") -> str:
    if value is None:
        return "—"
    digits = 8 if abs(value) < 1 else 4 if abs(value) < 1000 else 2
    return f"{value:,.{digits}f}".rstrip("0").rstrip(".") + (f" {unit}" if unit else "")


def dollars(value: Any) -> str:
    if value is None:
        return "—"
    for size, suffix in ((1e12, " T"), (1e9, " B"), (1e6, " M")):
        if abs(value) >= size:
            return f"${value / size:,.2f}{suffix}"
    return f"${value:,.0f}"


def _networks(value: dict[str, Any]) -> list[str]:
    lines = []
    for item in value["networks"]:
        if item["key"] == "bitcoin":
            lines.append(f"Bitcoin: block {item['block_height']:,} · fees {item['fee_fast_sat_vb']} sat/vB fastest, "
                         f"{item['fee_1h_sat_vb']} within an hour · {item['mempool_transactions']:,} transactions waiting")
            if item.get("hashrate_eh_s"):
                lines.append(f"  Mining: {item['hashrate_eh_s']:,} EH/s" + (f" · top pools {', '.join(pool['name'] + ' ' + str(pool['share_pct']) + '%' for pool in item.get('top_pools', [])[:3])}"
                                                                         if item.get("top_pools") else ""))
            if item.get("difficulty"):
                difficulty = item["difficulty"]
                lines.append(f"  Next difficulty adjustment: {difficulty['expected_change_pct']:+.2f}% expected in "
                             f"{difficulty['remaining_blocks']:,} blocks (about {str(difficulty['estimated_at'])[:10]})")
            if item.get("lightning"):
                light = item["lightning"]
                lines.append(f"  Lightning: {light['nodes']:,} nodes · {light['channels']:,} channels · {light['capacity_btc']:,} BTC capacity "
                             f"(as of {light['as_of']})")
        else:
            lines.append(f"{item['name']}: gas {item['gas_slow_gwei']} / {item['gas_average_gwei']} / {item['gas_fast_gwei']} gwei "
                         f"(slow/average/fast) · a block every {item['block_time_seconds']} s"
                         + (f" · {item['utilization_pct']:.0f}% of capacity used" if item.get("utilization_pct") is not None else "")
                         + (f" · {item['transactions_today']:,} transactions today" if item.get("transactions_today") else ""))
    if value.get("refused"):
        lines.append("Not read: " + ", ".join(item["network"] for item in value["refused"])
                     + " (their explorers answer automated requests with a browser challenge; HAYAT does not get around it).")
    lines += ["Unavailable: " + gap for gap in value.get("gaps") or []]
    return lines


def _lookup(value: dict[str, Any]) -> list[str]:
    unit = value.get("unit", "")
    title = value["title"] + (f" {value['ens']}" if value.get("ens") else "")
    lines = [f"{title} ({value['chain']}, {value['source']}):"]
    if value["kind"] == "address":
        lines.append(f"  {value['address']}" + (f" · {value['name']}" if value.get("name") else "")
                     + (f" · ENS {value['ens_name']}" if value.get("ens_name") and not value.get("ens") else ""))
        lines.append(f"  Balance {amount(value['balance'], unit)}" + (f" ({amount(value['pending'], unit)} pending)" if value.get("pending") else "")
                     + (f" · {value['transaction_count']:,} transactions" if value.get("transaction_count") else ""))
        if value.get("tokens"):
            lines.append(f"  Tokens Blockscout prices at about {dollars(value['tokens_usd'])}: " + ", ".join(
                f"{item['symbol']} {dollars(item['usd'])}" for item in value["tokens"][:5])
                + (f" ({value['tokens_hidden']} unpriced or flagged tokens hidden)" if value.get("tokens_hidden") else ""))
        for item in value.get("transactions", [])[:5]:
            lines.append(f"  {str(item.get('at') or 'pending')[:16].replace('T', ' ')} · {item['change']:+,.8f} {unit}".rstrip("0").rstrip(".")
                         + f" · {item['hash'][:14]}…")
    elif value["kind"] == "transaction":
        state = ("confirmed" + (f", {value['confirmations']:,} confirmations" if value.get("confirmations") else "")) if value.get("confirmed") \
            else (value.get("status") or "waiting in the mempool")
        lines.append(f"  {state} · block {value.get('block') or '—'} · {str(value.get('at') or '')[:16].replace('T', ' ')} UTC")
        lines.append(f"  Value {amount(value.get('value'), unit)} · fee {amount(value.get('fee'), unit)}"
                     + (f" ({value['fee_rate_sat_vb']} sat/vB)" if value.get("fee_rate_sat_vb") else "")
                     + (f" · method {value['method']}" if value.get("method") else ""))
        if value.get("from"):
            lines.append(f"  From {value['from']} to {value.get('to') or 'a new contract'}")
        for transfer in value.get("token_transfers", [])[:5]:
            lines.append(f"  Token: {amount(transfer['amount'])} {transfer['token'] or ''}")
    else:
        lines.append(f"  {str(value.get('at') or '')[:16].replace('T', ' ')} UTC · {value.get('transaction_count') or 0:,} transactions"
                     + (f" · mined by {value['miner']}" if value.get("miner") else "")
                     + (f" · fees {amount(value['fees'], unit)}" if value.get("fees") is not None else "")
                     + (f" · {value['gas_used_pct']}% gas used" if value.get("gas_used_pct") is not None else ""))
    return lines


def _web4(value: dict[str, Any]) -> list[str]:
    lines = [f"{value['site']}" + (f" ({value['title']})" if value.get("title") else "")
             + f": {value['score']} of {value['out_of']} agent-web surfaces published · {value['ai_position']}."]
    for key, check in value["checks"].items():
        mark = "✓" if check["present"] else "·"
        note = f" ({check['note']})" if check.get("note") and (check["present"] or "refused" in check["note"] or "robots" in check["note"]) else ""
        lines.append(f"  {mark} {check['label']}{note}")
    if (value.get("policy") or {}).get("summary"):
        lines.append("  " + value["policy"]["summary"])
    for advice in (value.get("recommendations") or [])[:3]:
        lines.append("  Could add: " + advice)
    robots = value.get("robots") or {}
    if robots.get("content_signals"):
        lines.append("  Content signals: " + ", ".join(f"{key}={item}" for key, item in robots["content_signals"].items()))
    lines.append(value["definition"])
    return lines


def report(history: list[dict[str, Any]]) -> str | None:
    """Plain text for verified Web3 and Web 4.0 results, written from the data (no model)."""
    sections = []
    for event in history or []:
        if not str(event.get("tool", "")).startswith(("web3_", "web4_")):
            continue
        result = event.get("result") or {}
        if not result.get("success") or not (event.get("verification") or {}).get("passed"):
            continue
        value = result.get("output") or {}
        if value.get("networks_view"):
            lines = _networks(value)
        elif value.get("blocks_view"):
            lines = [f"Latest {value['chain'].title()} blocks:"] + [
                f"  {item['height']:,} · {str(item['at'])[11:16]} UTC · {item.get('transactions') or 0:,} transactions"
                + (f" · {item['miner']}" if item.get("miner") and not str(item["miner"]).startswith("0x") else "")
                + (f" · fees {item['fees_btc']} BTC" if item.get("fees_btc") is not None else "")
                + (f" · {item['gas_used_pct']}% gas used" if item.get("gas_used_pct") is not None else "") for item in value["blocks"][:10]]
        elif value.get("lookup_view"):
            lines = _lookup(value)
        elif value.get("defi_view"):
            lines = [f"DeFi: {dollars(value['total_tvl_usd'])} locked across {value['chain_count']} chains (DefiLlama)."]
            lines += [f"  {item['name']}: {dollars(item['tvl_usd'])} ({item['share_pct']}%)" for item in value["chains"][:8]]
            dex = value.get("dex")
            if dex:
                lines.append(f"Decentralised exchanges: {dollars(dex['volume_24h_usd'])} traded in 24 h"
                             + (f" ({dex['change_1d_pct']:+.1f}% on the day before)" if dex.get("change_1d_pct") is not None else "")
                             + "; largest: " + ", ".join(f"{item['name']} {dollars(item['volume_24h_usd'])}" for item in dex["top"][:5]) + ".")
            if value.get("stablecoins"):
                lines.append(f"Stablecoins: {dollars(value.get('stablecoin_supply_usd'))} in circulation.")
                lines += [f"  {item['symbol']} ({item['name']}): {dollars(item['supply_usd'])} · {item['mechanism']}" for item in value["stablecoins"][:5]]
        elif value.get("watch_view"):
            entries = value["addresses"]
            verb = {"add": "Now watching", "remove": "Removed", "check": "Checked", "list": "Watching"}[value["action"]]
            lines = [f"{verb}: {len(entries)} address{'es' if len(entries) != 1 else ''} on your watch list."]
            for entry in entries[:20]:
                lines.append(f"  {entry['label'] or entry['address'][:14] + '…'} ({entry['chain']}): {amount(entry.get('balance'), entry.get('unit', ''))}"
                             + (f" · {entry['error']}" if entry.get("error") else ""))
            for item in value.get("changed") or []:
                lines.append(f"  Changed: {item['label'] or item['address'][:14]} {item['change']:+,.8f} {item.get('unit', '')}".rstrip())
            if value["action"] == "check" and not value.get("changed"):
                lines.append("  No balance changed since the last check.")
        elif value.get("web4_view"):
            lines = _web4(value)
        else:
            continue
        if value.get("basis") and not value.get("web4_view"):
            lines.append(value["basis"])
        sections.append("\n".join(lines))
    return "\n\n".join(sections) or None
