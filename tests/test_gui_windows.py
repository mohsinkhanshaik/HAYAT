"""Pop-ups open over HAYAT, inside the usable screen, with a way to close them."""
from __future__ import annotations

import re

from app.gui_windows import MARGIN, placement


def _box(geometry):
    w, h, x, y = (int(value) for value in re.fullmatch(r"(\d+)x(\d+)\+(-?\d+)\+(-?\d+)", geometry).groups())
    return x, y, w, h


def test_a_popup_is_centred_over_the_window_that_opened_it():
    x, y, w, h = _box(placement((560, 320), (115, 52, 1690, 1056), (0, 50, 1920, 1140)))
    assert (w, h) == (560, 320)
    assert abs((x + w / 2) - (115 + 1690 / 2)) <= 1


def test_its_title_bar_is_never_under_a_bar_docked_at_the_top():
    # Voice Access docked across the top: the usable area starts at y=50.
    # A main window near the top used to put the pop-up's title bar -- its
    # only close button -- underneath that bar.
    x, y, w, h = _box(placement((560, 320), (0, 0, 800, 200), (0, 50, 1920, 1140)))
    assert y >= 50 + MARGIN
    assert x >= MARGIN


def test_it_stays_wholly_inside_the_usable_area_and_is_shrunk_to_fit():
    area = (0, 50, 1366, 728)
    x, y, w, h = _box(placement((760, 860), (1200, 600, 800, 600), area))
    assert x >= area[0] and x + w <= area[2]
    assert y >= area[1] and y + h <= area[3]
    assert h < 860                                  # a tall viewer fits a short screen
