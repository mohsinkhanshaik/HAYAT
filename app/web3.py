"""Web3, read-only: blockchain networks, recent blocks, an explorer for addresses, transactions,
blocks and ENS names, and DeFi totals.

HAYAT reads public chain data. It never connects a wallet, never signs and never sends a
transaction; there is no key anywhere in this module.

Sources (keyless, robots.txt honoured by the fetcher, never a spoofed user agent):
* mempool.space: Bitcoin fees, mempool, blocks, addresses, transactions, mining, Lightning.
* Blockscout API v2: Ethereum (eth.blockscout.com), zkSync Era (zksync.blockscout.com) and
  Optimism (explorer.optimism.io, its own Blockscout).
* DefiLlama: value locked per chain, stablecoin supply.

Base, Arbitrum and Polygon's Blockscout explorers answer automated requests with a browser
challenge ("Just a moment..."). HAYAT does not get around that; it says so instead.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.logging import log_swallowed

_log = logging.getLogger(__name__)

MEMPOOL = "https://mempool.space/api/"
LLAMA = "https://api.llama.fi/"
STABLES = "https://stablecoins.llama.fi/"
#: EVM networks read through a public Blockscout API: (key, name, base URL, native coin).
EVM = (("ethereum", "Ethereum", "https://eth.blockscout.com/api/v2/", "ETH"),
       ("optimism", "Optimism", "https://explorer.optimism.io/api/v2/", "ETH"),
       ("zksync", "zkSync Era", "https://zksync.blockscout.com/api/v2/", "ETH"))
#: Networks whose explorer refuses automated reading; named so the gap is visible, never worked around.
REFUSED = (("Base", "base.blockscout.com"), ("Arbitrum", "arbitrum.blockscout.com"), ("Polygon", "polygon.blockscout.com"))
CACHE_SECONDS = {"networks": 30, "blocks": 20, "lookup": 30, "defi": 600}
READ_ONLY = ("Read-only public chain data: HAYAT has no wallet, never signs and never sends a transaction.")

BTC_ADDRESS = re.compile(r"(?:bc1[02-9ac-hj-np-z]{11,71}|[13][1-9A-HJ-NP-Za-km-z]{25,34})")
EVM_ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}")
EVM_TX = re.compile(r"0x[0-9a-fA-F]{64}")
BTC_TX = re.compile(r"[0-9a-fA-F]{64}")
ENS = re.compile(r"[a-z0-9-]+(?:\.[a-z0-9-]+)*\.eth", re.I)
BLOCK = re.compile(r"(?i)(?:(?P<chain>bitcoin|btc|ethereum|eth|optimism|op|zksync)\s+)?block\s+#?(?P<number>\d{1,10})")
CHAIN_WORDS = {"bitcoin": "bitcoin", "btc": "bitcoin", "ethereum": "ethereum", "eth": "ethereum", "optimism": "optimism",
               "op": "optimism", "zksync": "zksync"}


def classify(query: str) -> tuple[str, str, str]:
    """(kind, value, chain) for an explorer query: address, transaction, block or ENS name."""
    text = " ".join(str(query or "").split())
    if match := BLOCK.fullmatch(text):
        return "block", match["number"], CHAIN_WORDS.get((match["chain"] or "bitcoin").lower(), "bitcoin")
    word = text.split()[-1] if text else ""
    if EVM_TX.fullmatch(word):
        return "transaction", word.lower(), "ethereum"
    if EVM_ADDRESS.fullmatch(word):
        return "address", word, "ethereum"
    if BTC_TX.fullmatch(word):
        return "transaction", word.lower(), "bitcoin"
    if BTC_ADDRESS.fullmatch(word):
        return "address", word, "bitcoin"
    if ENS.fullmatch(word):
        return "ens", word.lower(), "ethereum"
    if word.isdigit() and len(word) <= 10:
        return "block", word, "bitcoin"
    raise ValueError("Give a Bitcoin or Ethereum address, a transaction hash, an ENS name such as vitalik.eth, "
                     "or a block such as \"bitcoin block 900000\".")


def wei(value: Any, decimals: int = 18) -> float | None:
    try:
        return int(value) / 10 ** decimals
    except (TypeError, ValueError):
        return None


def stamp(seconds: Any) -> str | None:
    try:
        return datetime.fromtimestamp(float(seconds), UTC).isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError):
        return None


class Web3Data:
    def __init__(self, fetcher: Any, path: Path | str | None = None) -> None:
        self.fetcher = fetcher
        #: Local file for watched addresses and checked sites; nothing in it leaves this computer.
        self.path = Path(path) if path else None
        self._cache: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()
        self._store_lock = threading.Lock()

    def _json(self, url: str, max_bytes: int = 1024 * 1024, ttl: float = 30) -> Any:
        with self._lock:
            cached = self._cache.get(url)
            if cached and time.monotonic() - cached[0] < ttl:
                return cached[1]
        value = json.loads(self.fetcher.fetch(url, max_bytes).content)
        with self._lock:
            self._cache[url] = (time.monotonic(), value)
            if len(self._cache) > 400:
                for key in sorted(self._cache, key=lambda item: self._cache[item][0])[:100]:
                    self._cache.pop(key, None)
        return value

    # ------------------------------------------------------------------ networks
    def networks(self) -> dict[str, Any]:
        """Every network's state now: Bitcoin (fees, mining, Lightning) and the EVM chains (gas, load)."""
        ttl = CACHE_SECONDS["networks"]
        result: dict[str, Any] = {"web3_view": True, "networks_view": True, "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
                                  "networks": [], "gaps": [], "refused": [{"network": name, "host": host,
                                  "why": "its explorer answers automated requests with a browser challenge; HAYAT does not get around it"}
                                  for name, host in REFUSED], "basis": READ_ONLY}
        try:
            fees = self._json(MEMPOOL + "v1/fees/recommended", 64 * 1024, ttl)
            pool = self._json(MEMPOOL + "mempool", 512 * 1024, ttl)
            height = self._json(MEMPOOL + "blocks/tip/height", 1024, ttl)
            bitcoin: dict[str, Any] = {"key": "bitcoin", "name": "Bitcoin", "coin": "BTC", "block_height": int(height),
                                       "fee_fast_sat_vb": fees.get("fastestFee"), "fee_30min_sat_vb": fees.get("halfHourFee"),
                                       "fee_1h_sat_vb": fees.get("hourFee"), "fee_economy_sat_vb": fees.get("economyFee"),
                                       "mempool_transactions": pool.get("count"), "mempool_vbytes": pool.get("vsize"),
                                       "source": "mempool.space"}
            try:
                adjustment = self._json(MEMPOOL + "v1/difficulty-adjustment", 64 * 1024, 300)
                bitcoin["difficulty"] = {"progress_pct": round(adjustment.get("progressPercent") or 0, 1),
                                         "expected_change_pct": round(adjustment.get("difficultyChange") or 0, 2),
                                         "remaining_blocks": adjustment.get("remainingBlocks"),
                                         "estimated_at": stamp((adjustment.get("estimatedRetargetDate") or 0) / 1000)}
                hashrate = self._json(MEMPOOL + "v1/mining/hashrate/1w", 128 * 1024, 900).get("hashrates") or []
                if hashrate:
                    bitcoin["hashrate_eh_s"] = round(hashrate[-1]["avgHashrate"] / 1e18, 1)
                pools = self._json(MEMPOOL + "v1/mining/pools/1w", 256 * 1024, 900).get("pools") or []
                total = sum(item.get("blockCount", 0) for item in pools) or 1
                bitcoin["top_pools"] = [{"name": item["name"], "blocks": item["blockCount"],
                                         "share_pct": round(item["blockCount"] / total * 100, 1)} for item in pools[:6]]
                lightning = (self._json(MEMPOOL + "v1/lightning/statistics/latest", 64 * 1024, 3600) or {}).get("latest") or {}
                if lightning:
                    bitcoin["lightning"] = {"nodes": lightning.get("node_count"), "channels": lightning.get("channel_count"),
                                            "capacity_btc": round((lightning.get("total_capacity") or 0) / 1e8),
                                            "as_of": str(lightning.get("added") or "")[:10]}
            except Exception:  # mining and Lightning are extras; fees and blocks stand without them
                log_swallowed(_log, "reading Bitcoin mining and Lightning statistics")
            result["networks"].append(bitcoin)
        except Exception as exc:  # one network unreachable leaves the others standing
            log_swallowed(_log, "reading Bitcoin network state")
            result["gaps"].append(f"Bitcoin (mempool.space): {type(exc).__name__}")
        for key, name, base, coin in EVM:
            try:
                stats = self._json(base + "stats", 256 * 1024, ttl)
                gas = stats.get("gas_prices") or {}
                result["networks"].append({
                    "key": key, "name": name, "coin": coin, "gas_slow_gwei": gas.get("slow"), "gas_average_gwei": gas.get("average"),
                    "gas_fast_gwei": gas.get("fast"),
                    "block_time_seconds": round(float(stats["average_block_time"]) / 1000, 1) if stats.get("average_block_time") else None,
                    "transactions_today": int(stats["transactions_today"]) if str(stats.get("transactions_today") or "").isdigit() else None,
                    "utilization_pct": stats.get("network_utilization_percentage"),
                    "total_blocks": int(stats["total_blocks"]) if str(stats.get("total_blocks") or "").isdigit() else None,
                    "total_addresses": int(stats["total_addresses"]) if str(stats.get("total_addresses") or "").isdigit() else None,
                    "coin_price_usd": float(stats["coin_price"]) if stats.get("coin_price") else None,
                    "source": base.split("/")[2]})
            except Exception as exc:
                log_swallowed(_log, f"reading {name} network state")
                result["gaps"].append(f"{name} ({base.split('/')[2]}): {type(exc).__name__}")
        if not result["networks"]:
            raise ValueError("No blockchain source answered: " + "; ".join(result["gaps"]))
        return result

    def blocks(self, chain: str = "bitcoin", limit: int = 10) -> dict[str, Any]:
        """The latest blocks on one network, newest first."""
        limit = max(1, min(int(limit), 15))
        rows = []
        if chain == "bitcoin":
            for item in self._json(MEMPOOL + "v1/blocks", 256 * 1024, CACHE_SECONDS["blocks"])[:limit]:
                extras = item.get("extras") or {}
                rows.append({"height": item["height"], "hash": item["id"], "at": stamp(item.get("timestamp")),
                             "transactions": item.get("tx_count"), "size_mb": round((item.get("size") or 0) / 1e6, 2),
                             "fees_btc": round((extras.get("totalFees") or 0) / 1e8, 4), "median_fee_sat_vb": extras.get("medianFee"),
                             "miner": (extras.get("pool") or {}).get("name")})
        else:
            base = self._evm(chain)[2]
            page = self._json(base + "blocks?type=block", 2 * 1024 * 1024, CACHE_SECONDS["blocks"])
            for item in (page.get("items") if isinstance(page, dict) else page or [])[:limit]:
                rows.append({"height": item["height"], "hash": item.get("hash"), "at": item.get("timestamp"),
                             "transactions": item.get("transactions_count") or item.get("transaction_count"),
                             "gas_used_pct": round(item.get("gas_used_percentage") or 0, 1),
                             "burnt_eth": wei(item.get("burnt_fees")), "miner": ((item.get("miner") or {}).get("name")
                                                                                   or (item.get("miner") or {}).get("hash"))})
        return {"web3_view": True, "blocks_view": True, "chain": chain, "blocks": rows, "basis": READ_ONLY}

    @staticmethod
    def _evm(chain: str) -> tuple[str, str, str, str]:
        for item in EVM:
            if item[0] == chain:
                return item
        raise ValueError(f"HAYAT reads {', '.join(name for _key, name, _base, _coin in EVM)} and Bitcoin; not {chain!r}.")

    # ------------------------------------------------------------------ explorer
    def lookup(self, query: str, chain: str = "") -> dict[str, Any]:
        """Public facts about an address, transaction, block or ENS name. Nothing is signed or sent."""
        kind, value, guessed = classify(query)
        chain = chain or guessed
        if kind == "ens":
            found = self._json(EVM[0][2] + "search?q=" + value, 256 * 1024, CACHE_SECONDS["lookup"])
            items = found.get("items") if isinstance(found, dict) else found
            match = next((item for item in items or [] if item.get("type") == "ens_domain"
                          and str(item.get("ens_info", {}).get("name") or item.get("name") or "").lower() == value), None)
            if not match or not match.get("address_hash", match.get("address")):
                raise ValueError(f"No Ethereum address is registered for {value}.")
            result = self.lookup(match.get("address_hash") or match.get("address"), "ethereum")
            result["ens"] = value
            return result
        if chain == "bitcoin":
            result = {"address": self._btc_address, "transaction": self._btc_transaction, "block": self._btc_block}[kind](value)
        else:
            result = {"address": self._evm_address, "transaction": self._evm_transaction, "block": self._evm_block}[kind](chain, value)
        return {"web3_view": True, "lookup_view": True, "kind": kind, "chain": chain, "query": value, **result,
                "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"), "basis": READ_ONLY}

    def _btc_address(self, address: str) -> dict[str, Any]:
        data = self._json(MEMPOOL + "address/" + address, 64 * 1024, CACHE_SECONDS["lookup"])
        chain, pool = data.get("chain_stats") or {}, data.get("mempool_stats") or {}
        balance = (chain.get("funded_txo_sum", 0) - chain.get("spent_txo_sum", 0)) / 1e8
        pending = (pool.get("funded_txo_sum", 0) - pool.get("spent_txo_sum", 0)) / 1e8
        transactions = []
        for item in self._json(MEMPOOL + f"address/{address}/txs", 2 * 1024 * 1024, CACHE_SECONDS["lookup"])[:25]:
            received = sum(out.get("value", 0) for out in item.get("vout") or [] if out.get("scriptpubkey_address") == address)
            sent = sum((vin.get("prevout") or {}).get("value", 0) for vin in item.get("vin") or []
                       if (vin.get("prevout") or {}).get("scriptpubkey_address") == address)
            status = item.get("status") or {}
            transactions.append({"hash": item["txid"], "at": stamp(status.get("block_time")), "confirmed": bool(status.get("confirmed")),
                                 "block": status.get("block_height"), "change": round((received - sent) / 1e8, 8),
                                 "fee": round((item.get("fee") or 0) / 1e8, 8)})
        return {"title": "Bitcoin address", "address": address, "balance": round(balance, 8), "unit": "BTC",
                "pending": round(pending, 8), "transaction_count": chain.get("tx_count", 0) + pool.get("tx_count", 0),
                "received_total": round(chain.get("funded_txo_sum", 0) / 1e8, 8), "transactions": transactions,
                "source": "mempool.space"}

    def _btc_transaction(self, txid: str) -> dict[str, Any]:
        item = self._json(MEMPOOL + "tx/" + txid, 2 * 1024 * 1024, CACHE_SECONDS["lookup"])
        status = item.get("status") or {}
        tip = int(self._json(MEMPOOL + "blocks/tip/height", 1024, CACHE_SECONDS["networks"]))
        outputs = [{"address": out.get("scriptpubkey_address") or out.get("scriptpubkey_type"), "value": out.get("value", 0) / 1e8}
                   for out in item.get("vout") or []]
        inputs = [{"address": (vin.get("prevout") or {}).get("scriptpubkey_address") or ("coinbase" if vin.get("is_coinbase") else "?"),
                   "value": (vin.get("prevout") or {}).get("value", 0) / 1e8} for vin in item.get("vin") or []]
        vsize = (item.get("weight") or 0) / 4
        return {"title": "Bitcoin transaction", "hash": txid, "confirmed": bool(status.get("confirmed")),
                "block": status.get("block_height"), "at": stamp(status.get("block_time")),
                "confirmations": tip - status["block_height"] + 1 if status.get("block_height") else 0,
                "fee": (item.get("fee") or 0) / 1e8, "fee_rate_sat_vb": round((item.get("fee") or 0) / vsize, 1) if vsize else None,
                "value": round(sum(out["value"] for out in outputs), 8), "unit": "BTC", "inputs": inputs[:20], "outputs": outputs[:20],
                "input_count": len(inputs), "output_count": len(outputs), "source": "mempool.space"}

    def _btc_block(self, height: str) -> dict[str, Any]:
        block_hash = self.fetcher.fetch(MEMPOOL + f"block-height/{int(height)}", 1024).content.decode().strip()
        item = self._json(MEMPOOL + "v1/block/" + block_hash, 128 * 1024, 3600)
        extras = item.get("extras") or {}
        return {"title": f"Bitcoin block {item['height']:,}", "hash": block_hash, "height": item["height"], "at": stamp(item.get("timestamp")),
                "transaction_count": item.get("tx_count"), "size_mb": round((item.get("size") or 0) / 1e6, 2),
                "fees": round((extras.get("totalFees") or 0) / 1e8, 4), "unit": "BTC", "miner": (extras.get("pool") or {}).get("name"),
                "median_fee_sat_vb": extras.get("medianFee"), "reward": round((extras.get("reward") or 0) / 1e8, 4),
                "source": "mempool.space"}

    def _evm_address(self, chain: str, address: str) -> dict[str, Any]:
        _key, name, base, coin = self._evm(chain)
        data = self._json(base + "addresses/" + address, 128 * 1024, CACHE_SECONDS["lookup"])
        transactions = []
        try:
            page = self._json(base + f"addresses/{address}/transactions", 2 * 1024 * 1024, CACHE_SECONDS["lookup"])
            for item in (page.get("items") or [])[:25]:
                sender = (item.get("from") or {}).get("hash", "")
                receiver = (item.get("to") or {}).get("hash", "")
                value = wei(item.get("value")) or 0.0
                outgoing = sender.lower() == address.lower()
                transactions.append({"hash": item.get("hash"), "at": item.get("timestamp"), "confirmed": item.get("status") == "ok",
                                     "status": item.get("status"), "block": item.get("block_number") or item.get("block"),
                                     "change": -value if outgoing else value, "method": item.get("method"),
                                     "counterparty": receiver if outgoing else sender, "fee": wei((item.get("fee") or {}).get("value"))})
        except Exception:  # the balance stands without the history
            log_swallowed(_log, "reading an address's transactions")
        holdings, hidden = [], 0
        try:
            balances = self._json(base + f"addresses/{address}/token-balances", 6 * 1024 * 1024, CACHE_SECONDS["lookup"])
            for item in balances if isinstance(balances, list) else []:
                info = item.get("token") or {}
                if info.get("type") not in (None, "ERC-20") or info.get("reputation") not in (None, "ok"):
                    hidden += 1
                    continue
                amount = wei(item.get("value"), int(info.get("decimals") or 18)) or 0.0
                rate = float(info["exchange_rate"]) if info.get("exchange_rate") else None
                if not amount or rate is None:
                    hidden += 1  # unpriced tokens are mostly airdropped spam
                    continue
                cap = float(info["circulating_market_cap"]) if info.get("circulating_market_cap") else None
                if cap and amount * rate > cap * .01:
                    hidden += 1  # more than 1 % of a token's whole value: airdropped or too illiquid to be worth that
                    continue
                holdings.append({"symbol": info.get("symbol") or "?", "name": info.get("name") or "", "address": info.get("address_hash") or
                                 info.get("address") or "", "amount": amount, "usd": round(amount * rate, 2)})
            holdings.sort(key=lambda row: -row["usd"])
        except Exception:  # the balance stands without the tokens
            log_swallowed(_log, "reading an address's token balances")
        token = data.get("token") or {}
        return {"title": f"{name} {'contract account' if data.get('is_contract') else 'address'}", "address": data.get("hash", address),
                "tokens": holdings[:15], "tokens_usd": round(sum(row["usd"] for row in holdings), 2), "tokens_hidden": hidden,
                "balance": wei(data.get("coin_balance")), "unit": coin, "name": data.get("name") or token.get("name"),
                "ens_name": data.get("ens_domain_name"), "is_contract": bool(data.get("is_contract")),
                "verified": bool(data.get("is_verified")), "token": ({"name": token.get("name"), "symbol": token.get("symbol"),
                                                                     "type": token.get("type"), "holders": token.get("holders_count") or token.get("holders")}
                                                                    if token else None),
                "transactions": transactions, "source": base.split("/")[2]}

    def _evm_transaction(self, chain: str, tx: str) -> dict[str, Any]:
        _key, name, base, coin = self._evm(chain)
        item = self._json(base + "transactions/" + tx, 512 * 1024, CACHE_SECONDS["lookup"])
        transfers = [{"token": (entry.get("token") or {}).get("symbol"), "from": (entry.get("from") or {}).get("hash"),
                      "to": (entry.get("to") or {}).get("hash"),
                      "amount": wei((entry.get("total") or {}).get("value"), int((entry.get("total") or {}).get("decimals") or 18))}
                     for entry in (item.get("token_transfers") or [])[:20]]
        return {"title": f"{name} transaction", "hash": tx, "status": item.get("status"), "confirmed": item.get("status") == "ok",
                "block": item.get("block_number") or item.get("block"), "at": item.get("timestamp"),
                "confirmations": item.get("confirmations"), "from": (item.get("from") or {}).get("hash"),
                "to": (item.get("to") or {}).get("hash"), "value": wei(item.get("value")), "unit": coin,
                "fee": wei((item.get("fee") or {}).get("value")), "method": item.get("method"),
                "gas_price_gwei": wei(item.get("gas_price"), 9), "token_transfers": transfers, "source": base.split("/")[2]}

    def _evm_block(self, chain: str, number: str) -> dict[str, Any]:
        _key, name, base, coin = self._evm(chain)
        item = self._json(base + f"blocks/{int(number)}", 256 * 1024, 3600)
        return {"title": f"{name} block {int(item['height']):,}", "hash": item.get("hash"), "height": item["height"], "at": item.get("timestamp"),
                "transaction_count": item.get("transactions_count") or item.get("transaction_count"),
                "gas_used_pct": round(item.get("gas_used_percentage") or 0, 1), "burnt": wei(item.get("burnt_fees")), "unit": coin,
                "miner": (item.get("miner") or {}).get("hash"), "source": base.split("/")[2]}

    # ------------------------------------------------------------------ local lists
    def _load(self) -> dict[str, Any]:
        if not self.path or not self.path.exists():
            return {"addresses": [], "sites": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log_swallowed(_log, "reading the Web3 watch list")
            return {"addresses": [], "sites": []}
        return {"addresses": list(data.get("addresses") or []), "sites": list(data.get("sites") or [])}

    def _store(self, data: dict[str, Any]) -> None:
        if not self.path:
            raise ValueError("Watching needs HAYAT's data folder; this session has none.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.path)

    def watchlist(self, action: str = "list", query: str = "", label: str = "") -> dict[str, Any]:
        """Watched addresses (local): list, add (an address or ENS name), remove, or check balances now.

        Checking reads each balance and says how it changed since the last check. Nothing is signed or sent."""
        if action not in ("list", "add", "remove", "check"):
            raise ValueError("Use list, add, remove or check.")
        changed: list[dict[str, Any]] = []
        with self._store_lock:
            data = self._load()
            entries = data["addresses"]
            if action == "add":
                found = self.lookup(query)
                if found["kind"] != "address":
                    raise ValueError("Only an address or an ENS name can be watched, not a transaction or a block.")
                address = found["address"]
                if not any(entry["address"].lower() == address.lower() and entry["chain"] == found["chain"] for entry in entries):
                    if len(entries) >= 50:
                        raise ValueError("At most 50 addresses can be watched; remove one first.")
                    entries.append({"address": address, "chain": found["chain"], "unit": found.get("unit", ""),
                                    "label": (label or found.get("ens") or found.get("ens_name") or found.get("name") or "")[:60],
                                    "added_at": datetime.now(UTC).isoformat(timespec="seconds"),
                                    "balance": found.get("balance"), "previous_balance": None, "checked_at": found["fetched_at"],
                                    "transactions": found.get("transaction_count") or len(found.get("transactions") or [])})
            elif action == "remove":
                needle = query.strip().lower()
                kept = [entry for entry in entries if needle not in (entry["address"].lower(), entry["label"].lower())]
                if len(kept) == len(entries) and ENS.fullmatch(needle):
                    # A renamed entry is still found by its ENS name.
                    resolved = self.lookup(needle)["address"].lower()
                    kept = [entry for entry in entries if entry["address"].lower() != resolved]
                if len(kept) == len(entries):
                    raise ValueError(f"{query} is not on the watch list.")
                entries[:] = kept
            elif action == "check":
                for entry in entries:
                    try:
                        found = self.lookup(entry["address"], entry["chain"])
                    except Exception:  # one address unreadable leaves the others checked
                        log_swallowed(_log, "checking a watched address")
                        entry["error"] = "could not be read just now"
                        continue
                    entry.pop("error", None)
                    balance = found.get("balance")
                    if entry.get("balance") is not None and balance is not None and abs(balance - entry["balance"]) > 1e-12:
                        changed.append({**entry, "new_balance": balance, "change": balance - entry["balance"]})
                        entry["previous_balance"] = entry["balance"]
                    entry["balance"], entry["checked_at"] = balance, found["fetched_at"]
            if action != "list":
                self._store(data)
        return {"web3_view": True, "watch_view": True, "action": action, "addresses": entries, "changed": changed,
                "basis": "Watched locally: HAYAT reads public balances and never signs or sends anything."}

    def remember_site(self, value: dict[str, Any]) -> list[dict[str, Any]]:
        """Keep the last 20 agent-web checks (site, score, AI position) for the history list."""
        if not self.path:
            return []
        with self._store_lock:
            data = self._load()
            sites = [entry for entry in data["sites"] if entry["site"] != value["site"]]
            sites.insert(0, {"site": value["site"], "score": value["score"], "out_of": value["out_of"],
                             "ai_position": value["ai_position"], "at": value["fetched_at"]})
            data["sites"] = sites[:20]
            self._store(data)
            return data["sites"]

    def sites(self) -> list[dict[str, Any]]:
        with self._store_lock:
            return self._load()["sites"]

    # ------------------------------------------------------------------ DeFi
    def defi(self, limit: int = 15) -> dict[str, Any]:
        """Value locked in DeFi per chain, and the largest stablecoins by supply (DefiLlama)."""
        limit = max(1, min(int(limit), 40))
        ttl = CACHE_SECONDS["defi"]
        chains = sorted((item for item in self._json(LLAMA + "v2/chains", 512 * 1024, ttl) if (item.get("tvl") or 0) > 0),
                        key=lambda item: -item["tvl"])
        total = sum(item["tvl"] for item in chains) or 1
        result: dict[str, Any] = {"web3_view": True, "defi_view": True, "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
                                  "total_tvl_usd": round(total), "chain_count": len(chains),
                                  "chains": [{"name": item["name"], "tvl_usd": round(item["tvl"]), "share_pct": round(item["tvl"] / total * 100, 2),
                                              "token": item.get("tokenSymbol")} for item in chains[:limit]],
                                  "stablecoins": [], "gaps": [], "source": "DefiLlama",
                                  "basis": "Total value locked is DefiLlama's estimate of funds deposited in DeFi apps on each chain."}
        try:
            stables = self._json(STABLES + "stablecoins?includePrices=false", 2 * 1024 * 1024, ttl).get("peggedAssets") or []
            rows = []
            for item in stables:
                supply = (item.get("circulating") or {}).get("peggedUSD")
                if supply:
                    rows.append({"name": item.get("name"), "symbol": item.get("symbol"), "supply_usd": round(supply),
                                 "mechanism": item.get("pegMechanism"), "chains": len(item.get("chains") or [])})
            rows.sort(key=lambda row: -row["supply_usd"])
            result["stablecoins"] = rows[:limit]
            result["stablecoin_supply_usd"] = round(sum(row["supply_usd"] for row in rows))
        except Exception as exc:
            log_swallowed(_log, "reading stablecoin supply")
            result["gaps"].append(f"Stablecoins (DefiLlama): {type(exc).__name__}")
        try:
            dexs = self._json(LLAMA + "overview/dexs?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true", 6 * 1024 * 1024, ttl)
            protocols = sorted((item for item in dexs.get("protocols") or [] if (item.get("total24h") or 0) > 0),
                               key=lambda item: -(item.get("total24h") or 0))
            result["dex"] = {"volume_24h_usd": round(dexs.get("total24h") or 0), "volume_7d_usd": round(dexs.get("total7d") or 0),
                             "change_1d_pct": round(dexs["change_1d"], 2) if dexs.get("change_1d") is not None else None,
                             "top": [{"name": item.get("displayName") or item.get("name"), "volume_24h_usd": round(item["total24h"]),
                                      "change_1d_pct": round(item["change_1d"], 2) if item.get("change_1d") is not None else None,
                                      "chains": len(item.get("chains") or [])} for item in protocols[:limit]]}
        except Exception as exc:
            log_swallowed(_log, "reading DEX volumes")
            result["gaps"].append(f"DEX volumes (DefiLlama): {type(exc).__name__}")
        return result
