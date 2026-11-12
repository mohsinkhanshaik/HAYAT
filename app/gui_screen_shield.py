"""Cover this app's windows for screen sharing; this is not an OS lock."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk


class ScreenShield:
    def __init__(self, gui):
        self.gui, self.root = gui, gui.root
        self.active = False
        self.covers = {}
        self.previous_focus = None
        self.previous_grab = None
        self._map_binding = self.root.bind_all('<Map>', self._mapped, add='+')
        self._focus_binding = self.root.bind_all('<FocusIn>', self._focused, add='+')
        self.root.bind('<Destroy>', self._destroyed, add='+')

    def toggle(self):
        self.hide() if self.active else self.show()
        return 'break'

    def _windows(self, widget):
        if isinstance(widget, (tk.Tk, tk.Toplevel)):
            yield widget
        for child in widget.winfo_children():
            yield from self._windows(child)

    def show(self):
        if self.active:
            return
        self.previous_focus = self.root.focus_get()
        self.previous_grab = self.root.grab_current()
        self.active = True
        for top in list(self._windows(self.root)):
            self._cover(top)
        self._focus_cover()

    def _cover(self, top):
        if top in self.covers:
            self.covers[top][0].lift()
            return
        p = self.gui
        # Glass maps the theme's BLACK to a transparent color key on Map.
        # The privacy cover must never opt into that map.
        cover = tk.Frame(top, bg='#0D1420', takefocus=True, highlightthickness=2,
                         highlightbackground=p.HOLO)
        self.covers[top] = (cover, top.title())
        top.title('HAYAT · Screen shield')
        card = tk.Frame(cover, bg=p.PANEL, padx=20, pady=20,
                        highlightthickness=1, highlightbackground=p.LINE)
        card.place(relx=.5, rely=.5, anchor='center', relwidth=.86)
        tk.Label(card, text='SCREEN SHIELD', bg=p.PANEL, fg=p.HOLO,
                 font=('Segoe UI Semibold', 16)).pack(pady=(0, 12))
        note = tk.Label(card, text='This HAYAT window is covered.\nWork continues in the background.\n'
                        'Other apps, files and the clipboard are unaffected.', bg=p.PANEL,
                        fg=p.MUTED, justify='center', wraplength=420)
        note.pack(fill='x', pady=(0, 16))
        card.bind('<Configure>', lambda event: note.configure(wraplength=max(80, event.width - 40)))
        ttk.Button(card, text='Reveal HAYAT', style='HayatAccent.TButton', command=self.hide).pack()
        if hasattr(self.gui, '_emergency_stop'):
            ttk.Button(card, text='Emergency stop', command=self.gui._emergency_stop).pack(pady=(10, 0))
        cover.bind('<Control-Shift-H>', lambda _event: self.toggle())
        cover.bind('<KeyPress>', lambda _event: 'break')
        cover.bindtags((str(cover),))
        cover.place(x=0, y=0, relwidth=1, relheight=1)
        cover.lift()

    def _mapped(self, event):
        if self.active and isinstance(event.widget, tk.Toplevel):
            self._cover(event.widget)
            self.root.after_idle(self._focus_cover)

    def _focused(self, event):
        if self.active:
            widget = event.widget
            while widget is not None:
                if any(widget is pair[0] for pair in self.covers.values()):
                    return
                widget = widget.master
            self._focus_cover()

    def _focus_cover(self):
        if not self.active:
            return
        visible = [pair[0] for pair in self.covers.values() if pair[0].winfo_exists() and pair[0].winfo_viewable()]
        if visible:
            cover = visible[0]
            current = cover.grab_current()
            if current is not None and current is not cover:
                self.previous_grab = current
            cover.grab_set()
            cover.focus_set()

    def hide(self):
        if not self.active:
            return
        self.active = False
        for top, (cover, title) in list(self.covers.items()):
            if top.winfo_exists():
                if cover.grab_current() is cover:
                    cover.grab_release()
                top.title(title)
                cover.destroy()
        self.covers.clear()
        if self.previous_grab is not None and self.previous_grab.winfo_exists():
            self.previous_grab.grab_set()
        if self.previous_focus is not None and self.previous_focus.winfo_exists():
            self.previous_focus.focus_set()

    def _destroyed(self, event):
        if event.widget is self.root:
            self.active = False
            self.covers.clear()
            self.root._unbind(('bind', 'all', '<Map>'), self._map_binding)
            self.root._unbind(('bind', 'all', '<FocusIn>'), self._focus_binding)
