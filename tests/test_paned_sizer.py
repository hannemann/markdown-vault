"""Tests for markdown_vault.editor.paned_sizer.PanedSizer (side-panel sizing policy)."""

import unittest
import unittest.mock

from markdown_vault.editor.paned_sizer import PanedSizer


class _FakePaned:
    """Minimal Gtk.Paned stand-in recording set_position calls."""

    def __init__(self, width, position):
        self._width = width
        self._position = position

    def connect(self, *_a, **_k):
        return 0

    def get_width(self):
        return self._width

    def get_position(self):
        return self._position

    def set_position(self, pos):
        self._position = pos


def _make(side, width, position):
    paned = _FakePaned(width, position)
    with unittest.mock.patch("markdown_vault.editor.paned_sizer.GLib.idle_add"):
        sizer = PanedSizer(paned, side)
    return sizer, paned


class TestConstruction(unittest.TestCase):
    def test_rejects_bad_side(self):
        with self.assertRaises(ValueError):
            PanedSizer(_FakePaned(800, 400), "middle")


class TestSideWidthGeometry(unittest.TestCase):
    def test_end_side_width(self):
        sizer, _ = _make("end", 800, 500)
        self.assertEqual(sizer._side_width(800, 500), 300)      # 800 - 500
        self.assertEqual(sizer._position_for_side(800, 300), 500)

    def test_start_side_width(self):
        sizer, _ = _make("start", 800, 200)
        self.assertEqual(sizer._side_width(800, 200), 200)
        self.assertEqual(sizer._position_for_side(800, 200), 200)


class TestGrowthCap(unittest.TestCase):
    def test_first_width_change_seeds_want(self):
        sizer, paned = _make("end", 1900, 1600)  # sidebar = 300
        with unittest.mock.patch("markdown_vault.editor.paned_sizer.GLib.idle_add"):
            sizer._on_width_changed(paned, None)
        self.assertEqual(sizer._want, 300)

    def test_widen_caps_side_to_want(self):
        sizer, paned = _make("end", 1900, 1600)  # sidebar = 300 (want)
        with unittest.mock.patch("markdown_vault.editor.paned_sizer.GLib.idle_add"):
            sizer._on_width_changed(paned, None)   # seeds want=300
            paned._width = 3400                    # window widened; end absorbs
            sizer._on_width_changed(paned, None)
        # side capped to 300 → position = 3400 - 300
        self.assertEqual(paned.get_position(), 3100)

    def test_narrow_does_not_touch_position(self):
        sizer, paned = _make("end", 3400, 3100)  # sidebar = 300 (want)
        with unittest.mock.patch("markdown_vault.editor.paned_sizer.GLib.idle_add"):
            sizer._on_width_changed(paned, None)   # want=300
            paned._width = 1900
            paned._position = 1649                 # sidebar shrank to 251 (< want)
            before = paned.get_position()
            sizer._on_width_changed(paned, None)
        self.assertEqual(paned.get_position(), before)  # no cap, side shrank freely

    def test_start_side_widen_caps(self):
        sizer, paned = _make("start", 1900, 250)   # tree = 250 (want)
        with unittest.mock.patch("markdown_vault.editor.paned_sizer.GLib.idle_add"):
            sizer._on_width_changed(paned, None)   # want=250
            paned._width = 3400
            paned._position = 900                  # tree grew (start absorbs)
            sizer._on_width_changed(paned, None)
        self.assertEqual(paned.get_position(), 250)  # tree capped back to want


class TestDragTracking(unittest.TestCase):
    def test_drag_updates_want(self):
        sizer, paned = _make("end", 3400, 3100)  # sidebar = 300
        sizer._last_width = 3400
        sizer._resizing = False
        paned._position = 1900                    # user dragged: sidebar → 1500
        sizer._on_position_changed(paned, None)
        self.assertEqual(sizer._want, 1500)

    def test_resize_driven_position_ignored(self):
        sizer, paned = _make("end", 1900, 1649)
        sizer._want = 1500
        sizer._resizing = True                    # inside a resize
        sizer._on_position_changed(paned, None)
        self.assertEqual(sizer._want, 1500)       # unchanged

    def test_own_set_position_ignored(self):
        sizer, paned = _make("end", 3400, 1900)
        sizer._want = 1500
        sizer._busy = True                        # our own set_position
        sizer._on_position_changed(paned, None)
        self.assertEqual(sizer._want, 1500)


if __name__ == "__main__":
    unittest.main()
