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


if __name__ == "__main__":
    unittest.main()
