"""Tests for MainWindow zen mode (Ctrl+B panels, Ctrl+Shift+B total)."""

import unittest

import markdown_vault.app_window as aw


class _FakeToggle:
    def __init__(self, active):
        self._active = active

    def get_active(self):
        return self._active

    def set_active(self, value):
        self._active = value


class _FakeWidget:
    def __init__(self, visible=True):
        self._visible = visible

    def get_visible(self):
        return self._visible

    def set_visible(self, value):
        self._visible = value


def _make_window(sidebar_open=True, search_open=False):
    class _W:
        _toggle_zen = aw.MainWindow._toggle_zen
        _cycle_zen = aw.MainWindow._cycle_zen
        _set_zen_level = aw.MainWindow._set_zen_level
        _apply_zen_level = aw.MainWindow._apply_zen_level
        _zen_get = aw.MainWindow._zen_get
        _zen_set = aw.MainWindow._zen_set
        _ZEN_ELEMENTS = aw.MainWindow._ZEN_ELEMENTS
        _ZEN_LEVELS = aw.MainWindow._ZEN_LEVELS

    w = _W()
    w._zen_level = None
    w._zen_saved = None
    w._header = _FakeWidget(True)
    w._tab_bar = _FakeWidget(True)
    w._vault_tree = _FakeWidget(True)
    w._status_bar = _FakeWidget(True)
    w._sidebar_toggle = _FakeToggle(sidebar_open)
    w._search_toggle = _FakeToggle(search_open)
    return w


def _state(w):
    return {
        "header": w._header.get_visible(),
        "tab_bar": w._tab_bar.get_visible(),
        "tree": w._vault_tree.get_visible(),
        "sidebar": w._sidebar_toggle.get_active(),
        "search": w._search_toggle.get_active(),
        "statusbar": w._status_bar.get_visible(),
    }


class TestPanelsZen(unittest.TestCase):
    def test_hides_panels_keeps_header_and_tabs(self):
        w = _make_window(search_open=True)
        w._toggle_zen("panels")
        self.assertEqual(w._zen_level, "panels")
        self.assertTrue(w._header.get_visible())      # header stays
        self.assertTrue(w._tab_bar.get_visible())     # tabs stay
        self.assertFalse(w._vault_tree.get_visible())
        self.assertFalse(w._sidebar_toggle.get_active())
        self.assertFalse(w._search_toggle.get_active())

    def test_toggle_off_restores(self):
        w = _make_window(sidebar_open=True, search_open=True)
        before = _state(w)
        w._toggle_zen("panels")
        w._toggle_zen("panels")
        self.assertIsNone(w._zen_level)
        self.assertEqual(_state(w), before)


class TestTotalZen(unittest.TestCase):
    def test_hides_everything(self):
        w = _make_window()
        w._toggle_zen("total")
        self.assertEqual(w._zen_level, "total")
        for getter in (w._header.get_visible, w._tab_bar.get_visible,
                       w._vault_tree.get_visible):
            self.assertFalse(getter())
        self.assertFalse(w._sidebar_toggle.get_active())

    def test_toggle_off_restores(self):
        w = _make_window(sidebar_open=False, search_open=True)
        before = _state(w)
        w._toggle_zen("total")
        w._toggle_zen("total")
        self.assertEqual(_state(w), before)


class TestCycle(unittest.TestCase):
    def test_cycle_normal_panels_total_normal(self):
        w = _make_window(sidebar_open=True, search_open=True)
        before = _state(w)

        w._cycle_zen()  # → panels
        self.assertEqual(w._zen_level, "panels")
        self.assertTrue(w._header.get_visible())       # header still up
        self.assertFalse(w._vault_tree.get_visible())

        w._cycle_zen()  # → total
        self.assertEqual(w._zen_level, "total")
        self.assertFalse(w._header.get_visible())       # header now hidden

        w._cycle_zen()  # → normal
        self.assertIsNone(w._zen_level)
        self.assertEqual(_state(w), before)

    def test_shift_shortcut_still_toggles_total(self):
        w = _make_window()
        w._toggle_zen("total")
        self.assertEqual(w._zen_level, "total")
        w._toggle_zen("total")
        self.assertIsNone(w._zen_level)


class TestLevelSwitching(unittest.TestCase):
    def test_panels_then_total_hides_header(self):
        w = _make_window()
        w._toggle_zen("panels")            # header still visible
        self.assertTrue(w._header.get_visible())
        w._toggle_zen("total")             # switch → header hidden
        self.assertEqual(w._zen_level, "total")
        self.assertFalse(w._header.get_visible())

    def test_total_then_panels_restores_header(self):
        w = _make_window()
        w._toggle_zen("total")             # header hidden
        w._toggle_zen("panels")            # switch → header back
        self.assertEqual(w._zen_level, "panels")
        self.assertTrue(w._header.get_visible())

    def test_switch_then_exit_restores_original(self):
        w = _make_window(sidebar_open=True, search_open=True)
        before = _state(w)
        w._toggle_zen("panels")
        w._toggle_zen("total")             # switch levels
        w._toggle_zen("total")             # exit
        self.assertIsNone(w._zen_level)
        self.assertEqual(_state(w), before)


if __name__ == "__main__":
    unittest.main()
