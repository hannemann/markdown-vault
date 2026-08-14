"""Tests for the dev-only D-Bus debug/automation interface (debug_control)."""

import os
import tempfile
import unittest
from types import SimpleNamespace

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import GLib

from markdown_vault import debug_control
from markdown_vault.app_window import MainWindow


class _FakeWin:
    def __init__(self):
        self.calls = []

    def debug_open_file(self, p): self.calls.append(("open", p)); return True
    def debug_close_tab(self, p): self.calls.append(("close", p)); return False
    def debug_search(self, q): self.calls.append(("search", q)); return True
    def debug_quick_open(self, q): self.calls.append(("qopen", q)); return True
    def debug_submit(self): self.calls.append(("submit",)); return True
    def debug_ask_answer(self): return "## Saturn\nRings."
    def debug_select_in_tree(self, p): self.calls.append(("select", p)); return True
    def debug_active_file(self): return "/v/a.md"
    def debug_list_tabs(self): return ["/v/a.md", "/v/b.md"]
    def debug_state(self): return '{"x": 1}'
    def debug_search_results(self): return ["/v/hit.md"]
    def debug_wait_idle(self, t): self.calls.append(("wait", t)); return True


class TestDispatch(unittest.TestCase):
    def setUp(self):
        self.win = _FakeWin()

    def _dispatch(self, method, arg=None):
        params = GLib.Variant("(s)", (arg,)) if arg is not None else None
        return debug_control.DebugControl._dispatch(self.win, method, params)

    def test_open_file_forwards_and_wraps_bool(self):
        out = self._dispatch("OpenFile", "/v/a.md")
        self.assertEqual(out.unpack(), (True,))
        self.assertIn(("open", "/v/a.md"), self.win.calls)

    def test_close_tab_returns_backend_bool(self):
        self.assertEqual(self._dispatch("CloseTab", "/v/a.md").unpack(), (False,))

    def test_search_and_quick_open(self):
        self.assertEqual(self._dispatch("Search", "jupiter").unpack(), (True,))
        self.assertEqual(self._dispatch("QuickOpen", "erde").unpack(), (True,))
        self.assertIn(("search", "jupiter"), self.win.calls)
        self.assertIn(("qopen", "erde"), self.win.calls)

    def test_submit(self):
        self.assertEqual(self._dispatch("Submit").unpack(), (True,))
        self.assertIn(("submit",), self.win.calls)

    def test_ask_answer(self):
        self.assertEqual(self._dispatch("AskAnswer").unpack(), ("## Saturn\nRings.",))

    def test_active_file(self):
        self.assertEqual(self._dispatch("ActiveFile").unpack(), ("/v/a.md",))

    def test_list_tabs(self):
        self.assertEqual(self._dispatch("ListTabs").unpack(),
                         (["/v/a.md", "/v/b.md"],))

    def test_dump_state(self):
        self.assertEqual(self._dispatch("DumpState").unpack(), ('{"x": 1}',))

    def test_search_results(self):
        self.assertEqual(self._dispatch("SearchResults").unpack(), (["/v/hit.md"],))

    def test_wait_idle_forwards_timeout(self):
        params = GLib.Variant("(i)", (1234,))
        out = debug_control.DebugControl._dispatch(self.win, "WaitIdle", params)
        self.assertEqual(out.unpack(), (True,))
        self.assertIn(("wait", 1234), self.win.calls)

    def test_unknown_method_raises(self):
        with self.assertRaises(ValueError):
            self._dispatch("Nope")


class TestGating(unittest.TestCase):
    def test_no_flag_registers_nothing(self):
        env = os.environ.pop(debug_control.ENV_FLAG, None)
        try:
            self.assertIsNone(debug_control.maybe_register(None, None, "/x"))
        finally:
            if env is not None:
                os.environ[debug_control.ENV_FLAG] = env


class TestPathConfinement(unittest.TestCase):
    """_debug_confined must accept only paths inside a configured vault root."""

    def setUp(self):
        self._dir = tempfile.mkdtemp()
        self.win = SimpleNamespace(
            _vault_tree=SimpleNamespace(get_vault_paths=lambda: [self._dir]))

    def _confined(self, path):
        return MainWindow._debug_confined(self.win, path)

    def test_inside_vault_allowed(self):
        self.assertTrue(self._confined(os.path.join(self._dir, "sub", "note.md")))

    def test_outside_vault_rejected(self):
        self.assertFalse(self._confined("/etc/passwd"))

    def test_sibling_prefix_not_confused(self):
        # A directory whose path merely starts with the vault path is not inside it.
        self.assertFalse(self._confined(self._dir + "-evil/note.md"))


if __name__ == "__main__":
    unittest.main()
