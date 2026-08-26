"""Instrumentation of the graph render component's JS return channel.

`graph_view.GraphView._on_message` is the callback WebKit invokes for every click in
the graph. It parses the raw message with `parse_graph_message` and routes it by the
view's click mode: the main explorer (collect mode) puts a card on a single click and
opens on a double click; the sidebar's mini-graph keeps the default — a single click
opens, as before.

The routing never touches a WebView, so it is unit-reachable without one: call the
unbound method with a stand-in `self`. Importing this module pulls in WebKit, which is
fine headless — other test modules already do it — and needs no display (only
*constructing* a WebView would).
"""

import unittest
from unittest import mock

from markdown_vault.graph import graph_view
from markdown_vault.graph.graph_view import parse_graph_message

_LOGGER = "markdown_vault.graph.graph_view"


def _me(collect):
    """A stand-in self carrying only the click-mode flag the routing reads."""
    me = mock.Mock()
    me._collect_on_click = collect
    return me


def _msg(me, raw):
    js = mock.Mock()
    js.to_string.return_value = raw
    graph_view.GraphView._on_message(me, None, js)
    return me


class _Unreadable:
    """A js_value whose to_string() fails, as a dead web process would produce."""

    def to_string(self):
        raise RuntimeError("web process gone")


class TestParseGraphMessage(unittest.TestCase):
    def test_tip(self):
        self.assertEqual(parse_graph_message("tip\t/v/a.md"), ("tip", "/v/a.md", ""))

    def test_click_with_color(self):
        self.assertEqual(parse_graph_message("click\t/v/a.md\t#123456"),
                         ("click", "/v/a.md", "#123456"))

    def test_click_without_color(self):
        self.assertEqual(parse_graph_message("click\t/v/a.md"),
                         ("click", "/v/a.md", ""))

    def test_clicknt(self):
        self.assertEqual(parse_graph_message("clicknt\t/v/a.md\t#abcdef"),
                         ("clicknt", "/v/a.md", "#abcdef"))

    def test_dblclick(self):
        self.assertEqual(parse_graph_message("dblclick\t/v/a.md"),
                         ("dblclick", "/v/a.md", ""))

    def test_empty_and_unknown_yield_no_kind(self):
        self.assertEqual(parse_graph_message(""), ("", "", ""))
        self.assertEqual(parse_graph_message("bogus\t/v/a.md"), ("", "", ""))


class TestOnMessageFailurePath(unittest.TestCase):
    def test_logs_and_drops_the_click_when_the_js_value_is_unreadable(self):
        me = _me(False)
        with self.assertLogs(_LOGGER, level="WARNING"):
            graph_view.GraphView._on_message(me, None, _Unreadable())
        me.emit.assert_not_called()      # the click is dropped, but now audibly


class TestOpenMode(unittest.TestCase):
    """Default mode (mini-graph): a single click opens, unchanged."""

    def test_click_opens(self):
        me = _msg(_me(False), "click\t/v/a.md\t#123456")
        me.emit.assert_called_once_with("node-activated", "/v/a.md")

    def test_clicknt_opens_new_tab(self):
        me = _msg(_me(False), "clicknt\t/v/a.md\t#123456")
        me.emit.assert_called_once_with("node-activated-new-tab", "/v/a.md")

    def test_dblclick_ignored(self):
        me = _msg(_me(False), "dblclick\t/v/a.md")
        me.emit.assert_not_called()      # the single click already opened


class TestCollectMode(unittest.TestCase):
    """Main explorer: a single click collects a card, a double click opens."""

    def test_click_collects_card_with_color(self):
        me = _msg(_me(True), "click\t/v/a.md\t#123456")
        me.emit.assert_called_once_with("node-carded", "/v/a.md", "#123456")

    def test_clicknt_collects_and_opens_new_tab(self):
        me = _msg(_me(True), "clicknt\t/v/a.md\t#123456")
        me.emit.assert_any_call("node-carded", "/v/a.md", "#123456")
        me.emit.assert_any_call("node-activated-new-tab", "/v/a.md")
        self.assertEqual(me.emit.call_count, 2)

    def test_dblclick_opens(self):
        me = _msg(_me(True), "dblclick\t/v/a.md")
        me.emit.assert_called_once_with("node-activated", "/v/a.md")


class TestTipRouting(unittest.TestCase):
    def test_tip_calls_send_tip_in_either_mode(self):
        for collect in (False, True):
            me = _msg(_me(collect), "tip\t/v/a.md")
            me._send_tip.assert_called_once_with("/v/a.md")
            me.emit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
