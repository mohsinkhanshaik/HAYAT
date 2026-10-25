"""Tk variable cleanup belongs to widget destruction on the interface thread."""
from __future__ import annotations

import tkinter as tk
import weakref
import gc
import threading


_COLLECTION_GUARDS = set()
_collection_was_enabled = False


class TkCollectionGuard:
    """Collect widget cycles on their owner thread while any Tk root is active.

    Background allocation can otherwise finalize an old Tk interpreter and
    abort Windows inside Tcl. Reference counting remains active; cyclic
    collection is scheduled on the UI instead of disabled permanently.
    """
    def __init__(self, root):
        global _collection_was_enabled
        self.root = weakref.ref(root)
        self.owner = threading.get_ident()
        self.closed, self.ticks = False, 0
        if not _COLLECTION_GUARDS:
            _collection_was_enabled = gc.isenabled()
            gc.disable()
        _COLLECTION_GUARDS.add(self)
        root.bind('<Destroy>', self._destroy, add='+')
        self.timer = root.after(1000, self.collect)

    def collect(self):
        if self.closed:
            return
        if threading.get_ident() != self.owner:
            raise RuntimeError("Tk cyclic collection must run on the interpreter's owner thread.")
        self.ticks += 1
        gc.collect(2 if self.ticks % 30 == 0 else 0)
        root = self.root()
        if root is not None:
            self.timer = root.after(1000, self.collect)

    def _destroy(self, event):
        if event.widget is self.root():
            self.close()

    def close(self):
        if self.closed:
            return
        if threading.get_ident() != self.owner:
            raise RuntimeError("Tk collection cleanup must run on the owner thread.")
        self.closed = True
        root = self.root()
        if root is not None:
            root.after_cancel(self.timer)
        gc.collect()
        _COLLECTION_GUARDS.discard(self)
        if not _COLLECTION_GUARDS and _collection_was_enabled:
            gc.enable()


def manage_tk_collection(root):
    if not hasattr(root, '_hayat_collection_guard'):
        root._hayat_collection_guard = TkCollectionGuard(root)
    return root._hayat_collection_guard


class _OwnedVariable:
    def __init__(self, master=None, *args, **kwargs):
        if master is None:
            master = tk._get_default_root('create variable')
        super().__init__(master, *args, **kwargs)
        variables = getattr(master, '_hayat_variables', None)
        if variables is None:
            variables = master._hayat_variables = []
            owner_ref = weakref.ref(master)

            def release(event):
                owner = owner_ref()
                if owner is None or event.widget is not owner:
                    return
                for variable in owner._hayat_variables:
                    variable.release()
                owner._hayat_variables.clear()

            master.bind('<Destroy>', release, add='+')
        variables.append(self)

    def release(self):
        """Remove traces and the Tcl value before the interpreter is destroyed."""
        try:
            tk.Variable.__del__(self)
        except tk.TclError:
            pass
        finally:
            self._tk = None
            self._root = None

    def set_if_changed(self, value):
        """Polling a stable reading must not retrigger layout and write traces."""
        if self.get() != value:
            self.set(value)

    def __del__(self):
        # Worker-thread GC must not synchronously call Tcl. The owner retains
        # this variable until its Destroy event performs explicit cleanup.
        pass


class StringVar(_OwnedVariable, tk.StringVar):
    pass


class BooleanVar(_OwnedVariable, tk.BooleanVar):
    pass


class DoubleVar(_OwnedVariable, tk.DoubleVar):
    pass
