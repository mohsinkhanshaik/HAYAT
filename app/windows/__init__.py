"""Windows integration package with lazy automation imports.

Importing a submodule executes this package first. Eagerly importing the
pywinauto-backed application controller here initialized COM before
``app.windows.com_apartment`` could declare STA, which broke TkDND/OLE and
caused pywinauto's threading-mode warning. Preserve the public import without
performing any COM-aware import until the controller is actually requested.
"""
from __future__ import annotations

__all__ = ["WindowsApplicationController"]


def __getattr__(name: str):
    if name == "WindowsApplicationController":
        from app.windows.applications import WindowsApplicationController

        return WindowsApplicationController
    raise AttributeError(name)
