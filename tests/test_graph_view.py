"""Instrumentation of the graph render component's JS return channel.

`graph_view.GraphView._on_message` is the callback WebKit invokes for every click
in the graph. If `js_value.to_string()` raises, the old code returned silently — a
dropped click with no trace, the exact swallow the logging convention forbids. These
guard the **failure path** (it logs and drops) and confirm the added log branch did
not break normal routing.

The failure path never touches `self`, so it is unit-reachable without a WebView:
call the unbound method with a stand-in `self`. Importing this module pulls in WebKit,
which is fine headless — five other test modules already do it — and needs no display
(only *constructing* a WebView would).
"""

import unittest
from unittest import mock

from markdown_vault.graph import graph_view

_LOGGER = "markdown_vault.graph.graph_view"


class _Unreadable:
    """A js_value whose to_string() fails, as a dead web process would produce."""

    def to_string(self):
        raise RuntimeError("web process gone")


class TestOnMessageInstrumentation(unittest.TestCase):
    def test_logs_and_drops_the_click_when_the_js_value_is_unreadable(self):
        me = mock.Mock()
        with self.assertLogs(_LOGGER, level="WARNING"):
            graph_view.GraphView._on_message(me, None, _Unreadable())
        me.emit.assert_not_called()      # the click is dropped, but now audibly

    def test_routes_a_plain_click_to_node_activated_without_logging(self):
        me = mock.Mock()
        js = mock.Mock()
        js.to_string.return_value = "0\t/v/note.md"
        with self.assertNoLogs(_LOGGER, level="WARNING"):
            graph_view.GraphView._on_message(me, None, js)
        me.emit.assert_called_once_with("node-activated", "/v/note.md")

    def test_routes_middle_click_to_new_tab(self):
        me = mock.Mock()
        js = mock.Mock()
        js.to_string.return_value = "1\t/v/note.md"
        graph_view.GraphView._on_message(me, None, js)
        me.emit.assert_called_once_with("node-activated-new-tab", "/v/note.md")

    def test_routes_a_tip_request_to_send_tip(self):
        me = mock.Mock()
        js = mock.Mock()
        js.to_string.return_value = "tip\t/v/note.md"
        graph_view.GraphView._on_message(me, None, js)
        me._send_tip.assert_called_once_with("/v/note.md")
        me.emit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
