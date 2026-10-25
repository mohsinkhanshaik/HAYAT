"""Web3 watched addresses, remembered agent-web checks, chain switching and the windows that use them."""
import json
import time
from types import SimpleNamespace

import pytest

from app.web3 import Web3Data
from app.web3_chat import report, web_request
from app.web4 import AgentWebInspector
from tests.test_web3 import ADDRESS, BTC_TX, ETH, HOME, PAYLOADS, ROBOTS, Fetcher, Site, history


def test_watched_addresses_are_local_and_report_balance_changes(tmp_path):
    payloads = dict(PAYLOADS)
    web3 = Web3Data(Fetcher(payloads), tmp_path / "web3.json")
    added = web3.watchlist("add", "vitalik.eth")
    assert [(entry["address"], entry["label"], entry["chain"]) for entry in added["addresses"]] == [(ETH, "vitalik.eth", "ethereum")]
    web3.watchlist("add", ADDRESS, "cold storage")
    web3.watchlist("add", ETH)  # already watched: not added twice
    assert len(web3.watchlist()["addresses"]) == 2 and (tmp_path / "web3.json").exists()
    assert web3.watchlist("check")["changed"] == []
    payloads[f"mempool.space/api/address/{ADDRESS}"] = {"chain_stats": {"funded_txo_sum": 600000000, "spent_txo_sum": 107678249,
                                                                        "tx_count": 901},
                                                        "mempool_stats": {"funded_txo_sum": 0, "spent_txo_sum": 0, "tx_count": 0}}
    web3._cache.clear()
    checked = web3.watchlist("check")
    assert [(item["label"], round(item["change"], 8)) for item in checked["changed"]] == [("cold storage", 1.0)]
    assert checked["addresses"][1]["previous_balance"] == 3.92321751
    with pytest.raises(ValueError):
        web3.watchlist("add", BTC_TX)  # a transaction cannot be watched
    web3.watchlist("remove", "cold storage")
    renamed = json.loads((tmp_path / "web3.json").read_text(encoding="utf-8"))
    renamed["addresses"][0]["label"] = "Vitalik"
    (tmp_path / "web3.json").write_text(json.dumps(renamed), encoding="utf-8")
    assert web3.watchlist("remove", "vitalik.eth")["addresses"] == []  # found by its ENS name after renaming
    with pytest.raises(ValueError):
        web3.watchlist("remove", "nobody")
    with pytest.raises(ValueError):
        Web3Data(Fetcher()).watchlist("add", ETH)  # no data folder, nothing to keep it in


def test_checked_sites_are_remembered_newest_first_without_repeats(tmp_path):
    web3 = Web3Data(Fetcher(), tmp_path / "web3.json")
    for index in range(22):
        web3.remember_site({"site": f"https://site{index}.org", "score": 1, "out_of": 8, "ai_position": "x", "fetched_at": "2026-10-11"})
    web3.remember_site({"site": "https://site5.org", "score": 4, "out_of": 8, "ai_position": "y", "fetched_at": "2026-10-12"})
    sites = web3.sites()
    assert len(sites) == 20 and sites[0]["site"] == "https://site5.org" and sites[0]["score"] == 4
    assert sum(entry["site"] == "https://site5.org" for entry in sites) == 1


def test_watch_phrases_route_and_reply(tmp_path):
    assert web_request("watch vitalik.eth") == {"tool": "web3_watchlist", "arguments": {"action": "add", "query": "vitalik.eth"}}
    assert web_request(f"watch address {ETH} as vitalik") == {"tool": "web3_watchlist",
                                                              "arguments": {"action": "add", "query": ETH, "label": "vitalik"}}
    assert web_request("stop watching vitalik.eth")["arguments"] == {"action": "remove", "query": "vitalik.eth"}
    assert web_request("check my watched addresses")["arguments"] == {"action": "check"}
    assert web_request("show my watched addresses")["arguments"] == {"action": "list"}
    assert web_request("watch mode off") is None and web_request("watch the news") is None
    web3 = Web3Data(Fetcher(), tmp_path / "web3.json")
    text = report(history("web3_watchlist", web3.watchlist("add", "vitalik.eth")))
    assert "Now watching: 1 address on your watch list." in text and "vitalik.eth (ethereum): 5.7687 ETH" in text
    assert "No balance changed since the last check." in report(history("web3_watchlist", web3.watchlist("check")))


def test_the_window_watches_addresses_switches_chains_and_remembers_sites(shared_tk, tmp_path):
    from app.gui_theme import Palette
    from app.gui_web3 import Web3Center
    blocks = [{"height": 158084850 - index, "hash": "0x2", "timestamp": "2026-10-12T01:27:57Z", "transactions_count": 47,
               "gas_used_percentage": 51.4, "burnt_fees": "19106406390", "miner": {"name": "Proxy"}} for index in range(12)]
    payloads = {**PAYLOADS, "explorer.optimism.io/api/v2/blocks?type=block": {"items": blocks}}
    web3 = Web3Data(Fetcher(payloads), tmp_path / "web3.json")
    site = Site({"/robots.txt": ROBOTS, "/": HOME})

    class GUI(Palette):
        pass
    gui = GUI()
    gui.root, gui.runtime = shared_tk, SimpleNamespace(web3=web3, agent_web=AgentWebInspector(site))
    window = Web3Center(gui)

    def wait(ready, seconds=10):
        deadline = time.time() + seconds
        while time.time() < deadline and not ready():
            shared_tk.update()
            time.sleep(.02)
        return ready()
    try:
        window.geometry("1400x880-4000-4000")
        assert wait(lambda: window.block_data["bitcoin"] and window.block_data["ethereum"])
        # The second strip follows the chosen chain; a click opens that chain's block.
        window.evm_chain.set("optimism")
        window.evm_switched()
        assert wait(lambda: window.block_data["optimism"])
        shared_tk.update()
        window.draw_blocks("evm")
        left, _right, height = window._block_tiles["evm"][0]
        window.block_clicked("evm", SimpleNamespace(x=left + 2))
        assert window.query.get() == f"optimism block {height}" and height == 158084850
        # Watch the address on screen, then add one by hand.
        window.look_up("vitalik.eth")
        assert wait(lambda: window.lookup_data and window.lookup_data.get("kind") == "address")
        assert window.watch_button.instate(["!disabled"])
        window.watch_current()
        assert wait(lambda: window.watch_data and window.watch_data["addresses"])
        assert window.watched.exists(ETH) and "vitalik.eth" in window.watched.item(ETH, "text")
        window.watch_query.set(ADDRESS)
        window.watch_label.set("cold storage")
        window.add_watch()
        assert wait(lambda: len(window.watch_data["addresses"]) == 2)
        window.tabs.select(4)
        window.check_watch()
        assert wait(lambda: "Checked" in window.watch_note.cget("text"))
        window.watched.selection_set(ADDRESS)
        window.open_watched()
        assert window.tabs.index("current") == 1 and wait(lambda: window.lookup_data["address"] == ADDRESS)
        window.tabs.select(4)
        window.watched.selection_set(ADDRESS)
        window.remove_watch()
        assert wait(lambda: len(window.watch_data["addresses"]) == 1)
        # A checked site joins the history.
        window.tabs.select(3)
        window.inspect("example.org")
        assert wait(lambda: window.inspect_data)
        assert window.history_tree.exists("https://example.org")
    finally:
        window.close()
    again = Web3Center(gui)  # a fresh window shows the history at once
    try:
        assert again.history_tree.exists("https://example.org")
    finally:
        again.close()


def test_cases_can_be_added_to_an_invention_without_writing_json(shared_tk, tmp_path):
    from app.gui_invention import InventionPanel
    from app.gui_theme import Palette
    from tests.conftest import close_window, open_window
    from tests.test_executive_invention import ScriptedModel, bench
    runtime = SimpleNamespace(registry=None, invention=bench(tmp_path, ScriptedModel()), session=SimpleNamespace(session_id='gui'))
    root = open_window(shared_tk, '960x700-4000-4000')
    panel = InventionPanel(root, Palette, runtime, lambda *_: None)
    panel.pack(fill='both', expand=True)
    try:
        panel.case_inputs.set('x=2, y=3')
        panel.case_expected.set('13')
        assert panel.add_case()
        panel.case_inputs.set('x = -1.5, y=0')
        panel.case_expected.set('2.25')
        assert panel.add_case()
        assert json.loads(panel.tests.get('1.0', 'end')) == [{'inputs': {'x': '2', 'y': '3'}, 'expected': '13'},
                                                             {'inputs': {'x': '-1.5', 'y': '0'}, 'expected': '2.25'}]
        assert '2 cases over x, y · exact' in panel.case_note.cget('text') and panel.case_inputs.get() == ''
        panel.case_inputs.set('nonsense')
        panel.case_expected.set('4')
        assert not panel.add_case() and 'x=2, y=3' in panel.case_note.cget('text')
    finally:
        close_window(root)
