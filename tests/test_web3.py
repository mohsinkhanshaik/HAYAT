"""Web3 (read-only chain data, explorer, DeFi) and Web 4.0 (agent-web readiness): data, chat, replies and the window."""
import json
import time
from types import SimpleNamespace

import httpx
import pytest

from app.web3 import Web3Data, classify
from app.web3_chat import TOOL_NAMES, direct_plan, report, web_request
from app.web4 import AgentWebInspector, normalise, page_signals, robots_rules, soft_404

NOW = int(time.time())
ADDRESS = "bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh"
ETH = "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"
ETH_TX = "0x" + "ab" * 32
BTC_TX = "cd" * 32
STATS = {"gas_prices": {"slow": .1, "average": .22, "fast": 1.7}, "average_block_time": 12000, "transactions_today": "1597320",
         "network_utilization_percentage": 49.3, "total_blocks": "26172878", "total_addresses": "739954404", "coin_price": "2542.15"}
PAYLOADS = {
    "mempool.space/api/v1/fees/recommended": {"fastestFee": 3, "halfHourFee": 2, "hourFee": 1, "economyFee": 1},
    "mempool.space/api/mempool": {"count": 70433, "vsize": 37491632},
    "mempool.space/api/blocks/tip/height": 971019,
    "mempool.space/api/v1/difficulty-adjustment": {"progressPercent": 65.6, "difficultyChange": 5.04, "remainingBlocks": 693,
                                                   "estimatedRetargetDate": (NOW + 400000) * 1000},
    "mempool.space/api/v1/mining/hashrate/1w": {"hashrates": [{"timestamp": NOW, "avgHashrate": 1.0286e21}]},
    "mempool.space/api/v1/mining/pools/1w": {"pools": [{"name": "Foundry USA", "blockCount": 287}, {"name": "AntPool", "blockCount": 209}]},
    "mempool.space/api/v1/lightning/statistics/latest": {"latest": {"node_count": 16017, "channel_count": 30303,
                                                                   "total_capacity": 364700000000, "added": "2026-10-12T00:00:00Z"}},
    "mempool.space/api/v1/blocks": [{"height": 971019 - index, "id": f"{index:064x}", "timestamp": NOW - 600 * index, "tx_count": 6436,
                                     "size": 1560000, "extras": {"totalFees": 460000, "medianFee": .3, "pool": {"name": "Binance Pool"}}}
                                    for index in range(12)],
    f"mempool.space/api/address/{ADDRESS}": {"chain_stats": {"funded_txo_sum": 500000000, "spent_txo_sum": 107678249, "tx_count": 900},
                                             "mempool_stats": {"funded_txo_sum": 0, "spent_txo_sum": 0, "tx_count": 0}},
    f"mempool.space/api/address/{ADDRESS}/txs": [{"txid": BTC_TX, "fee": 862, "status": {"confirmed": True, "block_height": 970947,
                                                                                      "block_time": NOW - 3600},
                                                  "vin": [{"prevout": {"scriptpubkey_address": "1other", "value": 80000}}],
                                                  "vout": [{"scriptpubkey_address": ADDRESS, "value": 73974}]}],
    f"mempool.space/api/tx/{BTC_TX}": {"fee": 862, "weight": 561, "status": {"confirmed": True, "block_height": 970947, "block_time": NOW - 3600},
                                       "vin": [{"prevout": {"scriptpubkey_address": "1other", "value": 80000}}],
                                       "vout": [{"scriptpubkey_address": ADDRESS, "value": 73974}, {"scriptpubkey_address": "1change", "value": 5164}]},
    "eth.blockscout.com/api/v2/stats": STATS,
    "explorer.optimism.io/api/v2/stats": {**STATS, "average_block_time": 2000},
    "zksync.blockscout.com/api/v2/stats": {**STATS, "coin_price": None},
    "eth.blockscout.com/api/v2/blocks?type=block": {"items": [{"height": 26173058 - index, "hash": "0x1", "timestamp": "2026-10-12T01:20:35Z",
                                                               "transactions_count": 223, "gas_used_percentage": 34.8,
                                                               "burnt_fees": "1323513869178060", "miner": {"hash": "0x4838"}} for index in range(12)]},
    "eth.blockscout.com/api/v2/search?q=vitalik.eth": {"items": [{"type": "token", "name": "x"},
                                                                 {"type": "ens_domain", "name": "vitalik.eth", "address_hash": ETH}]},
    f"eth.blockscout.com/api/v2/addresses/{ETH}": {"hash": ETH, "coin_balance": "5768698980403065000", "is_contract": False,
                                                    "ens_domain_name": "vitalik.eth"},
    f"eth.blockscout.com/api/v2/addresses/{ETH}/transactions": {"items": [{"hash": ETH_TX, "timestamp": "2026-10-11T14:29:35Z", "status": "ok",
                                                                           "block_number": 26169813, "value": "1000000000000",
                                                                           "from": {"hash": "0x930f"}, "to": {"hash": ETH}, "method": None,
                                                                           "fee": {"value": "3166702766450"}}]},
    f"eth.blockscout.com/api/v2/transactions/{ETH_TX}": {"status": "ok", "block_number": 26169813, "timestamp": "2026-10-11T14:29:35Z",
                                                          "confirmations": 3247, "from": {"hash": "0x930f"}, "to": {"hash": ETH},
                                                          "value": "1000000000000", "fee": {"value": "3166702766450"}, "method": "transfer",
                                                          "gas_price": "100000000",
                                                          "token_transfers": [{"token": {"symbol": "USDC"}, "from": {"hash": "0x930f"},
                                                                               "to": {"hash": ETH}, "total": {"value": "2500000", "decimals": "6"}}]},
    "mempool.space/api/v1/block/" + "00" * 32: {"height": 900000, "timestamp": NOW - 86400, "tx_count": 1562, "size": 1920000,
                                                 "extras": {"totalFees": 1790000, "pool": {"name": "ViaBTC"}, "medianFee": 2, "reward": 314290000}},
    "api.llama.fi/overview/dexs?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true": {
        "total24h": 5.6e9, "total7d": 38e9, "change_1d": 9.17, "protocols": [
            {"displayName": "Uniswap V4", "total24h": 7.5e8, "change_1d": 3.2, "chains": ["Ethereum", "Base"]},
            {"name": "curve", "displayName": "Curve", "total24h": 1.2e8, "change_1d": -4.0, "chains": ["Ethereum"]},
            {"displayName": "Dead DEX", "total24h": 0}]},
    f"eth.blockscout.com/api/v2/addresses/{ETH}/token-balances": [
        {"token": {"symbol": "ENS", "name": "Ethereum Name Service", "decimals": "18", "exchange_rate": "20", "type": "ERC-20",
                   "reputation": "ok", "address_hash": "0xens", "circulating_market_cap": "700000000"}, "value": "360000000000000000000"},
        {"token": {"symbol": "AIRDROP", "name": "Free Money", "decimals": "18", "exchange_rate": "1", "type": "ERC-20",
                   "reputation": "ok", "address_hash": "0xair", "circulating_market_cap": "1000000"}, "value": "500000000000000000000000"},
        {"token": {"symbol": "SPAM", "name": "Visit scam", "decimals": "18", "type": "ERC-20", "reputation": "scam"}, "value": "1"},
        {"token": {"symbol": "NOPRICE", "name": "Unpriced", "decimals": "6", "type": "ERC-20", "reputation": "ok"}, "value": "1000000"}],
    "api.llama.fi/v2/chains": [{"name": "Ethereum", "tvl": 51.5e9, "tokenSymbol": "ETH"}, {"name": "Base", "tvl": 6.25e9},
                               {"name": "Solana", "tvl": 6.2e9, "tokenSymbol": "SOL"}, {"name": "Dead", "tvl": 0}],
    "stablecoins.llama.fi/stablecoins?includePrices=false": {"peggedAssets": [
        {"name": "Tether", "symbol": "USDT", "circulating": {"peggedUSD": 184e9}, "pegMechanism": "fiat-backed", "chains": ["a"] * 130},
        {"name": "USD Coin", "symbol": "USDC", "circulating": {"peggedUSD": 73e9}, "pegMechanism": "fiat-backed", "chains": ["a"] * 157},
        {"name": "Euro", "symbol": "EURC", "circulating": {"peggedEUR": 1e8}}]},
}


class Fetcher:
    def __init__(self, payloads=PAYLOADS, down=()):
        self.payloads, self.down, self.calls = payloads, set(down), []

    def fetch(self, url, max_bytes=0, accept=None, **_kwargs):
        self.calls.append(url)
        key = url.split("://", 1)[1]
        if any(host in url for host in self.down):
            raise ConnectionError("offline")
        if key == "mempool.space/api/block-height/900000":
            return SimpleNamespace(content=("00" * 32).encode())
        if key not in self.payloads:
            raise AssertionError("unexpected " + url)
        value = self.payloads[key]
        return SimpleNamespace(content=value if isinstance(value, bytes) else json.dumps(value).encode())


@pytest.fixture
def web3():
    return Web3Data(Fetcher())


def test_queries_are_classified_by_their_shape():
    assert classify(ADDRESS) == ("address", ADDRESS, "bitcoin") and classify(ETH) == ("address", ETH, "ethereum")
    assert classify(ETH_TX)[:2] == ("transaction", ETH_TX) and classify(BTC_TX) == ("transaction", BTC_TX, "bitcoin")
    assert classify("Vitalik.ETH") == ("ens", "vitalik.eth", "ethereum")
    assert classify("ethereum block 20000000") == ("block", "20000000", "ethereum") and classify("900000") == ("block", "900000", "bitcoin")
    with pytest.raises(ValueError):
        classify("hello there")


def test_networks_cover_bitcoin_and_three_evm_chains_and_name_the_refused_ones(web3):
    value = web3.networks()
    assert [item["key"] for item in value["networks"]] == ["bitcoin", "ethereum", "optimism", "zksync"]
    bitcoin = value["networks"][0]
    assert bitcoin["hashrate_eh_s"] == 1028.6 and bitcoin["difficulty"]["expected_change_pct"] == 5.04
    assert bitcoin["lightning"]["capacity_btc"] == 3647 and bitcoin["top_pools"][0] == {"name": "Foundry USA", "blocks": 287, "share_pct": 57.9}
    assert value["networks"][2]["block_time_seconds"] == 2.0 and value["networks"][1]["transactions_today"] == 1597320
    assert {item["network"] for item in value["refused"]} == {"Base", "Arbitrum", "Polygon"} and "no wallet" in value["basis"]


def test_one_network_down_leaves_the_others(tmp_path):
    value = Web3Data(Fetcher(down={"zksync"})).networks()
    assert len(value["networks"]) == 3 and value["gaps"][0].startswith("zkSync Era")
    with pytest.raises(ValueError):
        Web3Data(Fetcher(down={"mempool", "blockscout", "optimism"})).networks()


def test_blocks_lookups_and_defi(web3):
    assert web3.blocks("bitcoin", 3)["blocks"][0] == {"height": 971019, "hash": "0" * 64, "at": web3.blocks("bitcoin")["blocks"][0]["at"],
                                                      "transactions": 6436, "size_mb": 1.56, "fees_btc": .0046, "median_fee_sat_vb": .3,
                                                      "miner": "Binance Pool"}
    assert web3.blocks("ethereum", 12)["blocks"][0]["burnt_eth"] == pytest.approx(.00132351, rel=1e-5)
    address = web3.lookup(ADDRESS)
    assert address["balance"] == 3.92321751 and address["transactions"][0]["change"] == 0.00073974
    tx = web3.lookup(BTC_TX)
    assert tx["confirmations"] == 73 and tx["fee_rate_sat_vb"] == pytest.approx(6.1, abs=.1) and tx["output_count"] == 2
    named = web3.lookup("vitalik.eth")
    assert named["ens"] == "vitalik.eth" and named["balance"] == pytest.approx(5.7687, abs=1e-4) and named["title"] == "Ethereum address"
    assert [(item["symbol"], item["usd"]) for item in named["tokens"]] == [("ENS", 7200.0)] and named["tokens_hidden"] == 3
    evm = web3.lookup(ETH_TX)
    assert evm["confirmations"] == 3247 and evm["token_transfers"][0] == {"token": "USDC", "from": "0x930f", "to": ETH, "amount": 2.5}
    block = web3.lookup("bitcoin block 900000")
    assert block["miner"] == "ViaBTC" and block["reward"] == 3.1429
    defi = web3.defi(5)
    assert [item["name"] for item in defi["chains"]] == ["Ethereum", "Base", "Solana"] and defi["chain_count"] == 3
    assert [item["symbol"] for item in defi["stablecoins"]] == ["USDT", "USDC"] and defi["stablecoin_supply_usd"] == 257e9
    assert [item["name"] for item in defi["dex"]["top"]] == ["Uniswap V4", "Curve"] and defi["dex"]["change_1d_pct"] == 9.17
    calls = len(web3.fetcher.calls)
    web3.defi(5)
    assert len(web3.fetcher.calls) == calls  # cached, the public APIs are not hammered


def test_chat_questions_route_to_the_read_only_tools_and_money_still_stops():
    from app.safety.money_requests import money_request
    cases = {"look up vitalik.eth": ("web3_lookup", {"query": "vitalik.eth"}), ADDRESS: ("web3_lookup", {"query": ADDRESS}),
             "show me ethereum block 20000000": ("web3_lookup", {"query": "ethereum block 20000000"}),
             "latest bitcoin blocks": ("web3_blocks", {"chain": "bitcoin"}), "recent eth blocks": ("web3_blocks", {"chain": "ethereum"}),
             "bitcoin hashrate": ("web3_networks", {}), "layer 2 gas": ("web3_networks", {}), "lightning network": ("web3_networks", {}),
             "how much is locked in defi?": ("web3_defi", {}), "biggest stablecoins": ("web3_defi", {}),
             "is stripe.com ready for AI agents?": ("web4_inspect", {"site": "stripe.com"}),
             "does nytimes.com block AI crawlers?": ("web4_inspect", {"site": "nytimes.com"}),
             "web 4.0 check for github.com": ("web4_inspect", {"site": "github.com"})}
    for text, (tool, arguments) in cases.items():
        assert web_request(text) == {"tool": tool, "arguments": arguments}, text
    for text in ("check 2024", "what is web 4.0?", "eth gas", "tell me about ethereum"):
        assert web_request(text) is None, text
    assert money_request("send 1 eth to vitalik.eth") and web_request("send 1 eth to vitalik.eth") is None
    assert direct_plan("latest bitcoin blocks", TOOL_NAMES)["tool"] == "web3_blocks" and direct_plan("latest bitcoin blocks", ()) is None


def history(tool, output):
    return [{"tool": tool, "result": {"success": True, "output": output}, "verification": {"passed": True}}]


def test_replies_are_written_from_the_data(web3):
    text = report(history("web3_networks", web3.networks()))
    assert "Bitcoin: block 971,019" in text and "Lightning: 16,017 nodes" in text and "Not read: Base, Arbitrum, Polygon" in text
    assert "Optimism: gas" in text and "never signs" in text
    text = report(history("web3_lookup", web3.lookup("vitalik.eth")))
    assert "Ethereum address vitalik.eth" in text and "Balance 5.7687 ETH" in text
    assert "DeFi: $63.95 B locked across 3 chains" in report(history("web3_defi", web3.defi()))
    assert "Latest Bitcoin blocks" in report(history("web3_blocks", web3.blocks("bitcoin")))
    assert report(history("market_quote", {})) is None


# ------------------------------------------------------------------ Web 4.0
ROBOTS = """User-agent: GPTBot
Disallow: /

User-agent: ClaudeBot
Allow: /

User-agent: *
Disallow: /private
Content-Signal: search=yes, ai-train=no
Sitemap: https://example.org/sitemap.xml
"""
HOME = """<html><head><title>Example Org</title><meta name="robots" content="index, noai">
<link rel="alternate" type="application/rss+xml" href="/feed">
<script type="application/ld+json">{"@context": "https://schema.org", "@graph": [{"@type": "Organization"}, {"@type": "WebSite"}]}</script>
</head></html>"""


class Site:
    def __init__(self, files, refused=()):
        self.files, self.refused, self.calls = files, set(refused), []

    def fetch(self, url, max_bytes=0, accept=None, **_kwargs):
        self.calls.append(url)
        path = "/" + url.split("/", 3)[3] if url.count("/") >= 3 else "/"
        if path in self.refused:
            raise PermissionError("robots.txt disallows retrieval")
        if path not in self.files:
            raise httpx.HTTPStatusError("404", request=None, response=SimpleNamespace(status_code=404))
        return SimpleNamespace(content=self.files[path].encode())


def test_robots_rules_page_signals_and_soft_404s():
    rules = robots_rules(ROBOTS)
    by_agent = {item["agent"]: item for item in rules["agents"]}
    assert not by_agent["GPTBot"]["allowed"] and by_agent["ClaudeBot"]["allowed"] and by_agent["ClaudeBot"]["named"]
    assert by_agent["CCBot"]["allowed"] and not by_agent["CCBot"]["named"]
    assert rules["content_signals"] == {"search": "yes", "ai-train": "no"} and rules["sitemaps"] == ["https://example.org/sitemap.xml"]
    page = page_signals(HOME)
    assert page["structured_data"] == ["Organization", "WebSite"] and page["feeds"] == 1 and page["noai"] == ["noai"]
    assert soft_404(b"<!DOCTYPE html><html>", "text") and soft_404(b"[1, 2]", "json") and not soft_404(b"# Title", "text")
    assert normalise("Example.org/path") == "https://example.org"
    with pytest.raises(ValueError):
        normalise("not a site")


def test_a_site_is_checked_for_every_agent_surface_honouring_robots():
    site = Site({"/robots.txt": ROBOTS, "/": HOME, "/llms.txt": "# Example Org\n\n- [Docs](https://example.org/docs)\n",
                 "/.well-known/agent-card.json": json.dumps({"name": "Example agent", "skills": [{"id": "a"}, {"id": "b"}]}),
                 "/.well-known/mcp.json": "<!doctype html><html>not found</html>"},
                refused={"/.well-known/security.txt"})
    value = AgentWebInspector(site).inspect("https://example.org/anything")
    checks = value["checks"]
    assert value["site"] == "https://example.org" and value["title"] == "Example Org"
    assert checks["llms"]["present"] and value["details"]["llms"]["title"] == "Example Org" and value["details"]["llms"]["links"] == 1
    assert checks["agent_card"]["present"] and value["details"]["agent_card"]["skills"] == 2
    assert not checks["mcp"]["present"] and "ordinary web page" in checks["mcp"]["note"]
    assert not checks["security"]["present"] and "robots.txt" in checks["security"]["note"]
    assert checks["ai_rules"]["present"] and checks["sitemap"]["present"] and checks["feed"]["present"] and checks["structured_data"]["present"]
    assert value["score"] == 7 and value["out_of"] == 8 and "blocks 1 of 12 AI agents (GPTBot)" == value["ai_position"]
    assert "no agreed definition" in value["definition"]
    assert value["policy"]["by_purpose"]["training"] == "mixed" and value["recommendations"] == [
        "Add /.well-known/security.txt with a contact for security reports."]
    text = report(history("web4_inspect", value))
    assert "7 of 8 agent-web surfaces" in text and "✓ llms.txt" in text and "Content signals: search=yes, ai-train=no" in text


def test_a_bare_site_scores_zero_and_says_it_has_no_position():
    value = AgentWebInspector(Site({})).inspect("example.com")
    assert value["score"] == 0 and value["ai_position"] == "no stated position on AI crawlers"


# ------------------------------------------------------------------ the window
def test_the_web3_window_reads_every_tab_and_follows_clicks(shared_tk):
    from app.gui_theme import Palette
    from app.gui_web3 import Web3Center
    site = Site({"/robots.txt": ROBOTS, "/": HOME, "/llms.txt": "# Example Org\n"})

    class GUI(Palette):
        pass
    gui = GUI()
    gui.root, gui.runtime = shared_tk, SimpleNamespace(web3=Web3Data(Fetcher()), agent_web=AgentWebInspector(site))
    window = Web3Center(gui)

    def wait(ready, seconds=10):
        deadline = time.time() + seconds
        while time.time() < deadline and not ready():
            shared_tk.update()
            time.sleep(.02)
        return ready()
    try:
        window.geometry("1400x880-4000-4000")
        assert wait(lambda: window.networks_data and window.block_data["bitcoin"] and window.block_data["ethereum"])
        shared_tk.update()
        window.draw_blocks("bitcoin")
        assert set(window.card_widgets) == {"bitcoin", "ethereum", "optimism", "zksync"} and "Not read: Base" in window.refused_note.cget("text")
        assert window.card_widgets["bitcoin"]["big"].cget("text") == "block 971,019" and len(window.fee_history) == 1
        # A click on a block opens it in the explorer.
        left, _right, height = window._block_tiles["bitcoin"][0]
        window.block_clicked("bitcoin", SimpleNamespace(x=left + 3))
        assert window.tabs.index("current") == 1 and window.query.get() == f"bitcoin block {height}"
        window.look_up("vitalik.eth")
        assert wait(lambda: window.lookup_data and window.lookup_data.get("ens"))
        assert "Balance 5.7687 ETH" in window.summary.cget("text") and len(window.rows.get_children()) == 2
        assert window.rows.get_children()[0] == "holding|0|0xens"  # tokens held come first
        window.rows.selection_set(next(row for row in window.rows.get_children() if row.startswith("tx|")))
        window.follow()
        assert wait(lambda: window.lookup_data["kind"] == "transaction")
        assert any("USDC" in str(window.rows.item(row, "values")) for row in window.rows.get_children())
        window.step(-1)
        assert window.query.get() == "vitalik.eth" and window.forward_button.instate(["!disabled"])
        window.look_up("hello there")
        assert wait(lambda: "Not found" in window.summary.cget("text"))
        # DeFi refreshes when its tab is shown.
        window.tabs.select(2)
        assert wait(lambda: window.defi_data)
        shared_tk.update()
        window.draw_tvl()
        assert len(window._tvl_bars) == 3 and "$63.95 B locked" in window.defi_note.cget("text") and len(window.stables.get_children()) == 2
        window.tvl_hover(SimpleNamespace(x=200, y=window._tvl_bars[0][0] + 4))
        assert window.tvl.find_withtag("hover")
        # The agent-web check.
        window.tabs.select(3)
        window.inspect("example.org")
        assert wait(lambda: window.inspect_data)
        assert "AI crawlers: blocks 1 of 12" in window.verdict.cget("text") and window.checks.exists("llms")
        window.checks.selection_set("robots")
        window.check_selected()
        assert "Blocked: GPTBot" in window.preview.get("1.0", "end")
        blocked = [row for row in window.crawlers.get_children() if "blocked" in window.crawlers.item(row, "values")[2]]
        assert len(blocked) == 1
        import tkinter as tk
        from tkinter import ttk
        buttons = [str(widget.cget("text")).lower() for widget in window.winfo_children()[0].master.winfo_children()
                   if isinstance(widget, (tk.Button, ttk.Button))]
        assert not any(word in label for label in buttons for word in ("send", "sign", "connect wallet", "buy"))
    finally:
        window.close()
