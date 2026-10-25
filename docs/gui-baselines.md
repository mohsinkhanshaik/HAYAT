# GUI baselines: display scaling, keyboard and long content

Updated October 14, 2026.

HAYAT runs DPI-unaware, so Windows display scaling shrinks the screen it sees.
A 1920x1080 panel is 1920x1080 at 100 %, 1536x864 at 125 %, 1280x720 at
150 % and 960x540 at 200 %. [tests/test_gui_baselines.py](../tests/test_gui_baselines.py)
lays the real main window out at the size HAYAT opens on each of those screens
(plus 125 % with the **Large** text setting) and checks, every full test run:

- the window fits the screen, clear of the taskbar;
- the message box, **Send**, the conversation, **Attach**, **Mic** and the
  visible header actions are shown and fully inside the window;
- **Send** and every one of those controls can be reached with **Tab** from the
  message box;
- after a 160-line report with a very long unbroken link, a failure line and a
  12-file attachment card, all of that is still true, the conversation keeps at
  least 110 px and never scrolls sideways.

## What the check found and changed

| Finding | Change |
| --- | --- |
| The window could not be smaller than 1180x700. At 200 % (960x540) it ran off the right edge and under the taskbar; at 150 % its bottom sat under the taskbar. | The minimum follows the screen: 1180x700, or the screen less 20 px across and 60 px down when that is smaller (`app.gui.minimum_size`). A first start on a 1280x720 screen now opens at 1180x660. |
| At 940x480 the fixed rows left the conversation 51 px tall. | Under 600 px tall the header's date line is hidden (the clock is in the status line when there is room), the live-state line keeps one row, **Beast mode** uses the compact button, an empty message box takes one line, and the status bar folds away until the window is taller. The conversation keeps about 120 px. |
| The attachment card's "Queued" status was a black box on the card, and long file names were cut mid-word. | Status rows use the card's colour; names are shortened with an ellipsis and keep their extension. |

## Reviewed screenshots

Captured on the owner's PC at each size with
`$env:HAYAT_SHOTS = "1"; .\.venv\Scripts\python.exe -m pytest -s tests/test_gui_baselines.py`
(the files land in `.test-runs/gui-baselines/`) and reviewed by eye on
October 14, 2026. The dark strip across the top of each picture is Windows
Voice Access docked on that screen, not HAYAT.

| Scaling | Screenshot | Review |
| --- | --- | --- |
| 100 % | [100.png](gui-baselines/2026-10-14/100.png) | Full layout with both rails, overview and resources; long lines wrap. |
| 125 % | [125.png](gui-baselines/2026-10-14/125.png) | Compact layout; every primary control visible. |
| 125 %, Large text | [125-large-text.png](gui-baselines/2026-10-14/125-large-text.png) | Larger text still fits; status bar and composer intact. |
| 150 % | [150.png](gui-baselines/2026-10-14/150.png) | Conversation about 230 px; status bar shown. |
| 200 % | [200.png](gui-baselines/2026-10-14/200.png) | Very-short layout; conversation about 120 px, all controls reachable. |

## Limits

The checks measure geometry and focus order inside Tk; they do not prove a
screen reader announces each control well, and colour contrast is reviewed by
eye in the screenshots, not measured. Other windows (Studios, Global
Intelligence, Trade Center) have their own small-window tests but are not yet
part of this scaling matrix.
