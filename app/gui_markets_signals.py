"""Trade Center "Signals" view: which signals have told something about this market, and what moves it.

Two parts, one at a time, so each fits the centre pane at the owner's 1536 x 900 screen:

* **Signal lab** (app/market_signals.py): two cards, next day and next week, with the usual chance of
  ending higher, the chance with today's proven signals (only when some passed) and the later-third
  check; then every signal at both horizons: how often its calls were right against chance, its verdict
  after the luck test and what it says today. Choosing a row explains it.
* **What moves it** (app/market_influences.py): today's move split into "with the markets" and "its own";
  each linked market's correlation, move per 1 %, whether the link held and whether it leads, today's
  move and what the link implies; world events, scheduled events and unconfirmed reports.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any, Callable

from app.gui_depth import blend
from app.gui_resources import StringVar

UP, DOWN, AMBER = '#4ADE80', '#F05D5E', '#E8C46A'
STATUS = {'works': ('beat chance', UP), 'luck': ('maybe luck', AMBER), 'none': ('no edge', None), 'backwards': ('backwards', DOWN),
          'few': ('too few', None)}
PARTS = (('lab', 'Signal lab'), ('links', 'What moves it'))


def pct(value: float | None, signed: bool = False) -> str:
    if value is None:
        return '—'
    return f"{value:+.1f}%" if signed else f"{value:.0f}%"


def card_text(horizon: dict[str, Any]) -> tuple[str, str, int]:
    """(big line, small line, direction) for one horizon's card."""
    low, high = horizon['usual_interval_pct']
    usual = f"usual {horizon['usual_up_pct']:.0f}% ({low:.0f}–{high:.0f}%, {horizon['usual_samples']} periods)"
    if horizon['from_signals']:
        shift = horizon['odds_up_pct'] - horizon['usual_up_pct']
        return (f"{horizon['odds_up_pct']:.0f}% higher", f"{usual} · tested combination moves it {shift:+.1f} pts · "
                f"{horizon['holdout']['short']}", (shift > 0) - (shift < 0))
    if horizon.get('validation_passed'):
        return f"{horizon['usual_up_pct']:.0f}% higher", f"{usual} · tested signals make no call today · {horizon['holdout']['short']}", 0
    if horizon['working'] and horizon.get('withheld'):
        return f"{horizon['usual_up_pct']:.0f}% higher", f"{usual} · adjustments withheld · {horizon['holdout']['short']}", 0
    return f"{horizon['usual_up_pct']:.0f}% higher", f"{usual} · no signal beat chance, so nothing shifts them", 0


class SignalsPanel:
    def __init__(self, parent: tk.Misc, palette: Any, submit: Callable[[str, Callable[[], Any]], None], model: Any = None) -> None:
        p = self.palette = palette
        self.submit, self.model = submit, model
        self.signals_data: dict[str, Any] | None = None
        self.links_data: dict[str, Any] | None = None
        self.part = StringVar(parent, 'lab')
        self.frame = tk.Frame(parent, bg=p.PANEL)
        head = tk.Frame(self.frame, bg=p.PANEL)
        head.pack(fill='x', pady=(8, 0))
        for value, label in PARTS:
            ttk.Radiobutton(head, text=label, value=value, variable=self.part, style='Segment.Toolbutton',
                            command=self.show_part).pack(side='left', padx=(0, 4))
        self.explain_button = ttk.Button(head, text='Explain with HAYAT', style='Compact.Hayat.TButton', command=self.explain)
        self.explain_button.pack(side='right')
        self.head = tk.Label(self.frame, text='Testing ten signals on up to two years of daily prices…', bg=p.PANEL, fg=p.MUTED,
                             font=('Segoe UI', 8), anchor='w', justify='left')
        self.head.pack(fill='x', pady=(4, 0))
        self.head.bind('<Configure>', lambda event: self.head.configure(wraplength=max(200, event.width - 4)))
        # The note is packed first, from the bottom, so a short screen shrinks the table rather than hiding the
        # explanation a chosen row brings (at 1536 x 860 it had been pushed out of sight).
        from app.gui_theme import DarkScrolledText
        self.note = DarkScrolledText(self.frame, bg=blend(p.PANEL, p.BLACK, .35), fg=p.MUTED, font=('Segoe UI', 8), wrap='word', height=4,
                            relief='flat', padx=8, pady=6, highlightthickness=0, cursor='arrow')
        self.note.pack(side='bottom', fill='x', pady=(6, 0))
        self.note.configure(state='disabled')
        self.body = tk.Frame(self.frame, bg=p.PANEL)
        self.body.pack(fill='both', expand=True)
        self.lab = self._build_lab(self.body)
        self.influence = self._build_links(self.body)
        self.show_part()
        self.frame.bind('<Configure>', self._fit)

    def _fit(self, event):
        """Keep results visible when a short window cannot fit the full summaries."""
        compact = event.height < 350
        if compact == getattr(self, '_compact', None):
            return
        self._compact = compact
        if compact:
            self.head.pack_forget()
        else:
            self.head.pack(fill='x', pady=(4, 0), before=self.note.frame)
        self.note.configure(height=1 if compact else 4)
        self.card_row.pack_configure(pady=2 if compact else (6, 6))
        for _big, small in self.cards.values():
            small.master.configure(pady=2 if compact else 6)
            if compact:
                small.pack_forget()
            else:
                small.pack(fill='x')

    def _build_lab(self, parent: tk.Misc) -> tk.Frame:
        p = self.palette
        frame = tk.Frame(parent, bg=p.PANEL)
        cards = tk.Frame(frame, bg=p.PANEL)
        self.card_row = cards
        cards.pack(fill='x', pady=(6, 6))
        self.cards = {}
        for index, (key, label) in enumerate((('1d', 'Next day'), ('1w', 'Next week'))):
            card = tk.Frame(cards, bg=blend(p.PANEL, p.BLACK, .35), padx=10, pady=6)
            card.grid(row=0, column=index, sticky='nsew', padx=(0, 6) if index == 0 else (0, 0))
            cards.columnconfigure(index, weight=1, uniform='cards')
            top = tk.Frame(card, bg=card['bg'])
            top.pack(fill='x')
            tk.Label(top, text=label, bg=card['bg'], fg=p.MUTED, font=('Segoe UI', 8)).pack(side='left')
            big = tk.Label(top, text='—', bg=card['bg'], fg=p.WHITE, font=('Segoe UI Semibold', 14))
            big.pack(side='right')
            small = tk.Label(card, text='', bg=card['bg'], fg=p.MUTED, font=('Segoe UI', 8), anchor='w', justify='left')
            small.pack(fill='x')
            small.bind('<Configure>', lambda event, label=small: label.configure(wraplength=max(160, event.width - 4)))
            self.cards[key] = (big, small)
        table = tk.Frame(frame, bg=p.PANEL)
        table.pack(fill='both', expand=True)
        self.tree = ttk.Treeview(table, columns=('horizon', 'now', 'calls', 'right', 'chance', 'verdict'), height=5,
                                 selectmode='browse', style='Markets.Treeview')
        for key, label, width, anchor in (('#0', 'Signal', 170, 'w'), ('horizon', 'For', 70, 'w'), ('now', 'Today', 78, 'w'),
                                          ('calls', 'Calls', 50, 'e'), ('right', 'Right', 56, 'e'), ('chance', 'Chance', 64, 'e'),
                                          ('verdict', 'Verdict', 92, 'w')):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, minwidth=44, anchor=anchor, stretch=key in ('#0', 'verdict'))
        for status, (_text, colour) in STATUS.items():
            self.tree.tag_configure(status, foreground=colour or p.MUTED)
        self._scroll_table(table, self.tree)
        self.tree.bind('<<TreeviewSelect>>', lambda _event: self.signal_selected())
        return frame

    def _build_links(self, parent: tk.Misc) -> tk.Frame:
        p = self.palette
        frame = tk.Frame(parent, bg=p.PANEL)
        card = tk.Frame(frame, bg=blend(p.PANEL, p.BLACK, .35), padx=10, pady=6)
        card.pack(fill='x', pady=(6, 6))
        top = tk.Frame(card, bg=card['bg'])
        top.pack(fill='x')
        tk.Label(top, text='Today', bg=card['bg'], fg=p.MUTED, font=('Segoe UI', 8)).pack(side='left')
        self.split_big = tk.Label(top, text='—', bg=card['bg'], fg=p.WHITE, font=('Segoe UI Semibold', 14))
        self.split_big.pack(side='right')
        self.split_small = tk.Label(card, text='', bg=card['bg'], fg=p.MUTED, font=('Segoe UI', 8), anchor='w', justify='left')
        self.split_small.pack(fill='x')
        self.split_small.bind('<Configure>', lambda event: self.split_small.configure(wraplength=max(200, event.width - 4)))
        table = tk.Frame(frame, bg=p.PANEL)
        table.pack(fill='both', expand=True)
        self.links = ttk.Treeview(table, columns=('corr', 'beta', 'held', 'lead', 'today', 'implied'), height=6,
                                  selectmode='browse', style='Markets.Treeview')
        for key, label, width, anchor in (('#0', 'Market', 130, 'w'), ('corr', 'Moves with', 96, 'e'), ('beta', 'Per 1%', 60, 'e'),
                                          ('held', 'Link', 70, 'w'), ('lead', 'Leads?', 62, 'w'), ('today', 'Today', 64, 'e'),
                                          ('implied', 'Implies here', 100, 'e')):
            self.links.heading(key, text=label)
            self.links.column(key, width=width, minwidth=44, anchor=anchor, stretch=key == '#0')
        self.links.tag_configure('with', foreground=UP)
        self.links.tag_configure('against', foreground=DOWN)
        self.links.tag_configure('loose', foreground=p.MUTED)
        self._scroll_table(table, self.links)
        self.links.bind('<<TreeviewSelect>>', lambda _event: self.link_selected())
        return frame

    @staticmethod
    def _scroll_table(parent, tree):
        horizontal = ttk.Scrollbar(parent, orient='horizontal', command=tree.xview)
        horizontal.pack(side='bottom', fill='x')
        vertical = ttk.Scrollbar(parent, orient='vertical', command=tree.yview)
        vertical.pack(side='right', fill='y')
        tree.configure(xscrollcommand=horizontal.set, yscrollcommand=vertical.set)
        tree.pack(side='left', fill='both', expand=True)

    def show_part(self) -> None:
        """Show the signal lab or what moves it; each brings its own summary and notes."""
        lab = self.part.get() == 'lab'
        (self.influence if lab else self.lab).pack_forget()
        (self.lab if lab else self.influence).pack(fill='both', expand=True)
        data = self.signals_data if lab else self.links_data
        self.head.configure(text=self.head_text(data, lab))
        self.say((data['basis'] if lab else self.extra_text(data)) if data else '')

    @staticmethod
    def head_text(data: dict[str, Any] | None, lab: bool) -> str:
        if not data:
            return ('Testing ten signals on up to two years of daily prices…' if lab
                    else 'Lining up its daily moves with eight markets that often carry others…')
        return data['summary'] + (f"  ·  {data['history_days']} days" + (' · ● live price' if data['live'] else '')
                                  + f" · updated {data['computed_at'][11:16]} UTC")

    def say(self, text: str) -> None:
        self.note._smooth_scroller.cancel()
        self.note.configure(state='normal')
        self.note.delete('1.0', 'end')
        self.note.insert('1.0', text)
        self.note.configure(state='disabled')
        self.note.yview_moveto(0)

    def said(self) -> str:
        return self.note.get('1.0', 'end').strip()

    def reset(self) -> None:
        """A new market: empty the view until its numbers arrive."""
        self.signals_data = self.links_data = None
        self.tree.delete(*self.tree.get_children())
        self.links.delete(*self.links.get_children())
        for big, small in self.cards.values():
            big.configure(text='—', fg=self.palette.WHITE)
            small.configure(text='')
        self.split_big.configure(text='—', fg=self.palette.WHITE)
        self.split_small.configure(text='')
        self.show_part()

    # ------------------------------------------------------------------ signals
    def show_signals(self, value: dict[str, Any]) -> None:
        p = self.palette
        first = self.signals_data is None
        self.signals_data = value
        for horizon in value['horizons']:
            big, small = self.cards[horizon['key']]
            text, detail, direction = card_text(horizon)
            big.configure(text=text, fg=UP if direction > 0 else DOWN if direction < 0 else p.WHITE)
            small.configure(text=detail)
        tree = self.tree
        selected = tree.selection()
        position = tree.yview()[0], tree.xview()[0]
        tree.delete(*tree.get_children())
        for row in value['signals']:
            tree.insert('', 'end', iid=f"{row['key']}:{row['horizon']}", text=row['label'],
                        values=(row['horizon_label'], row['now_text'], row['calls'], pct(row['hit_pct']), pct(row['chance_pct']),
                                STATUS[row['status']][0]), tags=(row['status'],))
        if selected and selected[0] in tree.get_children():
            tree.selection_set(selected[0])
        tree.yview_moveto(position[0])
        tree.xview_moveto(position[1])
        if self.part.get() == 'lab':
            self.head.configure(text=self.head_text(value, True))
            if first:
                self.say(value['summary'] + '\n' + '\n'.join(
                    f"{horizon['label'].capitalize()}: {horizon['holdout']['short']}" for horizon in value['horizons'])
                    + '\n' + value['basis'])

    def signal_selected(self) -> None:
        selection = self.tree.selection()
        row = next((row for row in (self.signals_data or {}).get('signals', [])
                    if f"{row['key']}:{row['horizon']}" == (selection or [''])[0]), None)
        if not row:
            return
        edge = f"{row['edge_pts']:+.1f} points" if row['edge_pts'] is not None else '—'
        self.say(f"{row['label']} ({row['horizon_label']}): calls that {row['about']}.\n"
                 f"Right {pct(row['hit_pct'])} of {row['calls']} calls, against {pct(row['chance_pct'])} expected by chance ({edge}); "
                 f"chance of doing this well by luck {row['p_value']:.3f} before allowing for the other tries.\n"
                 f"Verdict: {row['verdict']}.\nToday: {row['now_text']}.")

    # ------------------------------------------------------------------ links
    def show_influences(self, value: dict[str, Any]) -> None:
        p = self.palette
        first = self.links_data is None
        self.links_data = value
        model = value.get('model') or {}
        split = model.get('today')
        if split:
            self.split_big.configure(text=f"{split['actual_pct']:+.2f}%", fg=UP if split['actual_pct'] > 0 else DOWN
                                     if split['actual_pct'] < 0 else p.WHITE)
            self.split_small.configure(text=f"about {split['with_markets_pct']:+.2f}% goes with the wider markets and "
                                            f"{split['own_pct']:+.2f}% is its own (news, flows, anything else) · these markets explain "
                                            f"about {model['r2_pct']:.0f}% of its daily swings over {model['days']} days")
        else:
            self.split_big.configure(text=f"{value['today_pct']:+.2f}%", fg=p.WHITE)
            self.split_small.configure(text='too little shared history today to split the move')
        links = self.links
        selected = links.selection()
        position = links.yview()[0], links.xview()[0]
        links.delete(*links.get_children())
        for row in value['drivers']:
            corr = row['corr_90']
            tag = 'loose' if row['strength'] in ('weak', 'none', 'unknown') else 'with' if (corr or 0) > 0 else 'against'
            today = row['today']
            links.insert('', 'end', iid=row['symbol'], text=row['label'],
                         values=('—' if corr is None else f"{corr:+.2f} {row['strength']}",
                                 '—' if row['beta_90'] is None else f"{row['beta_90']:+.2f}",
                                 'steady' if row['steady'] else 'changed', 'yes' if row['lead']['significant'] else 'no',
                                 pct(today['driver_pct'], True),
                                 pct(today['implied_pct'], True) if row['strength'] in ('strong', 'moderate') else '—'),
                         tags=(tag,))
        if selected and selected[0] in links.get_children():
            links.selection_set(selected[0])
        links.yview_moveto(position[0])
        links.xview_moveto(position[1])
        if self.part.get() == 'links':
            self.head.configure(text=self.head_text(value, False))
            if first:
                self.say(self.extra_text(value))

    @staticmethod
    def extra_text(value: dict[str, Any]) -> str:
        """World events, scheduled events, rumours and the markets that could not be measured."""
        lines = [f"{item['factor']}: {item['reading']}" for item in value.get('other_factors') or []]
        lines += ['Not measured · ' + gap for gap in value.get('gaps') or []]
        return '\n'.join(lines + [value['basis']])

    def link_selected(self) -> None:
        selection = self.links.selection()
        row = next((row for row in (self.links_data or {}).get('drivers', []) if row['symbol'] == (selection or [''])[0]), None)
        if not row:
            return
        stress, lead = row['stress'], row['lead']
        self.say(f"{row['text']}\nOver a year: correlation {row['corr_365'] if row['corr_365'] is not None else '—'} "
                 f"({row['days']} shared days). On {row['label']}'s worst tenth of days ({stress['driver_move_pct']:+.1f}% on average) "
                 f"{self.links_data['symbol']} moved {stress['move_pct']:+.2f}% on average; its usual swing is ±{stress['usual_abs_pct']}%.\n"
                 f"Next-day lead: correlation {lead['corr'] if lead['corr'] is not None else '—'} over {lead['days']} days "
                 f"({'past the strict bar' if lead['significant'] else 'not past the strict bar for eight markets tried'}).\n"
                 + self.extra_text(self.links_data))

    # ------------------------------------------------------------------ explain
    def explain(self) -> None:
        if not self.signals_data:
            return
        if self.model is None:
            self.say("HAYAT's local model is not available in this session; the numbers above stand on their own.")
            return
        from app.market_signals import explain
        model, value = self.model, self.signals_data
        self.say('HAYAT is reading the numbers…')
        self.submit('signals_explain', lambda: {'symbol': value['symbol'], 'text': explain(model, value)})

    def show_explain(self, value: dict[str, Any]) -> None:
        if self.signals_data and value['symbol'] == self.signals_data['symbol']:
            self.say('HAYAT: ' + value['text'] + '\n(From the numbers above; not advice.)')
