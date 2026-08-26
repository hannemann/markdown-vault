"""Tests for markdown_vault.graph.graph_explorer — the full-graph explorer.

Constructing a GraphExplorer would build a WebKit WebView (needs a display), so the
control logic is reached the same way as the other graph tests: the unbound method with
a stand-in self carrying only the widgets it reads.
"""

import unittest
from unittest import mock

from markdown_vault.graph.graph_explorer import GraphExplorer, _LENS_DEFAULTS


def _me(fisheye=True, labels=True, radius=140.0, strength=2.6, label_radius=160.0):
    me = mock.Mock()
    me._fisheye_chk.get_active.return_value = fisheye
    me._labels_chk.get_active.return_value = labels
    me._radius_scale.get_value.return_value = radius
    me._strength_scale.get_value.return_value = strength
    me._label_radius_scale.get_value.return_value = label_radius
    return me


class TestLensControls(unittest.TestCase):
    def test_defaults_are_on_with_the_seed_values(self):
        self.assertEqual(_LENS_DEFAULTS, {"fisheye": True, "labels": True,
                                          "radius": 140.0, "strength": 2.6,
                                          "label_radius": 160.0})

    def test_on_lens_changed_builds_the_dict_from_the_widgets(self):
        me = _me(fisheye=True, labels=False, radius=200.0, strength=3.5, label_radius=100.0)
        GraphExplorer._on_lens_changed(me)
        self.assertEqual(me._lens, {"fisheye": True, "labels": False, "radius": 200.0,
                                    "strength": 3.5, "label_radius": 100.0})
        me._apply_lens.assert_called_once_with()

    def test_on_lens_changed_notifies_the_callback_with_a_copy(self):
        me = _me(fisheye=False, labels=True, radius=90.0, strength=1.2, label_radius=220.0)
        GraphExplorer._on_lens_changed(me)
        me._on_lens_config_changed.assert_called_once()
        cfg = me._on_lens_config_changed.call_args.args[0]
        self.assertEqual(cfg, {"fisheye": False, "labels": True, "radius": 90.0,
                               "strength": 1.2, "label_radius": 220.0})
        self.assertIsNot(cfg, me._lens)   # a copy, so later edits don't leak back

    def test_no_callback_is_tolerated(self):
        me = _me()
        me._on_lens_config_changed = None
        GraphExplorer._on_lens_changed(me)   # must not raise

    def test_apply_lens_pushes_the_dict_to_the_graph_in_order(self):
        me = mock.Mock()
        me._lens = {"fisheye": False, "labels": True, "radius": 90.0,
                    "strength": 1.2, "label_radius": 220.0}
        GraphExplorer._apply_lens(me)
        me._graph.set_lens_config.assert_called_once_with(False, True, 90.0, 1.2, 220.0)


if __name__ == "__main__":
    unittest.main()
