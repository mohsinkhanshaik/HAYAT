"""Windows COM apartment policy for HAYAT's desktop process.

Tk/tkdnd uses OLE, which requires a single-threaded apartment on the GUI
thread.  pywinauto otherwise asks comtypes for MTA and then has to recover,
emitting ``Revert to STA COM threading mode``.  Declare STA before either
library initializes COM so there is nothing to recover from.
"""
from __future__ import annotations

import os
import sys
import warnings


COINIT_APARTMENTTHREADED = 2


def configure_gui_sta() -> bool:
    """Declare the GUI thread as STA before importing COM-aware libraries."""
    if os.name != "nt":
        return False
    sys.coinit_flags = COINIT_APARTMENTTHREADED
    # pywinauto reports an explicitly supplied mode as a UserWarning.  This
    # exact informational message is expected; unrelated warnings stay live.
    warnings.filterwarnings(
        "ignore",
        message=r"Apply externally defined coinit_flags: 2",
        category=UserWarning,
        module=r"pywinauto(?:\..*)?",
    )
    return True

