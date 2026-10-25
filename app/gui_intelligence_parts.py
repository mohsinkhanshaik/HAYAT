"""Drawn pieces of the intelligence workspace: glyphs, flowing rows and the live feed."""
import tkinter as tk
from tkinter import ttk

from app.gui_resources import StringVar
from app.intelligence.classify import CATEGORIES, SEVERITY_COLOURS
from app.intelligence.view import relative_time

MAP_OCEAN, MAP_LAND, MAP_COAST, MAP_GRID, MAP_TEXT = '#0A1520', '#182A3B', '#2B4A66', '#112131', '#8FA6BA'


def blend(colour, background, amount):
    """Mix two #rrggbb colours; amount 0 keeps colour, 1 gives background."""
    first = [int(colour[index:index + 2], 16) for index in (1, 3, 5)]
    second = [int(background[index:index + 2], 16) for index in (1, 3, 5)]
    return '#' + ''.join(f'{round(a + (b - a) * amount):02x}' for a, b in zip(first, second))


def round_rect(canvas, x1, y1, x2, y2, radius=8, **options):
    radius = max(0, min(radius, (x2 - x1) / 2, (y2 - y1) / 2))
    points = [x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius, x2, y2 - radius, x2, y2,
              x2 - radius, y2, x1 + radius, y2, x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1]
    return canvas.create_polygon(points, smooth=True, **options)


def glyph(canvas, shape, x, y, radius, colour, tags=(), outline=''):
    """One map/list symbol; shape varies by category so colour is never the only cue."""
    r = radius
    if shape == 'triangle':
        return canvas.create_polygon(x, y - r * 1.2, x + r * 1.1, y + r * .8, x - r * 1.1, y + r * .8, fill=colour, outline=outline, tags=tags)
    if shape == 'flame':
        return canvas.create_polygon(x, y - r * 1.3, x + r, y + r * .2, x + r * .55, y + r, x - r * .55, y + r, x - r, y + r * .2,
                                     fill=colour, outline=outline, smooth=True, tags=tags)
    if shape == 'diamond':
        return canvas.create_polygon(x, y - r * 1.2, x + r * 1.2, y, x, y + r * 1.2, x - r * 1.2, y, fill=colour, outline=outline, tags=tags)
    if shape == 'square':
        return canvas.create_rectangle(x - r * .9, y - r * .9, x + r * .9, y + r * .9, fill=colour, outline=outline, tags=tags)
    if shape == 'star':
        s = r * .38
        return canvas.create_polygon(x, y - r * 1.3, x + s, y - s, x + r * 1.3, y, x + s, y + s, x, y + r * 1.3, x - s, y + s,
                                     x - r * 1.3, y, x - s, y - s, fill=colour, outline=outline, tags=tags)
    if shape == 'drop':
        return canvas.create_polygon(x, y - r * 1.4, x + r, y + r * .1, x + r * .6, y + r, x - r * .6, y + r, x - r, y + r * .1,
                                     fill=colour, outline=outline, smooth=True, tags=tags)
    if shape == 'ring':
        return canvas.create_oval(x - r, y - r, x + r, y + r, outline=colour, width=max(2, r * .45), fill=outline or '', tags=tags)
    if shape == 'camera':
        return canvas.create_rectangle(x - r * .8, y - r * .6, x + r * .8, y + r * .6, fill=colour, outline=outline, tags=tags)
    return canvas.create_oval(x - r, y - r, x + r, y + r, fill=colour, outline=outline, tags=tags)


def category_glyph(parent, category, background, size=16):
    canvas = tk.Canvas(parent, width=size, height=size, bg=background, highlightthickness=0, bd=0)
    title, colour, shape = CATEGORIES.get(category, CATEGORIES['other'])
    canvas.glyph = glyph(canvas, shape, size / 2, size / 2, size * .3, colour)
    return canvas


def draw_globe(canvas, colour, accent):
    size = int(canvas['width'])
    pad = 3
    canvas.create_oval(pad, pad, size - pad, size - pad, outline=colour, width=2)
    for inset in (size * .22, size * .38):
        canvas.create_oval(inset, pad, size - inset, size - pad, outline=colour, width=1)
    for fraction in (.35, .5, .65):
        y = size * fraction
        half = ((size / 2 - pad) ** 2 - (y - size / 2) ** 2) ** .5
        canvas.create_line(size / 2 - half, y, size / 2 + half, y, fill=colour)
    canvas.create_oval(size * .62, size * .2, size * .62 + 6, size * .2 + 6, fill=accent, outline='')


class FlowRow(tk.Frame):
    """Lays its child widgets left to right and wraps them to new rows by actual width."""

    def __init__(self, parent, background, gap=4, max_items=None, **options):
        super().__init__(parent, bg=background, **options)
        self.gap = gap
        #: Show at most this many (the first ones); the rest stay built but unplaced.
        self.max_items = max_items
        self.items = []
        self.pack_propagate(False)
        self.bind('<Configure>', lambda _event: self.arrange())

    def add(self, widget, index=None):
        self.items.insert(len(self.items) if index is None else index, widget)
        widget.bind('<Configure>', lambda _event: self.arrange(), add='+')
        return widget

    def remove(self, widget):
        if widget in self.items:
            self.items.remove(widget)
        widget.destroy()
        self.arrange()

    def arrange(self):
        width = max(1, self.winfo_width())
        top, left, row_height = 2, 0, 0
        shown = self.items if self.max_items is None else self.items[:self.max_items]
        for widget in self.items[len(shown):]:
            widget.place_forget()
        for widget in shown:
            needed = widget.winfo_reqwidth() + self.gap
            if left + needed > width and left:
                top, left, row_height = top + row_height + self.gap, 0, 0
            widget.place(x=left, y=top)
            left += needed
            row_height = max(row_height, widget.winfo_reqheight())
        height = top + row_height + 2
        if int(self.cget('height')) != height:
            self.configure(height=height)


class LiveFeed(tk.Frame):
    """Newest dated observations as drawn cards; a click selects the event."""

    CARD = 62
    #: "All" plus the six largest categories.
    CHIPS = 7

    def __init__(self, parent, palette, on_select, on_more):
        super().__init__(parent, bg=palette.PANEL, padx=10, pady=10)
        self.palette, self.on_select = palette, on_select
        self.rows, self.selected = [], None
        self._signature = None
        self.choice = StringVar(self, '')
        self._chips, self._more, self._more_keys = None, None, {}
        from tkinter import font as tkfont
        # Measured, not estimated: at 125% scaling 7 px a letter cut "RESTRICT" to "RESTRI".
        self._badge_font = tkfont.Font(self, family='Segoe UI Semibold', size=8)
        self._title_font = tkfont.Font(self, family='Segoe UI Semibold', size=10)
        self._sub_font = tkfont.Font(self, family='Segoe UI', size=9)
        self._time_font = tkfont.Font(self, family='Segoe UI', size=8)
        header = tk.Frame(self, bg=palette.PANEL)
        header.pack(fill='x')
        self.title = tk.Label(header, text='Live Global Feed', bg=palette.PANEL, fg=palette.WHITE, font=('Segoe UI Semibold', 13))
        self.title.pack(side='left')
        self.state = tk.Label(header, text='● Live', bg=palette.PANEL, fg='#4ADE80', font=('Segoe UI Semibold', 9))
        self.state.pack(side='right')
        self.chips = FlowRow(self, palette.PANEL, height=30)
        self.chips.pack(fill='x', pady=(8, 6))
        body = tk.Frame(self, bg=palette.PANEL)
        body.pack(fill='both', expand=True)
        self.canvas = tk.Canvas(body, bg=palette.PANEL, highlightthickness=0, bd=0, width=280)
        scroll = ttk.Scrollbar(body, command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.canvas.pack(side='left', fill='both', expand=True)
        self.canvas.bind('<Configure>', lambda _event: self.draw())
        self.canvas.bind('<Button-1>', self.click)
        self.canvas.bind('<MouseWheel>', lambda event: self.canvas.yview_scroll(-1 if event.delta > 0 else 1, 'units'))
        self.more = ttk.Button(self, text='View all events →', command=on_more, style='Compact.Hayat.TButton')
        self.more.pack(fill='x', pady=(6, 0))

    def set_rows(self, rows, total, counts, running):
        self.state.configure(text='● Live' if running else '● Paused', fg='#4ADE80' if running else self.palette.MUTED)
        self.title.configure(text=f'Live Global Feed ({total})')
        chips = [('', f"All ({sum(count for key, count in counts.items() if key != 'cameras')})")] + [(key, f"{CATEGORIES[key][0]} ({count})") for key, count in
                                                         sorted(counts.items(), key=lambda item: -item[1]) if key != 'cameras']
        if chips != self._chips:
            # "All" and the six largest categories as chips; the rest in one menu. Fifteen
            # chips took six rows (about 220 px) before the first event card.
            self._chips = chips
            for button in self.chips.items:
                button.destroy()
            self.chips.items = []
            shown, rest = chips[:self.CHIPS], chips[self.CHIPS:]
            for key, label in shown:
                self.chips.add(ttk.Radiobutton(self.chips, text=label, value=key, variable=self.choice,
                                               command=self.draw, style='Chip.Toolbutton'))
            self._more = None
            if rest:
                self._more = ttk.Menubutton(self.chips, text=f'More ({len(rest)}) ▾', style='Chip.Toolbutton')
                menu = tk.Menu(self._more, tearoff=False, bg=self.palette.PANEL_2, fg=self.palette.WHITE,
                               activebackground=self.palette.GOLD, activeforeground=self.palette.BLACK)
                for key, label in rest:
                    menu.add_radiobutton(label=label, value=key, variable=self.choice, command=self.draw)
                self._more.configure(menu=menu)
                self._more_keys = {key: label for key, label in rest}
                self.chips.add(self._more)
            self.chips.arrange()
        if self._more is not None:
            chosen = self._more_keys.get(self.choice.get())
            self._more.configure(text=f'{chosen} ▾' if chosen else f'More ({len(self._more_keys)}) ▾')
        if self.choice.get() and self.choice.get() not in counts:
            self.choice.set('')
        self.rows = rows
        self.draw()

    #: Cards in the "All" view; each category chip shows that category's own newest rows.
    ALL_LIMIT = 60

    #: At most this many of one category in "All", so 3,000 road works do not fill a world feed.
    PER_CATEGORY = 15

    def visible_rows(self):
        if not self.choice.get():
            shown, taken = [], {}
            for row in self.rows:
                if taken.get(row['category'], 0) < self.PER_CATEGORY:
                    shown.append(row)
                    taken[row['category']] = taken.get(row['category'], 0) + 1
                if len(shown) == self.ALL_LIMIT:
                    break
            return shown
        return [row for row in self.rows if row['category'] == self.choice.get()]

    def draw(self):
        p, canvas = self.palette, self.canvas
        rows = self.visible_rows()
        width = max(200, canvas.winfo_width())
        signature = (width, self.selected, [(row['id'], row.get('observed_at'), row['badge']) for row in rows],
                     relative_time(rows[0].get('observed_at')) if rows else '')
        if signature == self._signature:
            return
        self._signature = signature
        canvas.delete('all')
        card = blend(p.PANEL_2, p.PANEL, .35) if p.PANEL_2.startswith('#') and len(p.PANEL_2) == 7 else p.PANEL_2
        for index, row in enumerate(rows):
            top = index * self.CARD
            tag = 'card:' + row['id']
            selected = row['id'] == self.selected
            round_rect(canvas, 2, top + 3, width - 4, top + self.CARD - 3, 9, fill=card,
                       outline=p.GOLD if selected else p.LINE, width=2 if selected else 1, tags=(tag,))
            title, colour, shape = CATEGORIES[row['category']]
            canvas.create_oval(12, top + 17, 40, top + 45, outline=blend(colour, card, .45), width=2, tags=(tag,))
            glyph(canvas, shape, 26, top + 31, 7, colour, tags=(tag,))
            when = relative_time(row.get('observed_at'))
            badge, tone = row['badge'], SEVERITY_COLOURS[row['severity']]
            badge_width = 12 + self._badge_font.measure(badge)
            canvas.create_text(50, top + 20, text=fit(row['headline'], self._title_font, width - 70 - self._time_font.measure(when)),
                               anchor='w', fill=p.WHITE, font=self._title_font, tags=(tag,))
            canvas.create_text(50, top + 41, text=fit(row['subtitle'] or row['provider'], self._sub_font, width - 72 - badge_width),
                               anchor='w', fill=p.MUTED, font=self._sub_font, tags=(tag,))
            canvas.create_text(width - 12, top + 20, text=when, anchor='e', fill=p.MUTED, font=self._time_font, tags=(tag,))
            round_rect(canvas, width - 12 - badge_width, top + 33, width - 12, top + 51, 5, fill=blend(tone, card, .78),
                       outline=tone, tags=(tag,))
            canvas.create_text(width - 12 - badge_width / 2, top + 42, text=badge, fill=tone, font=('Segoe UI Semibold', 8), tags=(tag,))
        if not rows:
            canvas.create_text(width / 2, 40, text='No dated observations match\nthe current filters.', fill=p.MUTED,
                               justify='center', font=('Segoe UI', 10))
        canvas.configure(scrollregion=(0, 0, width, max(1, len(rows)) * self.CARD))

    def click(self, event):
        for item in self.canvas.find_overlapping(event.x, self.canvas.canvasy(event.y), event.x, self.canvas.canvasy(event.y)):
            for tag in self.canvas.gettags(item):
                if tag.startswith('card:'):
                    self.on_select(tag[5:])
                    return

    def mark(self, identifier):
        if identifier != self.selected:
            self.selected = identifier
            self.draw()


def fit(text, font, pixels):
    """The longest start of text that fits in pixels with an ellipsis, measured in the real font."""
    text = str(text or '')
    if font.measure(text) <= pixels:
        return text
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if font.measure(text[:middle].rstrip() + '…') <= pixels:
            low = middle
        else:
            high = middle - 1
    return text[:low].rstrip() + '…' if low else '…'


def clip(text, pixels, average=7.0):
    text = str(text or '')
    limit = max(4, int(pixels / average))
    return text if len(text) <= limit else text[:limit - 1].rstrip() + '…'
