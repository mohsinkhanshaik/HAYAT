"""The download icon in HAYAT's header, and the Downloads window it opens.

The icon shows a badge with the number of downloads in progress and its arrow moves while
something downloads. The window has three tabs:
* **Active:** each download in progress: what, from where, saved where, how far, how fast.
* **History:** every file, model and video downloaded (kept across restarts), with where it came
  from and where it was saved; "Show in folder" opens Explorer at the file.
* **Web activity:** every request HAYAT made this session (feeds, prices, maps, news, research),
  newest first, and a summary per site: how many requests, how much data, how fast, for what.
"""
from __future__ import annotations

import logging
import math
import os
import subprocess
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import ttk
from typing import Any

from app.gui_resources import StringVar
from app.logging import log_swallowed
from app.gui_windows import place_over
from app.transfers import transfers
from app.gui_fonts import DISPLAY

_log = logging.getLogger(__name__)
UP, DOWN, AMBER = '#4ADE80', '#F05D5E', '#E8C46A'
KIND_LABEL = {'file': 'File', 'model': 'AI model', 'video': 'Video', 'web': 'Web'}
STATUS_COLOUR = {'done': UP, 'failed': DOWN, 'blocked': DOWN, 'cancelled': AMBER, 'active': AMBER}


def size(value: Any) -> str:
    value = float(value or 0)
    for unit, step in (('GB', 1e9), ('MB', 1e6), ('KB', 1e3)):
        if value >= step:
            return f'{value / step:.1f} {unit}'
    return f'{int(value)} B'


def local_time(stamp: str) -> str:
    try:
        return datetime.fromisoformat(stamp).astimezone().strftime('%d %b %H:%M:%S')
    except (TypeError, ValueError):
        return ''


class DownloadsButton(tk.Canvas):
    """The header icon: an arrow into a tray, a badge with the active count, moving while something downloads."""

    def __init__(self, parent: Any, palette: Any, command: Any) -> None:
        super().__init__(parent, width=40, height=32, bg=palette.PANEL, highlightthickness=0, cursor='hand2')
        self.p, self.command, self._phase, self._hover = palette, command, 0.0, False
        self.bind('<Button-1>', lambda _event: command())
        self.bind('<Enter>', lambda _event: self._set_hover(True))
        self.bind('<Leave>', lambda _event: self._set_hover(False))
        self._tip = None
        self.draw()
        self._job = self.after(120, self._tick)

    def _set_hover(self, on: bool) -> None:
        self._hover = on
        if on:
            summary = transfers().summary()
            text = (f"Downloads · {summary['active']} in progress" if summary['active'] else 'Downloads')
            text += f" · {summary['web_requests']:,} web requests ({size(summary['web_bytes'])}) this session"
            self._tip = tk.Toplevel(self)
            self._tip.wm_overrideredirect(True)
            tk.Label(self._tip, text=text, bg=self.p.PANEL_2, fg=self.p.WHITE, font=('Segoe UI', 9), padx=8, pady=4).pack()
            self._tip.geometry(f'+{self.winfo_rootx() - 120}+{self.winfo_rooty() + 36}')
        elif self._tip is not None:
            self._tip.destroy()
            self._tip = None
        self.draw()

    def _tick(self) -> None:
        if not self.winfo_exists():
            return
        self._phase += .35
        self.draw()
        self._job = self.after(120, self._tick)

    def draw(self) -> None:
        self.delete('all')
        active = transfers().summary()['active']
        colour = AMBER if active else (self.p.WHITE if self._hover else self.p.MUTED)
        bob = 3 * math.sin(self._phase) if active else 0
        self.create_line(18, 5 + bob, 18, 19 + bob, fill=colour, width=2.5)
        self.create_line(12, 13 + bob, 18, 19 + bob, 24, 13 + bob, fill=colour, width=2.5, capstyle='round', joinstyle='round')
        self.create_line(7, 19, 7, 26, 29, 26, 29, 19, fill=colour, width=2.5, capstyle='round', joinstyle='round')
        if active:
            self.create_oval(25, 1, 39, 15, fill=DOWN, outline=self.p.PANEL)
            self.create_text(32, 8, text=str(min(active, 9)), fill='#FFFFFF', font=('Segoe UI Semibold', 8))


class DownloadsWindow(tk.Toplevel):
    def __init__(self, gui: Any) -> None:
        super().__init__(gui.root)
        self.withdraw()
        self.gui, self.p = gui, type(gui)
        p = self.p
        self._closed = False
        self.kind = StringVar(self, 'all')
        self.filter = StringVar(self, '')
        self.history_rows: list[dict[str, Any]] = []
        self.title('HAYAT · Downloads & web activity')
        self.configure(bg=p.BLACK)
        self.minsize(900, 560)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.bind('<Escape>', lambda _event: self.close())
        header = tk.Frame(self, bg=p.BLACK, padx=16, pady=10)
        header.pack(fill='x')
        tk.Label(header, text='Downloads & web activity', bg=p.BLACK, fg=p.WHITE, font=(DISPLAY, 18), anchor='w').pack(fill='x')
        self.totals = tk.Label(header, text='', bg=p.BLACK, fg=p.MUTED, font=('Segoe UI', 9), anchor='w')
        self.totals.pack(fill='x')
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill='both', expand=True, padx=16, pady=(0, 8))
        self.tabs.add(self._build_active(), text='Active')
        self.tabs.add(self._build_history(), text='History')
        self.tabs.add(self._build_web(), text='Web activity')
        self.footer = tk.Label(self, text='Kept on this computer only. Web addresses are shown without their query, so keys and tokens never '
                                          'appear here. History: data\\logs\\transfers.jsonl.',
                               bg=p.PANEL, fg=p.MUTED, font=('Segoe UI', 8), anchor='w', padx=14, pady=4)
        self.footer.pack(side='bottom', fill='x')
        transfers().listeners.append(self._changed)
        place_over(self, gui.root, (1200, 760))
        self.deiconify()
        self.refresh_history()
        self._tick_id = self.after(50, self._tick)

    def _tree(self, parent: Any, columns: tuple, height: int = 14) -> ttk.Treeview:
        frame = tk.Frame(parent, bg=self.p.BLACK)
        frame.pack(fill='both', expand=True)
        tree = ttk.Treeview(frame, columns=[key for key, _label, _width in columns[1:]], selectmode='browse', height=height)
        for key, label, width in columns:
            tree.heading(key, text=label)
            tree.column(key, width=width, stretch=key in ('#0', 'path', 'what'))
        for status, colour in STATUS_COLOUR.items():
            tree.tag_configure(status, foreground=colour)
        scroll = ttk.Scrollbar(frame, command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        tree.pack(side='left', fill='both', expand=True)
        return tree

    def _build_active(self) -> tk.Frame:
        frame = tk.Frame(self.tabs, bg=self.p.BLACK)
        self.active_note = tk.Label(frame, text='', bg=self.p.BLACK, fg=self.p.MUTED, font=('Segoe UI', 9), anchor='w')
        self.active_note.pack(fill='x', pady=(8, 4))
        self.active_tree = self._tree(frame, (('#0', 'Downloading', 260), ('progress', 'Progress', 380), ('speed', 'Speed', 90),
                                              ('source', 'From', 170), ('destination', 'Saving to', 280)))
        return frame

    def _build_history(self) -> tk.Frame:
        p = self.p
        frame = tk.Frame(self.tabs, bg=p.BLACK)
        row = tk.Frame(frame, bg=p.BLACK)
        row.pack(fill='x', pady=(8, 4))
        for value, label in (('all', 'All'), ('file', 'Files'), ('model', 'AI models'), ('video', 'Videos')):
            ttk.Radiobutton(row, text=label, value=value, variable=self.kind, style='Segment.Toolbutton',
                            command=self.refresh_history).pack(side='left', padx=(0, 4))
        ttk.Button(row, text='Copy address', style='Compact.Hayat.TButton', command=self.copy_address).pack(side='right')
        ttk.Button(row, text='Open downloads folder', style='Compact.Hayat.TButton', command=self.open_folder).pack(side='right', padx=6)
        ttk.Button(row, text='Show in folder', style='HayatAccent.TButton', command=self.show_in_folder).pack(side='right')
        self.history_tree = self._tree(frame, (('#0', 'When', 130), ('kind', 'Kind', 80), ('what', 'What', 260), ('source', 'From', 170),
                                               ('path', 'Saved to', 300), ('size', 'Size', 80), ('status', 'Result', 120)))
        self.history_tree.bind('<Double-1>', lambda _event: self.show_in_folder())
        self.history_note = tk.Label(frame, text='', bg=p.PANEL, fg=p.MUTED, font=('Segoe UI', 8), anchor='w', justify='left', padx=8, pady=5)
        self.history_note.pack(fill='x', pady=(4, 0))
        self.history_tree.bind('<<TreeviewSelect>>', lambda _event: self.history_selected())
        return frame

    def _build_web(self) -> tk.Frame:
        p = self.p
        frame = tk.Frame(self.tabs, bg=p.BLACK)
        row = tk.Frame(frame, bg=p.BLACK)
        row.pack(fill='x', pady=(8, 4))
        tk.Label(row, text='Filter', bg=p.BLACK, fg=p.WHITE).pack(side='left')
        entry = ttk.Entry(row, textvariable=self.filter, width=30)
        entry.pack(side='left', padx=6)
        self.filter.trace_add('write', lambda *_: self.refresh_web())
        self.web_note = tk.Label(row, text='', bg=p.BLACK, fg=p.MUTED, font=('Segoe UI', 8))
        self.web_note.pack(side='left', padx=8)
        body = tk.PanedWindow(frame, orient='horizontal', bg=p.BLACK, sashwidth=6, bd=0)
        body.pack(fill='both', expand=True)
        left, right = tk.Frame(body, bg=p.BLACK), tk.Frame(body, bg=p.BLACK)
        self.web_tree = self._tree(left, (('#0', 'Time', 90), ('purpose', 'For', 120), ('host', 'Site', 190), ('path', 'Address', 260),
                                          ('size', 'Size', 70), ('ms', 'Took', 70)))
        tk.Label(right, text='By site, this session', bg=p.BLACK, fg=p.WHITE, font=('Segoe UI Semibold', 10), anchor='w').pack(fill='x')
        self.site_tree = self._tree(right, (('#0', 'Site', 170), ('requests', 'Requests', 64), ('data', 'Data', 70), ('avg', 'Avg', 60),
                                            ('failed', 'Failed', 48), ('for', 'Used for', 150)))
        body.add(left, minsize=480, stretch='always')
        body.add(right, minsize=420, width=580, stretch='never')
        return frame

    # ------------------------------------------------------------------ refreshing
    def _changed(self) -> None:
        if not self._closed:
            self.after(0, self.refresh_history)

    def _tick(self) -> None:
        if self._closed:
            return
        self.refresh_active()
        if self.tabs.index('current') == 2:
            self.refresh_web()
        summary = transfers().summary()
        self.totals.configure(text=f"{summary['active']} downloading now · {len(self.history_rows):,} in history · "
                                   f"{summary['web_requests']:,} web requests ({size(summary['web_bytes'])}) from {summary['sites']} sites this session")
        self._tick_id = self.after(700, self._tick)

    def refresh_active(self) -> None:
        tree = self.active_tree
        tree.delete(*tree.get_children())
        rows = transfers().active()
        for item in rows:
            total = item.get('total')
            if total:
                share = min(1.0, item['done'] / total)
                bar = '█' * int(share * 14) + '░' * (14 - int(share * 14))
                progress = f"{bar} {share * 100:.0f}% · {size(item['done'])} of {size(total)}"
            else:
                progress = f"{size(item['done'])} so far"
            tree.insert('', 'end', iid=str(item['id']), text=f"{KIND_LABEL.get(item['kind'], item['kind'])} · {item['what']}",
                        values=(progress, f"{size(item['speed'])}/s", item['host'] or '—', item['destination'] or '—'), tags=('active',))
        self.active_note.configure(text=f'{len(rows)} downloading now.' if rows else
                                   'Nothing is downloading right now. Files, AI models and videos appear here while they download.')

    def refresh_history(self) -> None:
        if self._closed:
            return
        kind = self.kind.get()
        rows = transfers().history(500, '' if kind == 'all' else kind)
        # Files downloaded before the log existed, from the download manager's own records.
        manager = getattr(getattr(self.gui, 'runtime', None), 'downloads', None)
        if manager is not None and kind in ('all', 'file'):
            known = {row.get('destination') for row in rows}
            try:
                for record in manager.list(200):
                    if record.get('path') in known:
                        continue
                    rows.append({'kind': 'file', 'what': record.get('purpose') or Path(record.get('path') or '').name,
                                 'host': record.get('source_host') or '', 'path': '', 'destination': record.get('path') or '',
                                 'done': record.get('bytes') or 0, 'status': {'complete': 'done'}.get(record.get('status'), record.get('status')),
                                 'finished': record.get('timestamp') or '', 'source_url': record.get('url') or ''})
            except Exception:  # the log's own history still shows
                log_swallowed(_log, "reading older download records")
        rows.sort(key=lambda row: row.get('finished') or row.get('started') or '', reverse=True)
        self.history_rows = rows
        tree = self.history_tree
        tree.delete(*tree.get_children())
        for index, row in enumerate(rows):
            status = row.get('status') or ''
            tree.insert('', 'end', iid=str(index), text=local_time(row.get('finished') or row.get('started') or ''),
                        values=(KIND_LABEL.get(row['kind'], row['kind']), row.get('what', ''), row.get('host') or '—', row.get('destination') or '—',
                                size(row.get('done')), status + (f" · {row['error'][:40]}" if row.get('error') else '')),
                        tags=(status,) if status in STATUS_COLOUR else ())
        if not rows:
            self.history_note.configure(text='No downloads yet. Files, AI models and videos HAYAT downloads are listed here, with where they '
                                             'came from and where they were saved.')

    def refresh_web(self) -> None:
        needle = self.filter.get().strip().lower()
        rows = [row for row in transfers().web_recent(500)
                if not needle or needle in (row['host'] + row['path'] + row['purpose']).lower()]
        tree = self.web_tree
        tree.delete(*tree.get_children())
        for index, row in enumerate(rows[:300]):
            tree.insert('', 'end', iid=str(index), text=local_time(row['at'])[-8:],
                        values=(row['purpose'], row['host'], row['path'], size(row['bytes']), f"{row['ms']} ms"),
                        tags=('failed',) if row['status'] != 'done' else ())
        sites = transfers().sites()
        tree = self.site_tree
        tree.delete(*tree.get_children())
        for index, site in enumerate(sites):
            if needle and needle not in (site['host'] + ' '.join(site['purposes'])).lower():
                continue
            tree.insert('', 'end', iid=str(index), text=site['host'], values=(site['requests'], size(site['bytes']), f"{site['average_ms']} ms",
                                                                          site['failed'] or '', ', '.join(site['purposes'])),
                        tags=('failed',) if site['failed'] and site['failed'] == site['requests'] else ())
        self.web_note.configure(text=f'{len(rows):,} recent requests shown · red = failed')

    # ------------------------------------------------------------------ actions
    def selected(self) -> dict[str, Any] | None:
        selection = self.history_tree.selection()
        return self.history_rows[int(selection[0])] if selection else None

    def history_selected(self) -> None:
        row = self.selected()
        if row:
            source = row.get('source_url') or (f"https://{row['host']}{row.get('path', '')}" if row.get('host') and row.get('path') else row.get('host', ''))
            self.history_note.configure(text=f"{KIND_LABEL.get(row['kind'], row['kind'])}: {row.get('what', '')}\nFrom: {source or '—'}\n"
                                             f"Saved to: {row.get('destination') or '—'}\n{size(row.get('done'))}"
                                             + (f" in {row['seconds']} s ({size(row.get('speed'))}/s)" if row.get('seconds') else '')
                                             + f" · {row.get('status', '')}" + (f" · {row['error']}" if row.get('error') else ''))

    def show_in_folder(self) -> None:
        row = self.selected()
        if not row or not row.get('destination'):
            return
        path = Path(row['destination'])
        if path.is_file():
            subprocess.Popen(['explorer', '/select,', str(path)])
        elif path.is_dir():
            os.startfile(str(path))  # noqa: S606 - opens the owner's own folder in Explorer
        else:
            self.history_note.configure(text=f'{path} is no longer on this computer (moved or cleaned up).')

    def open_folder(self) -> None:
        manager = getattr(getattr(self.gui, 'runtime', None), 'downloads', None)
        folder = Path(getattr(manager, 'root', 'data/downloads'))
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(str(folder))  # noqa: S606 - opens the owner's own folder in Explorer

    def copy_address(self) -> None:
        row = self.selected()
        if row:
            self.clipboard_clear()
            self.clipboard_append(row.get('source_url') or row.get('host') or '')

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            transfers().listeners.remove(self._changed)
        except ValueError:
            pass
        try:
            self.after_cancel(self._tick_id)
        except (tk.TclError, AttributeError):
            pass
        self.destroy()
