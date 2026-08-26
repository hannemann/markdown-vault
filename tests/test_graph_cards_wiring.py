"""Wiring test: MainWindow forwards a collected node to the sidebar.

The graph explorer's node-carded(path, colour) is routed by
``MainWindow._on_graph_node_carded``, which forwards it to ``sidebar.add_card_for_node``
(switch=True, so the Cards tab comes to the front) and reveals the sidebar when it is
hidden. A unit test of the sidebar leaves that forwarding unguarded — is the colour kept
distinct from the path, is switch=True passed, is the reveal gated on visibility. Reach
the handler via the unbound method with a stand-in self (no window construction).
"""

import unittest
from unittest import mock

from markdown_vault.app.app_window import MainWindow


def _win(visible=True):
    me = mock.Mock()
    me._sidebar.get_visible.return_value = visible
    return me


class TestNodeCardedForwarding(unittest.TestCase):
    def test_forwards_path_and_colour_with_switch_true(self):
        me = _win()
        MainWindow._on_graph_node_carded(me, None, "/v/a.md", "#abcdef")
        me._sidebar.add_card_for_node.assert_called_once_with(
            "/v/a.md", "#abcdef", switch=True)

    def test_reveals_sidebar_when_hidden(self):
        me = _win(visible=False)
        MainWindow._on_graph_node_carded(me, None, "/v/a.md", "#000000")
        me._sidebar_toggle.set_active.assert_called_once_with(True)

    def test_does_not_reveal_when_already_visible(self):
        me = _win(visible=True)
        MainWindow._on_graph_node_carded(me, None, "/v/a.md", "#000000")
        me._sidebar_toggle.set_active.assert_not_called()


class TestGraphLensPersistence(unittest.TestCase):
    """MainWindow reads/writes the cursor-lens options under settings 'graph.*'."""

    def test_reads_defaults_from_empty_settings(self):
        me = mock.Mock()
        me._settings = {}
        self.assertEqual(
            MainWindow._graph_lens_config(me),
            {"fisheye": True, "labels": True, "radius": 140.0, "strength": 2.6,
             "label_radius": 160.0, "lens_in_sidebar": True})

    def test_reads_stored_values(self):
        me = mock.Mock()
        me._settings = {"graph": {"fisheye": False, "cursor_labels": False,
                                  "lens_radius": 90.0, "lens_strength": 4.0,
                                  "label_radius": 220.0, "lens_in_sidebar": False}}
        self.assertEqual(
            MainWindow._graph_lens_config(me),
            {"fisheye": False, "labels": False, "radius": 90.0, "strength": 4.0,
             "label_radius": 220.0, "lens_in_sidebar": False})

    def test_mini_graph_gates_fisheye_and_labels_on_the_opt_in(self):
        me = mock.Mock()
        me._graph_lens_config.return_value = {
            "fisheye": True, "labels": True, "radius": 90.0, "strength": 4.0,
            "label_radius": 220.0, "lens_in_sidebar": False}
        # Opt-out: fisheye/labels forced off, the numbers pass through unchanged.
        self.assertEqual(MainWindow._mini_graph_lens_config(me),
                         (False, False, 90.0, 4.0, 220.0))
        me._graph_lens_config.return_value["lens_in_sidebar"] = True
        self.assertEqual(MainWindow._mini_graph_lens_config(me),
                         (True, True, 90.0, 4.0, 220.0))

    def test_schema_defaults_match_the_code_defaults(self):
        # The config schema (survives a load) and graph_view.LENS_DEFAULTS (the code
        # default) must not drift; read the schema's graph branch through the real path.
        from markdown_vault.core.config import _DEFAULT_SETTINGS
        from markdown_vault.graph.graph_view import LENS_DEFAULTS
        me = mock.Mock()
        me._settings = {"graph": dict(_DEFAULT_SETTINGS["graph"])}
        self.assertEqual(MainWindow._graph_lens_config(me), LENS_DEFAULTS)

    def test_clamps_garbage_from_disk(self):
        me = mock.Mock()
        me._settings = {"graph": {"lens_strength": -5, "lens_radius": "abc"}}
        cfg = MainWindow._graph_lens_config(me)
        self.assertEqual(cfg["strength"], 0.6)     # clamped to the range floor
        self.assertEqual(cfg["radius"], 140.0)     # bad type -> default

    def test_persist_writes_all_keys_and_saves(self):
        me = mock.Mock()
        me._settings = {}
        cfg = {"fisheye": False, "labels": True, "radius": 90.0, "strength": 3.0,
               "label_radius": 220.0, "lens_in_sidebar": False}
        with mock.patch("markdown_vault.app.app_window.config.save_settings") as save:
            MainWindow._persist_graph_lens_config(me, cfg)
        self.assertEqual(me._settings["graph"], {
            "fisheye": False, "cursor_labels": True, "lens_radius": 90.0,
            "lens_strength": 3.0, "label_radius": 220.0, "lens_in_sidebar": False})
        save.assert_called_once_with()
        me._sidebar.refresh_mini_graph_lens.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
