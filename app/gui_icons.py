"""Small local vector icons; the text label always carries the meaning."""
from __future__ import annotations

import re
import tkinter as tk
from tkinter import ttk


def icon_kind(label: str) -> str:
    text = label.casefold()
    if text.startswith('zoom'):
        return 'zoom_out' if '−' in text or '-' in text else 'zoom_in'
    for kind, words in (
        # Checked first: whole phrases that would otherwise take a word-level icon below.
        ("help", ("what can hayat do", "help", "how do i", "guide")),
        ("download", ("download", "downloads", "transfers", "web activity")),
        ("bolt", ("automation", "automations", "beast mode", "quick action", "power", "tools", "tool")),
        ("list", ("commands", "latest", "more actions", "chat actions", "list", "queue", "in view")),
        ("expand", ("overview", "expand", "full screen", "fullscreen")),
        ("stop", ("stop", "pause", "close", "cancel", "clear", "turn off", "delete", "reject")),
        ("activity", ("activity", "transparency", "audit", "running")),
        ("globe", ("global", "intelligence", "osiris", "world", "map", "region")),
        ("alert", ("alert", "alerts")),
        ("income", ("business", "revenue", "income", "wallet", "invoice", "money", "profit", "rate", "earn", "sales")),
        ("grid", ("all features", "toolbox", "features", "mesh", "points view", "workspace", "workspaces")),
        ("book", ("learn", "learning", "language", "knowledge", "quran", "lesson")),
        ("task", ("tasks", "task", "workflow")),
        ("theme", ("appearance", "theme", "contrast")),
        ("settings", ("settings", "preferences", "configure", "customize")),
        ("lock", ("security", "privacy", "child mode", "remote")),
        ("check", ("health", "self check", "doctor", "confirm", "correct", "verify")),
        ("gallery", ("gallery", "photos")),
        ("history", ("history", "conversations", "recent")),
        ("image", ("image", "images", "picture", "draw", "snapshot", "still", "samples")),
        ("video", ("video", "videos", "clip")),
        ("chat", ("chat", "conversation")),
        ("code", ("code", "github")),
        ("eye", ("vision", "eye", "camera", "look", "screen", "watch", "streams", "tracking", "track")),
        ("link", ("source", "publisher", "open link", "website")),
        ("copy", ("copy", "duplicate")),
        ("cloud", ("weather", "forecast")),
        ("pin", ("focus", "location", "point", "near", "nearby")),
        ("voice", ("voice", "speech", "speaker", "microphone", "mic", "talk", "speak",
                   "hands-free", "hands free")),
        ("brain", ("brain", "plan", "operator", "reason", "analyze", "analyse", "assess", "understand", "scene")),
        ("search", ("research", "search", "osint", "inspect", "find")),
        ("model", ("model", "models", "load", "memory", "memories")),
        ("file", ("file", "document", "documents", "save", "export", "attach", "paper", "brief", "report", "transcript",
                  "summarize", "summarise", "details")),
        ("code", ("raw", "evidence", "json")),
        ("play", ("start", "run", "replay", "resume", "send", "live", "turn on", "listen", "hear")),
        ("refresh", ("refresh", "reset", "restore")),
    ):
        if any(re.search(rf"(?<!\w){re.escape(word)}(?!\w)", text) for word in words):
            return kind
    return "tool"


def icon_image(widget: tk.Misc, kind: str, colour: str, size: int = 18):
    from PIL import Image, ImageDraw, ImageTk

    root = widget.winfo_toplevel()._root()
    cache = getattr(root, "_hayat_icons", None)
    if cache is None:
        root._hayat_icons = cache = {}

    size = max(14, round(size * getattr(root, "_hayat_text_scale", 1.0)))

    def make(kind, colour):
        key = (kind, colour, size)
        if key in cache:
            return cache[key]
        picture = Image.new("RGBA", (48, 48))
        d = ImageDraw.Draw(picture)
        def line(points):
            d.line(points, fill=colour, width=4)
        if kind == "globe":
            d.ellipse((5, 5, 43, 43), outline=colour, width=3)
            d.ellipse((15, 5, 33, 43), outline=colour, width=3)
            line([(6, 24), (42, 24)])
            d.arc((5, 11, 43, 36), 0, 360, fill=colour, width=2)
        elif kind == "income":
            d.rounded_rectangle((4, 12, 44, 40), radius=5, outline=colour, width=3)
            d.ellipse((18, 19, 30, 33), outline=colour, width=3)
            line([(10, 7), (38, 7)])
        elif kind == "expand":
            for points in (((5, 18), (5, 5), (18, 5)), ((30, 5), (43, 5), (43, 18)),
                           ((5, 30), (5, 43), (18, 43)), ((30, 43), (43, 43), (43, 30))):
                line(points)
        elif kind == "grid":
            for x, y in ((5, 5), (27, 5), (5, 27), (27, 27)):
                d.rounded_rectangle((x, y, x+16, y+16), radius=3, outline=colour, width=3)
        elif kind == "book":
            line([(24, 10), (14, 5), (4, 5), (4, 37), (14, 37), (24, 42), (34, 37), (44, 37), (44, 5), (34, 5), (24, 10), (24, 42)])
        elif kind == "task":
            d.rounded_rectangle((8, 5, 40, 43), radius=3, outline=colour, width=3)
            for y in (15, 25, 35):
                line([(14, y), (17, y+3), (22, y-3)])
                line([(27, y), (34, y)])
        elif kind == "activity":
            line([(3, 25), (12, 25), (19, 8), (28, 40), (35, 20), (45, 20)])
        elif kind == "chat":
            d.rounded_rectangle((4, 5, 44, 34), radius=8, outline=colour, width=4)
            line([(12, 34), (10, 44), (25, 34)])
        elif kind == "code":
            line([(16, 9), (4, 24), (16, 39)])
            line([(32, 9), (44, 24), (32, 39)])
            line([(27, 6), (21, 42)])
        elif kind == "eye":
            line([(3, 24), (14, 12), (24, 8), (34, 12), (45, 24), (34, 36), (24, 40), (14, 36), (3, 24)])
            d.ellipse((17, 17, 31, 31), outline=colour, width=4)
        elif kind == "voice":
            d.rounded_rectangle((17, 4, 31, 29), radius=7, outline=colour, width=4)
            d.arc((10, 12, 38, 38), 0, 180, fill=colour, width=4)
            line([(24, 38), (24, 44)])
            line([(16, 44), (32, 44)])
        elif kind in ("search", "zoom_in", "zoom_out"):
            d.ellipse((5, 4, 32, 31), outline=colour, width=4)
            line([(29, 29), (43, 43)])
            if kind != 'search':
                line([(11, 17), (26, 17)])
                if kind == 'zoom_in':
                    line([(18, 10), (18, 24)])
        elif kind == 'alert':
            d.arc((12, 6, 36, 34), 180, 360, fill=colour, width=4)
            line([(12, 18), (12, 31), (7, 36), (41, 36), (36, 31), (36, 18)])
            d.arc((19, 33, 29, 43), 0, 180, fill=colour, width=4)
        elif kind == "model":
            for y in (5, 18, 31):
                d.rounded_rectangle((6, y, 42, y+11), radius=3, outline=colour, width=3)
                d.ellipse((11, y+4, 14, y+7), fill=colour)
        elif kind == "image":
            d.rounded_rectangle((4, 6, 44, 42), radius=5, outline=colour, width=4)
            d.ellipse((29, 12, 36, 19), fill=colour)
            line([(9, 34), (18, 24), (24, 30), (30, 22), (40, 34)])
        elif kind == "video":
            d.rounded_rectangle((4, 8, 44, 40), radius=5, outline=colour, width=4)
            d.polygon([(19, 15), (19, 33), (34, 24)], fill=colour)
        elif kind == "gallery":
            d.rounded_rectangle((4, 11, 35, 42), radius=3, outline=colour, width=3)
            d.rounded_rectangle((13, 5, 44, 35), radius=3, outline=colour, width=3)
            line([(18, 29), (26, 20), (31, 25), (37, 17)])
        elif kind == "download":
            line([(24, 4), (24, 30)])
            line([(13, 20), (24, 31), (35, 20)])
            line([(5, 30), (5, 42), (43, 42), (43, 30)])
        elif kind == "history":
            d.arc((7, 7, 41, 41), 45, 300, fill=colour, width=4)
            line([(7, 11), (7, 23), (18, 21)])
            line([(24, 13), (24, 24), (33, 29)])
        elif kind == "brain":
            d.arc((5, 5, 27, 42), 70, 250, fill=colour, width=4)
            d.arc((21, 5, 43, 42), 220, 250, fill=colour, width=4)
            line([(24, 8), (24, 40)])
            line([(10, 21), (19, 24)])
            line([(29, 24), (38, 21)])
        elif kind == "settings":
            d.ellipse((15, 15, 33, 33), outline=colour, width=4)
            for x1, y1, x2, y2 in ((24, 3, 24, 13), (24, 35, 24, 45),
                                    (3, 24, 13, 24), (35, 24, 45, 24),
                                    (9, 9, 16, 16), (32, 32, 39, 39),
                                    (39, 9, 32, 16), (16, 32, 9, 39)):
                line([(x1, y1), (x2, y2)])
        elif kind == "theme":
            d.ellipse((5, 5, 43, 43), outline=colour, width=4)
            d.pieslice((10, 10, 38, 38), 90, 270, fill=colour)
        elif kind == "check":
            d.ellipse((5, 5, 43, 43), outline=colour, width=4)
            line([(13, 24), (21, 32), (36, 16)])
        elif kind == "lock":
            d.arc((13, 4, 35, 32), 180, 360, fill=colour, width=4)
            d.rounded_rectangle((8, 22, 40, 43), radius=4, outline=colour, width=4)
            d.ellipse((22, 29, 26, 33), fill=colour)
        elif kind == "file":
            line([(9, 4), (29, 4), (39, 14), (39, 44), (9, 44), (9, 4)])
            line([(28, 5), (28, 16), (39, 16)])
            line([(16, 25), (32, 25)])
            line([(16, 34), (32, 34)])
        elif kind == "stop":
            d.rounded_rectangle((10, 10, 38, 38), radius=3, outline=colour, width=4)
        elif kind == "play":
            d.polygon([(13, 6), (42, 24), (13, 42)], outline=colour, width=4)
        elif kind == "refresh":
            d.arc((7, 7, 40, 40), 35, 325, fill=colour, width=4)
            line([(29, 7), (41, 7), (41, 19)])
        elif kind == "cloud":
            d.arc((6, 18, 26, 38), 90, 270, fill=colour, width=4)
            d.arc((14, 7, 38, 31), 180, 360, fill=colour, width=4)
            d.arc((26, 16, 44, 38), 270, 90, fill=colour, width=4)
            line([(16, 38), (35, 38)])
        elif kind == "pin":
            d.ellipse((12, 4, 36, 28), outline=colour, width=4)
            d.ellipse((20, 12, 28, 20), fill=colour)
            line([(15, 24), (24, 44), (33, 24)])
        elif kind == "copy":
            d.rounded_rectangle((15, 14, 43, 44), radius=4, outline=colour, width=4)
            line([(33, 5), (5, 5), (5, 35)])
        elif kind == "bolt":
            d.polygon([(28, 3), (10, 27), (22, 27), (18, 45), (38, 19), (26, 19)], outline=colour, width=4)
        elif kind == "help":
            d.ellipse((4, 4, 44, 44), outline=colour, width=4)
            d.arc((16, 11, 32, 27), 180, 90, fill=colour, width=4)
            line([(24, 27), (24, 31)])
            d.ellipse((21, 35, 27, 41), fill=colour)
        elif kind == "list":
            for y in (10, 24, 38):
                d.ellipse((5, y - 3, 11, y + 3), fill=colour)
                line([(17, y), (43, y)])
        elif kind == "link":
            d.rounded_rectangle((5, 12, 36, 43), radius=4, outline=colour, width=4)
            line([(22, 26), (42, 6)])
            line([(28, 6), (42, 6), (42, 20)])
        else:
            points = [(8, 12), (24, 5), (40, 12), (8, 36), (24, 42), (40, 36)]
            for x, y in points:
                line([(24, 24), (x, y)])
                d.ellipse((x-4, y-4, x+4, y+4), fill=colour)
            d.ellipse((18, 18, 30, 30), outline=colour, width=3)
        cache[key] = ImageTk.PhotoImage(picture.resize((size, size), Image.Resampling.LANCZOS), master=root)
        return cache[key]

    return make(kind, colour)


def apply_icons(widget: tk.Misc, palette) -> None:
    def visit(item):
        if isinstance(item, (tk.Button, ttk.Button)):
            text = str(item.cget("text"))
            if text and len(text.strip()) > 1:
                # A window may choose a button's icon; a later app-wide pass keeps that choice.
                kind = getattr(item, "_hayat_icon", None) or icon_kind(text)
                accent = isinstance(item, ttk.Button) and str(item.cget("style")).endswith("HayatAccent.TButton")
                if accent:
                    colour = palette.BLACK
                elif kind == "stop":
                    colour = palette.ALERT
                elif kind in {"play", "check"}:
                    colour = palette.OLIVE_SOFT
                elif kind in {"eye", "image", "video"}:
                    colour = palette.HOLO
                elif kind in {"brain", "theme"}:
                    colour = palette.GOLD_SOFT
                else:
                    colour = palette.ROYAL_SOFT
                item.configure(image=icon_image(item, kind, colour), compound=getattr(item, "_hayat_compound", "left"))
        if isinstance(item, tk.Label) and hasattr(item, "_hayat_icon_kind"):
            item.configure(image=icon_image(item, item._hayat_icon_kind, str(item.cget("fg")), 20), text="", width=0)
        for child in item.winfo_children():
            visit(child)
    visit(widget)


class CameraEye(tk.Canvas):
    """A camera indicator: closed when off, an iris when capturing."""
    def __init__(self, parent, palette):
        super().__init__(parent, width=64, height=38, bg=palette.PANEL, highlightthickness=0)
        self.palette, self.active = palette, False
        self.bind("<Configure>", lambda event: self.refresh_theme())
        self.refresh_theme()

    def set_active(self, active):
        if bool(active) != self.active:
            self.active = bool(active)
            self.refresh_theme()

    def refresh_theme(self):
        p = self.palette
        self.configure(bg=p.PANEL)
        self.delete("all")
        colour = p.HOLO if self.active else p.MUTED
        if self.active:
            self.create_polygon(4, 19, 18, 6, 32, 3, 46, 6, 60, 19, 46, 32,
                                32, 35, 18, 32, smooth=True, outline=colour, fill=p.BLACK, width=2)
            self.create_oval(21, 8, 43, 30, outline=colour, fill=p.PANEL_2, width=2)
            self.create_oval(27, 14, 37, 24, fill=p.GOLD, outline="")
            self.create_oval(28, 13, 31, 16, fill=p.WHITE, outline="")
        else:
            self.create_line(5, 16, 19, 25, 32, 29, 46, 25, 59, 16, smooth=True, fill=colour, width=2)
            for x, y in ((14, 23), (27, 28), (40, 28), (53, 23)):
                self.create_line(x, y, x-2, y+6, fill=colour, width=2)
