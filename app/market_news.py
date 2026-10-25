"""Market news and rumours: business and crypto headlines, which markets they name, and which are unconfirmed.

Headlines come from publishers' public RSS feeds (BBC Business, the Guardian, MarketWatch, NPR,
CoinDesk, Cointelegraph, Decrypt), read with HAYAT's own user agent and robots.txt honoured.
Each headline is linked to the markets it names: companies and tickers, coins, and industry words
(oil, chips, gold, rates). A headline written with rumour words ("reportedly", "sources say",
"in talks", "considering", "could") is marked **unconfirmed**: a report that something may happen,
not that it has. For each, HAYAT shows how the named markets moved since it was published, next to
the S&P 500, so a broad market move is not mistaken for the rumour's effect. Rumours can be wrong;
HAYAT never treats one as fact.
"""
from __future__ import annotations

import html
import logging
import re
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

from app.logging import log_swallowed

_log = logging.getLogger(__name__)

FEEDS = (("bbc", "BBC Business", "https://feeds.bbci.co.uk/news/business/rss.xml"),
         ("guardian", "The Guardian · Business", "https://www.theguardian.com/uk/business/rss"),
         ("marketwatch", "MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
         ("npr", "NPR Business", "https://feeds.npr.org/1006/rss.xml"),
         ("coindesk", "CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
         ("cointelegraph", "Cointelegraph", "https://cointelegraph.com/rss"),
         ("decrypt", "Decrypt", "https://decrypt.co/feed"))
#: Rumour wording: a report that something may happen, not that it has. Ordinary "could" and "plans to" are left out:
#: they made every forecast and company announcement a rumour.
RUMOUR = re.compile(r"(?i)\b(?:reportedly|rumou?r(?:s|ed)?|sources?\s+(?:say|said|says|tell|told)|people\s+familiar|according\s+to\s+"
                    r"(?:people|sources|a\s+person|insiders)|is\s+said\s+to|are\s+said\s+to|considering\s+(?:a\s+)?(?:sale|bid|deal|"
                    r"merger|takeover|offer|listing|ipo)|weighing\s+(?:a\s+)?(?:sale|bid|deal|merger|takeover|offer)|mulls?|mulling|"
                    r"in\s+(?:early\s+|advanced\s+|final\s+)?talks|exploring\s+(?:a\s+)?(?:sale|deal|merger|bid|options)|"
                    r"unconfirmed|leaked|speculation|takeover\s+(?:bid|approach)|approach\s+for)\b")
#: Words that name an industry, and the markets that stand for it.
TOPICS = ((r"\boil\b|\bcrude\b|\bopec\b|\bbrent\b|\bgasoline\b", ("XLE", "XOP")), (r"\blng\b|\bnatural gas\b", ("LNG",)),
          (r"\bchips?\b|\bsemiconductors?\b|\bchipmakers?\b", ("SMH", "NVDA", "TSM")), (r"\bgold\b", ("GLD",)),
          (r"\bbitcoin\b|\bbtc\b", ("BTC",)), (r"\bether(?:eum)?\b|\beth\b", ("ETH",)), (r"\bsolana\b", ("SOL",)),
          (r"\bcrypto(?:currency|currencies)?\b|\bstablecoins?\b", ("BTC", "COIN")),
          (r"\bfed\b|\bfederal reserve\b|\binterest rates?\b|\brate cut\b|\binflation\b|\btariffs?\b|\brecession\b|\bwall street\b|\bs&p\b",
           ("SPY",)), (r"\bdefen[cs]e\b|\barms\b|\bmissiles?\b", ("ITA",)), (r"\bcopper\b", ("COPX",)), (r"\buranium\b|\bnuclear\b", ("URA",)),
          (r"\bbanks?\b|\blenders?\b", ("XLF",)), (r"\bai\b|\bartificial intelligence\b", ("NVDA", "QQQ")))
COINS = {"BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "AVAX", "LINK", "LTC", "DOT"}
CACHE_SECONDS = 600


def _text(element: Any, *names: str) -> str:
    for name in names:
        found = element.find(name)
        if found is not None and (found.text or "").strip():
            return found.text.strip()
    return ""


def parse_feed(content: bytes, outlet: str) -> list[dict[str, Any]]:
    """Items from an RSS or Atom feed (defusedxml: no entity expansion)."""
    from defusedxml import ElementTree
    root = ElementTree.fromstring(content)
    atom = "{http://www.w3.org/2005/Atom}"
    items = root.findall(".//item") or root.findall(f".//{atom}entry")
    result = []
    for item in items[:60]:
        title = html.unescape(re.sub(r"<[^>]+>", "", _text(item, "title", f"{atom}title")))
        link = _text(item, "link")
        if not link:
            node = item.find(f"{atom}link")
            link = node.get("href", "") if node is not None else ""
        summary = html.unescape(re.sub(r"<[^>]+>", " ", _text(item, "description", f"{atom}summary")))
        summary = " ".join(summary.split())[:400]
        stamp = _text(item, "pubDate", f"{atom}updated", f"{atom}published")
        try:
            moment = parsedate_to_datetime(stamp) if "," in stamp else datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            moment = moment if moment.tzinfo else moment.replace(tzinfo=UTC)
        except (TypeError, ValueError):
            moment = None
        if title:
            result.append({"title": title, "link": link, "summary": summary, "outlet": outlet,
                           "published": moment.astimezone(UTC).isoformat(timespec="minutes") if moment else ""})
    return result


FUND = re.compile(r"(?i)\b(?:etf|fund|trust|ishares|spdr|msci|select sector|vaneck|invesco|global x|index)\b")


def link_markets(text: str, title: str = "") -> list[str]:
    """Markets a headline names. Companies and tickers may be named in the headline or its summary; funds (a country,
    an industry) and industry words count only in the headline: a summary that mentions France or inflation in passing
    does not make a story about them."""
    from app.markets import STOCK_NAMES
    found: list[str] = []
    lowered = text.lower()
    heading = (title or text).lower()
    for symbol, name in STOCK_NAMES.items():
        where = heading if FUND.search(name) else lowered
        short = re.sub(r"(?i)\b(?:inc|corp(?:oration)?|holdings?|group|co|ltd|plc|the|class [a-z]|etf|trust|fund|spdr|ishares|vaneck)\b\.?", "",
                       name).strip(" ,.&")
        if len(short) >= 4 and re.search(r"\b" + re.escape(short.lower()) + r"\b", where):
            found.append(symbol)
        elif len(symbol) >= 3 and re.search(r"(?<![\w$])\$?" + re.escape(symbol) + r"\b", text):
            found.append(symbol)
    for pattern, markets in TOPICS:
        if re.search(pattern, heading):
            found += list(markets)
    return list(dict.fromkeys(found))[:6]


class MarketNews:
    def __init__(self, fetcher: Any, markets: Any = None) -> None:
        self.fetcher, self.markets = fetcher, markets
        self._cache: tuple[float, list[dict[str, Any]], list[str]] | None = None

    def headlines(self) -> tuple[list[dict[str, Any]], list[str]]:
        if self._cache and time.monotonic() - self._cache[0] < CACHE_SECONDS:
            return self._cache[1], self._cache[2]
        items, gaps = [], []
        for _key, outlet, url in FEEDS:
            try:
                items += parse_feed(self.fetcher.fetch(url, 3 * 1024 * 1024).content, outlet)
            except Exception as exc:  # one publisher down leaves the others
                log_swallowed(_log, "reading a market news feed")
                gaps.append(f"{outlet}: {type(exc).__name__}")
        seen, unique = set(), []
        for item in sorted(items, key=lambda row: row["published"], reverse=True):
            key = re.sub(r"\W+", " ", item["title"].lower()).strip()
            if key not in seen:
                seen.add(key)
                unique.append(item)
        self._cache = (time.monotonic(), unique, gaps)
        return unique, gaps

    def moves_since(self, symbols: list[str], since: str) -> list[dict[str, Any]]:
        if self.markets is None or not since:
            return []
        from app.market_events import _since
        moment = datetime.fromisoformat(since).timestamp()
        rows = []
        for symbol in list(dict.fromkeys([*symbols, "SPY"]))[:5]:
            try:
                moved = _since(self.markets, symbol, moment)
                rows.append({"symbol": symbol, "change_pct": moved["change_pct"], "thin": moved["thin"], "covers": moved["covers_event"]})
            except Exception:  # a market without candles is left out
                log_swallowed(_log, "reading a move since a headline")
        return rows

    def news(self, symbol: str = "", hours: float = 48, rumours_only: bool = False, limit: int = 20) -> dict[str, Any]:
        """Headlines that name markets (or one market), newest first; rumours marked unconfirmed, with moves since."""
        items, gaps = self.headlines()
        cutoff = datetime.now(UTC).timestamp() - hours * 3600
        wanted = symbol.upper().strip()
        rows = []
        for item in items:
            if item["published"] and datetime.fromisoformat(item["published"]).timestamp() < cutoff:
                continue
            markets = link_markets(item["title"] + " " + item["summary"], item["title"])
            if not markets or (wanted and wanted not in markets):
                continue
            rumour = bool(RUMOUR.search(item["title"]) or RUMOUR.search(item["summary"][:200]))
            if rumours_only and not rumour:
                continue
            rows.append({**item, "markets": markets, "rumour": rumour})
            if len(rows) >= limit:
                break
        for row in rows[:12]:
            row["moves"] = self.moves_since(row["markets"], row["published"])
        return {"markets": True, "news_view": True, "symbol": wanted, "rumours_only": rumours_only, "items": rows, "hours": hours,
                "rumours": sum(row["rumour"] for row in rows), "outlets": [outlet for _key, outlet, _url in FEEDS], "gaps": gaps,
                "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "basis": ("Headlines from publishers' public feeds. \"Unconfirmed\" marks rumour wording (reportedly, sources say, in "
                          "talks, could): a report that something may happen, which may prove wrong. Moves since a headline sit next "
                          "to the S&P 500's; nearness in time is not proof the news moved them.")}


def report_lines(value: dict[str, Any]) -> list[str]:
    title = ("Market rumours" if value["rumours_only"] else "Market news") + (f" about {value['symbol']}" if value["symbol"] else "")
    lines = [f"{title}, last {value['hours']:g} h: {len(value['items'])} headline{'s' if len(value['items']) != 1 else ''}"
             + (f", {value['rumours']} unconfirmed" if value["rumours"] else "") + "."]
    for item in value["items"][:8]:
        moves = ", ".join(f"{move['symbol']} {move['change_pct']:+.2f}%" for move in item.get("moves") or [] if move["change_pct"] is not None)
        lines.append(f"  {'UNCONFIRMED · ' if item['rumour'] else ''}{item['title'][:110]} ({item['outlet']}, {item['published'][11:16]} UTC)"
                     + (f" · since: {moves}" if moves else ""))
    lines.append(value["basis"])
    return lines
