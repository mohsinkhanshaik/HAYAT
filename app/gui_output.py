"""Credential-safe display widgets, separate from editable controls."""
from __future__ import annotations

from tkinter import ttk

from app.gui_presentation import safe_text
from app.gui_resources import StringVar


class DisplayVar(StringVar):
    def __init__(self, master=None, value="", **kw):
        super().__init__(master, value=safe_text(value), **kw)

    def set(self, value):
        super().set(safe_text(value))

    def set_if_changed(self, value):
        value = safe_text(value)
        if self.get() != value:
            super().set(value)


class SafeTreeview(ttk.Treeview):
    """Mask only visible text/values; row identity and source data stay intact."""
    @staticmethod
    def _visible(options):
        options = dict(options)
        if "text" in options:
            options["text"] = safe_text(options["text"])
        if "values" in options:
            options["values"] = tuple(safe_text(item) for item in options["values"])
        return options

    def insert(self, parent, index, iid=None, **kw):
        return super().insert(parent, index, iid=iid, **self._visible(kw))

    def item(self, item, option=None, **kw):
        return super().item(item, option, **self._visible(kw))

    def set(self, item, column=None, value=None):
        return super().set(item, column, safe_text(value) if value is not None else None)
