"""MainWindow zen mode (Ctrl+B panels, Ctrl+Shift+B total).

Runs against the **real** window (`AppWindowTest`). It used to borrow the zen
methods onto a hand-built stand-in with fake widgets — the standard workaround
from when nobody had managed to construct `MainWindow` headless. That worked
because these methods touch little state, but it tested the methods rather than
the window: a zen level that forgets to restore a real widget, or an attribute
renamed during a split, would still have passed.

Same assertions as before, real widgets underneath.
"""
import unittest

from test_app_window_construction import AppWindowTest


class ZenTest(AppWindowTest):
    """Zen acts on six things; each test starts from a known state."""

    def _open(self, *, sidebar_open=True, search_open=False):
        self.win._zen_level = None
        self.win._zen_saved = None
        for widget in (self.win._header, self.win._tab_bar, self.win._vault_tree,
                       self.win._status_bar):
            widget.set_visible(True)
        self.win._sidebar_toggle.set_active(sidebar_open)
        self.win._search_toggle.set_active(search_open)

    def _state(self):
        w = self.win
        return {
            "header": w._header.get_visible(),
            "tab_bar": w._tab_bar.get_visible(),
            "tree": w._vault_tree.get_visible(),
            "sidebar": w._sidebar_toggle.get_active(),
            "search": w._search_toggle.get_active(),
            "statusbar": w._status_bar.get_visible(),
        }


class TestPanelsZen(ZenTest):
    def test_hides_panels_keeps_header_and_tabs(self):
        self._open(search_open=True)
        self.win._toggle_zen("panels")
        self.assertEqual(self.win._zen_level, "panels")
        self.assertTrue(self.win._header.get_visible())      # header stays
        self.assertTrue(self.win._tab_bar.get_visible())     # tabs stay
        self.assertFalse(self.win._vault_tree.get_visible())
        self.assertFalse(self.win._sidebar_toggle.get_active())
        self.assertFalse(self.win._search_toggle.get_active())

    def test_toggle_off_restores(self):
        self._open(sidebar_open=True, search_open=True)
        before = self._state()
        self.win._toggle_zen("panels")
        self.win._toggle_zen("panels")
        self.assertIsNone(self.win._zen_level)
        self.assertEqual(self._state(), before)


class TestTotalZen(ZenTest):
    def test_hides_everything(self):
        self._open()
        self.win._toggle_zen("total")
        self.assertEqual(self.win._zen_level, "total")
        for widget in (self.win._header, self.win._tab_bar, self.win._vault_tree):
            self.assertFalse(widget.get_visible())
        self.assertFalse(self.win._sidebar_toggle.get_active())

    def test_toggle_off_restores(self):
        self._open(sidebar_open=False, search_open=True)
        before = self._state()
        self.win._toggle_zen("total")
        self.win._toggle_zen("total")
        self.assertEqual(self._state(), before)


class TestCycle(ZenTest):
    def test_cycle_normal_panels_total_normal(self):
        self._open(sidebar_open=True, search_open=True)
        before = self._state()

        self.win._cycle_zen()  # → panels
        self.assertEqual(self.win._zen_level, "panels")
        self.assertTrue(self.win._header.get_visible())       # header still up
        self.assertFalse(self.win._vault_tree.get_visible())

        self.win._cycle_zen()  # → total
        self.assertEqual(self.win._zen_level, "total")
        self.assertFalse(self.win._header.get_visible())      # header now hidden

        self.win._cycle_zen()  # → normal
        self.assertIsNone(self.win._zen_level)
        self.assertEqual(self._state(), before)

    def test_shift_shortcut_still_toggles_total(self):
        self._open()
        self.win._toggle_zen("total")
        self.assertEqual(self.win._zen_level, "total")
        self.win._toggle_zen("total")
        self.assertIsNone(self.win._zen_level)


class TestLevelSwitching(ZenTest):
    def test_panels_then_total_hides_header(self):
        self._open()
        self.win._toggle_zen("panels")            # header still visible
        self.assertTrue(self.win._header.get_visible())
        self.win._toggle_zen("total")             # switch → header hidden
        self.assertEqual(self.win._zen_level, "total")
        self.assertFalse(self.win._header.get_visible())

    def test_total_then_panels_restores_header(self):
        self._open()
        self.win._toggle_zen("total")             # header hidden
        self.win._toggle_zen("panels")            # switch → header back
        self.assertEqual(self.win._zen_level, "panels")
        self.assertTrue(self.win._header.get_visible())

    def test_switch_then_exit_restores_original(self):
        self._open(sidebar_open=True, search_open=True)
        before = self._state()
        self.win._toggle_zen("panels")
        self.win._toggle_zen("total")             # switch levels
        self.win._toggle_zen("total")             # exit
        self.assertIsNone(self.win._zen_level)
        self.assertEqual(self._state(), before)


if __name__ == "__main__":
    unittest.main()
