"""Responsive navigation for the main desktop window."""
from __future__ import annotations

import tkinter as tk


class MainNavigationLayout:
    """Dock on roomy windows, overlay on small ones; keep choices per mode."""

    BREAKPOINT = 1100

    def __init__(self, gui):
        self.gui = gui
        self.compact = self.is_compact()
        self.choices = {False: True, True: False}
        self.return_focus = None
        self.shade = tk.Frame(gui._body, bg=gui.BLACK, cursor='arrow')
        self.shade.bind('<Button-1>', lambda _event: self.dismiss())
        gui.root.bind('<Escape>', self.escape, add='+')
        gui.root.bind('<Tab>', self.tab, add='+')
        gui.root.bind('<Shift-Tab>', lambda event: self.tab(event, backwards=True), add='+')
        gui.workspace_navigation.entry.bind('<Escape>', self.search_escape)
        self.dismiss_hint = tk.Label(self.shade, text='Choose a workspace\nClick here or press Esc to return',
                                     bg=gui.BLACK, fg=gui.MUTED, font=('Segoe UI', 10),
                                     justify='center', cursor='hand2')
        self.dismiss_hint.bind('<Button-1>', lambda _event: self.dismiss())

    def is_compact(self):
        scale = min(1.3, max(1.0, float(getattr(self.gui.root, '_hayat_text_scale', 1.0))))
        return self.gui.root.winfo_width() / scale < self.BREAKPOINT

    @property
    def visible(self):
        return self.choices[self.compact] and not self.gui._focus_mode

    @property
    def drawer_open(self):
        return self.compact and self.visible

    def update_mode(self):
        compact = self.is_compact()
        changed = compact != self.compact
        self.compact = compact
        return changed

    def mount(self):
        gui = self.gui
        gui._sidebar.place_forget()
        self.shade.place_forget()
        if self.visible and not self.compact:
            gui._sidebar.pack(side='left', fill='y')
        gui.navigation_button.configure(
            text='Close menu' if self.drawer_open else 'Menu',
            style='HayatChosen.TButton' if self.visible else 'Hayat.TButton')

    def overlay(self):
        if self.drawer_open:
            gui = self.gui
            width = min(max(260, gui._sidebar.winfo_reqwidth()), max(260, gui._body.winfo_width() - 100))
            self.shade.place(x=0, y=0, relwidth=1, relheight=1)
            self.shade.lift()
            self.dismiss_hint.place(relx=.5, rely=.5, x=width // 2, anchor='center')
            gui._sidebar.place(x=0, y=0, width=width, relheight=1)
            gui._sidebar.lift()

    def toggle(self):
        gui = self.gui
        opening = not self.visible
        if opening:
            self.return_focus = gui.root.focus_get()
            if gui._focus_mode:
                gui._focus_mode = False
                gui.focus_mode_button.configure(text='Focus Mode')
        self.choices[self.compact] = opening
        gui._arrange_rails()
        if opening:
            gui.workspace_navigation.entry.focus_set()
        else:
            self.restore_focus()

    def reveal(self):
        gui = self.gui
        if not self.visible:
            self.return_focus = gui.root.focus_get()
        self.choices[self.compact] = True
        if gui._focus_mode:
            gui._focus_mode = False
            gui.focus_mode_button.configure(text='Focus Mode')
        gui._arrange_rails()

    def dismiss(self, *, focus=True):
        if not self.drawer_open:
            return
        self.choices[True] = False
        self.gui._arrange_rails()
        if focus:
            self.restore_focus()

    def restore_focus(self):
        target, self.return_focus = self.return_focus, None
        try:
            if target is not None and target.winfo_exists() and target.winfo_viewable():
                target.focus_set()
            else:
                self.gui.navigation_button.focus_set()
        except tk.TclError:
            self.gui.navigation_button.focus_set()

    def escape(self, event):
        if event.widget.winfo_toplevel() is self.gui.root and self.drawer_open:
            self.dismiss()
            return 'break'

    def search_escape(self, event):
        navigation = self.gui.workspace_navigation
        if navigation.query.get() or not self.drawer_open:
            return navigation.clear()
        return self.escape(event)

    def tab(self, event, backwards=False):
        if event.widget.winfo_toplevel() is not self.gui.root or not self.drawer_open:
            return None
        candidate = event.widget
        # Keep traversal on the drawer and exposed header controls (including
        # Emergency stop), rather than invisible controls underneath the shade.
        for _ in range(512):
            candidate = candidate.tk_focusPrev() if backwards else candidate.tk_focusNext()
            if candidate is None:
                break
            ancestor = candidate
            while ancestor not in (None, self.gui._sidebar, self.gui._body):
                ancestor = getattr(ancestor, 'master', None)
            if ancestor is not self.gui._body:
                navigation = self.gui.workspace_navigation
                if candidate in navigation.items.values():
                    navigation.focus_result(candidate.key)
                else:
                    candidate.focus_set()
                break
        return 'break'
