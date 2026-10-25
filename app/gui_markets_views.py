"""Advanced Trade Center views: a live ticker strip, a market heatmap, and world-event markers for the chart.

* Ticker strip: the markets most people watch, each streaming its own last trade, flashing
  green or red as it moves; a click opens it.
* Heatmap: crypto, big tech, the US market and every industry fund as tiles, sized by
  trading volume and coloured by today's move; a click opens it.
* Event markers: on a share's or coin's chart, a mark where a world event that bears on it
  (market_events rules) was reported; hovering names the event. Nearness in time is shown,
  not cause.
"""
from __future__ import annotations

import math
import tkinter as tk
from datetime import datetime
from typing import Any, Callable

from app.gui_intelligence_parts import blend
from app.markets import money, signed_pct

UP, DOWN = "#4ADE80", "#F05D5E"
#: (symbol, asset, short label) for the strip. SPY, QQQ, GLD and XLE are funds: the price is one fund share,
#: not the index level or an ounce of gold, so their labels name the fund.
TICKER = (("BTC", "crypto", "Bitcoin"), ("ETH", "crypto", "Ether"), ("SOL", "crypto", "Solana"),
          ("SPY", "stock", "S&P 500 fund (SPY)"), ("QQQ", "stock", "Nasdaq-100 fund (QQQ)"), ("NVDA", "stock", "Nvidia"),
          ("AAPL", "stock", "Apple"), ("GLD", "stock", "Gold fund (GLD)"), ("XLE", "stock", "Energy fund (XLE)"))
CRYPTO_TILES = ("BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "AVAX", "LINK", "LTC", "DOT", "TRX", "XLM")
BIG_TECH = ("AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "AMD", "NFLX")


def heat_colour(change: float | None, background: str) -> str:
    """Green for up, red for down, stronger with the size of the move (full colour at 3 %)."""
    if change is None:
        return blend("#5B6770", background, .3)
    strength = min(1.0, abs(change) / 3.0)
    return blend(UP if change >= 0 else DOWN, background, .82 - .62 * strength)


def squarify(weights: list[float], x: float, y: float, width: float, height: float) -> list[tuple[float, float, float, float]]:
    """Rectangles for weights, close to square (split the longer side, halves by weight)."""
    if not weights:
        return []
    if len(weights) == 1:
        return [(x, y, width, height)]
    total = sum(weights)
    running, cut = 0.0, 0
    for index, weight in enumerate(weights):
        if running + weight > total / 2 and index:
            break
        running += weight
        cut = index + 1
    share = running / total if total else .5
    if width >= height:
        first = width * share
        return squarify(weights[:cut], x, y, first, height) + squarify(weights[cut:], x + first, y, width - first, height)
    first = height * share
    return squarify(weights[:cut], x, y, width, first) + squarify(weights[cut:], x, y + first, width, height - first)


def heat_groups(stocks: dict[str, Any], crypto: dict[str, Any]) -> list[dict[str, Any]]:
    """[{group, tiles: [{symbol, asset, change, weight, name}]}] from the stock and crypto rows (by symbol)."""
    from app.market_events import SECTORS

    def tile(symbol: str, asset: str) -> dict[str, Any] | None:
        row = (crypto if asset == "crypto" else stocks).get(symbol)
        if not row:
            return None
        return {"symbol": symbol, "asset": asset, "change": row.get("change_today_pct"), "name": row.get("name", ""),
                "weight": max(1.0, math.log10((row.get("volume_24h_quote") or 0) + 10)), "thin": row.get("thin", False),
                "price": row.get("price")}
    groups = [("Crypto", [(symbol, "crypto") for symbol in CRYPTO_TILES]), ("Big tech", [(symbol, "stock") for symbol in BIG_TECH]),
              ("US market", [(symbol, "stock") for symbol in ("SPY", "QQQ", "IWM", "VTI")])]
    seen = {symbol for _name, members in groups for symbol, _asset in members}
    for sector, symbols in SECTORS.items():
        if sector in ("US market (benchmark)", "Crypto"):
            continue
        members = [(symbol, "crypto" if symbol in ("BTC", "ETH") else "stock") for symbol in symbols if symbol not in seen]
        seen.update(symbol for symbol, _asset in members)
        if members:
            groups.append((sector, members))
    result = []
    for name, members in groups:
        tiles = [found for found in (tile(symbol, asset) for symbol, asset in members) if found]
        if tiles:
            result.append({"group": name, "tiles": tiles, "weight": sum(item["weight"] for item in tiles)})
    return result


def events_for(events_data: dict[str, Any] | None, symbol: str) -> list[dict[str, Any]]:
    """World events whose industry includes this market (from Trade Center's World events data)."""
    from app.market_events import SECTORS
    found = {}
    for entry in (events_data or {}).get("sectors") or []:
        if symbol in SECTORS.get(entry["sector"], []):
            for event in entry["events"]:
                found.setdefault(event["id"], {**event, "sector": entry["sector"]})
    return list(found.values())


class TickerStrip(tk.Frame):
    """The markets most people watch, streaming; click one to open it."""

    def __init__(self, parent: Any, palette: Any, on_click: Callable[[str, str], Any]) -> None:
        super().__init__(parent, bg=palette.PANEL, padx=8, pady=4)
        self.p, self.cells, self.last = palette, {}, {}
        for column, (symbol, asset, label) in enumerate(TICKER):
            cell = tk.Frame(self, bg=palette.PANEL, padx=8, cursor="hand2")
            cell.grid(row=0, column=column, sticky="ew")
            self.columnconfigure(column, weight=1)
            name = tk.Label(cell, text=label, bg=palette.PANEL, fg=palette.MUTED, font=("Segoe UI", 8))
            price = tk.Label(cell, text="…", bg=palette.PANEL, fg=palette.WHITE, font=("Segoe UI Semibold", 10))
            change = tk.Label(cell, text="", bg=palette.PANEL, fg=palette.MUTED, font=("Segoe UI", 8))
            name.pack(anchor="w")
            price.pack(anchor="w")
            change.pack(anchor="w")
            for widget in (cell, name, price, change):
                widget.bind("<Button-1>", lambda _event, symbol=symbol, asset=asset: on_click(symbol, asset))
            self.cells[symbol] = {"asset": asset, "price": price, "change": change}

    @staticmethod
    def stream_names() -> list[str]:
        from app.markets_live import ws_symbol
        return [ws_symbol(symbol, asset) for symbol, asset, _label in TICKER]

    def update_from(self, snapshot: dict[str, Any]) -> None:
        from app.markets_live import ws_symbol
        for symbol, cell in self.cells.items():
            item = (snapshot.get("prices") or {}).get(ws_symbol(symbol, cell["asset"]))
            if not item or item.get("price") is None:
                continue
            price = float(item["price"])
            before = self.last.get(symbol)
            colour = self.p.WHITE if before is None or price == before else UP if price > before else DOWN
            cell["price"].configure(text=money(price), fg=colour)
            if before is not None and price != before:
                cell["price"].after(700, lambda label=cell["price"]: label.winfo_exists() and label.configure(fg=self.p.WHITE))
            self.last[symbol] = price
            change = item.get("change_pct")
            if change is not None:
                cell["change"].configure(text=signed_pct(change) + " 24 h", fg=UP if change > 0 else DOWN if change < 0 else self.p.MUTED)


class Heatmap(tk.Canvas):
    """Tiles sized by trading volume, coloured by today's move; a click opens the market."""

    def __init__(self, parent: Any, palette: Any, on_click: Callable[[str, str], Any]) -> None:
        super().__init__(parent, bg=blend(palette.PANEL, palette.BLACK, .5), highlightthickness=0)
        self.p, self.on_click, self.groups, self.tiles, self.flagged = palette, on_click, [], [], {}
        self.bind("<Configure>", lambda _event: self.draw())
        self.bind("<Button-1>", self.click)
        self.bind("<Motion>", self.hover)
        self.bind("<Leave>", lambda _event: self.delete("hover"))

    def show(self, groups: list[dict[str, Any]], flagged: dict[str, int] | None = None) -> None:
        """Draw these groups; `flagged` = {symbol: world events in its industries}, marked ◆ on the tile."""
        self.groups, self.flagged = groups, flagged or {}
        self.draw()

    def draw(self) -> None:
        self.delete("all")
        width, height = max(200, self.winfo_width()), max(150, self.winfo_height())
        self.tiles = []
        if not self.groups:
            self.create_text(width / 2, height / 2, text="Loading the heatmap…", fill=self.p.MUTED, font=("Segoe UI", 10))
            return
        background = blend(self.p.PANEL, self.p.BLACK, .5)
        for group, (gx, gy, gw, gh) in zip(self.groups, squarify([group["weight"] for group in self.groups], 2, 2, width - 4, height - 4)):
            self.create_rectangle(gx, gy, gx + gw, gy + gh, outline=self.p.BLACK, width=2)
            # The group name only where it fits (about 6 px a letter at this size), so small groups never overlap.
            room = int((gw - 10) / 6)
            labelled = room >= 4 and gh > 40
            if labelled:
                name = group["group"] if len(group["group"]) <= room else group["group"][:room - 1] + "…"
                self.create_text(gx + 5, gy + 3, text=name, anchor="nw", fill=self.p.MUTED, font=("Segoe UI Semibold", 8))
            inner = squarify([tile["weight"] for tile in group["tiles"]], gx + 2, gy + (16 if labelled else 2), gw - 4,
                             gh - (18 if labelled else 4))
            for tile, (x, y, w, h) in zip(group["tiles"], inner):
                self.create_rectangle(x + 1, y + 1, x + w - 1, y + h - 1, fill=heat_colour(tile["change"], background),
                                      outline=self.p.BLACK)
                if w > 34 and h > 22:
                    self.create_text(x + w / 2, y + h / 2 - (6 if h > 34 else 0), text=tile["symbol"], fill=self.p.WHITE,
                                     font=("Segoe UI Semibold", 9 if w > 60 else 7))
                    if h > 34:
                        self.create_text(x + w / 2, y + h / 2 + 8, text=signed_pct(tile["change"]) + (" ·thin" if tile["thin"] else ""),
                                         fill=self.p.WHITE, font=("Segoe UI", 7))
                if tile["symbol"] in self.flagged and w > 16 and h > 16:
                    self.create_text(x + w - 4, y + 3, text="◆", anchor="ne", fill=self.p.GOLD, font=("Segoe UI", 8))
                self.tiles.append((x, y, x + w, y + h, tile))

    def tile_at(self, x: float, y: float) -> dict[str, Any] | None:
        return next((tile for left, top, right, bottom, tile in self.tiles if left <= x <= right and top <= y <= bottom), None)

    def click(self, event: Any) -> None:
        tile = self.tile_at(event.x, event.y)
        if tile:
            self.on_click(tile["symbol"], tile["asset"])

    def hover(self, event: Any) -> None:
        self.delete("hover")
        tile = self.tile_at(event.x, event.y)
        if not tile:
            return
        events = self.flagged.get(tile["symbol"])
        text = (f"{tile['symbol']}" + (f" · {tile['name']}" if tile.get("name") else "") + f" · {money(tile['price'])} · {signed_pct(tile['change'])} today"
                + (f" · ◆ {events} world event{'s' if events != 1 else ''} in its industry" if events else ""))
        label = self.create_text(event.x + 12, event.y + 12, text=text, anchor="nw", fill=self.p.WHITE, font=("Segoe UI", 9), tags="hover")
        box = self.bbox(label)
        if box[2] > self.winfo_width():
            self.move(label, -(box[2] - self.winfo_width()) - 16, 0)
            box = self.bbox(label)
        backing = self.create_rectangle(box[0] - 4, box[1] - 2, box[2] + 4, box[3] + 2, fill=self.p.PANEL, outline=self.p.LINE, tags="hover")
        self.tag_lower(backing, label)


def event_time(event: dict[str, Any]) -> float | None:
    try:
        return datetime.fromisoformat(str(event.get("observed_at")).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None
