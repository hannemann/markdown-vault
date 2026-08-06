"""Tests for MainWindow._toggle_zen (Ctrl+B distraction-free mode)."""

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

    w = _W()
    w._zen_active = False
    w._zen_saved = None
    w._header = _FakeWidget(True)
    w._tab_bar = _FakeWidget(True)
    w._vault_tree = _FakeWidget(True)
    w._sidebar_toggle = _FakeToggle(sidebar_open)
    w._search_toggle = _FakeToggle(search_open)
    return w


class TestToggleZen(unittest.TestCase):
    def test_entering_hides_all_chrome(self):
        w = _make_window(search_open=True)
        w._toggle_zen()
        self.assertTrue(w._zen_active)
        self.assertFalse(w._header.get_visible())
        self.assertFalse(w._tab_bar.get_visible())
        self.assertFalse(w._vault_tree.get_visible())
        self.assertFalse(w._sidebar_toggle.get_active())
        self.assertFalse(w._search_toggle.get_active())  # search bar hidden too

    def test_exit_restores_open_search(self):
        w = _make_window(search_open=True)
        w._toggle_zen()   # enter (search hidden)
        w._toggle_zen()   # exit
        self.assertTrue(w._search_toggle.get_active())  # search reopened

    def test_exiting_restores_previous_state(self):
        w = _make_window(sidebar_open=True)
        w._toggle_zen()   # enter
        w._toggle_zen()   # exit
        self.assertFalse(w._zen_active)
        self.assertTrue(w._header.get_visible())
        self.assertTrue(w._tab_bar.get_visible())
        self.assertTrue(w._vault_tree.get_visible())
        self.assertTrue(w._sidebar_toggle.get_active())

    def test_exit_keeps_sidebar_closed_if_it_was_closed(self):
        w = _make_window(sidebar_open=False)  # sidebar was closed before zen
        w._toggle_zen()   # enter
        w._toggle_zen()   # exit
        self.assertFalse(w._sidebar_toggle.get_active())  # stays closed

    def test_round_trip_is_idempotent(self):
        w = _make_window(sidebar_open=True)
        before = (
            w._header.get_visible(), w._tab_bar.get_visible(),
            w._vault_tree.get_visible(), w._sidebar_toggle.get_active(),
        )
        w._toggle_zen()
        w._toggle_zen()
        after = (
            w._header.get_visible(), w._tab_bar.get_visible(),
            w._vault_tree.get_visible(), w._sidebar_toggle.get_active(),
        )
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
