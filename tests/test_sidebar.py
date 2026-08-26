"""Tests for markdown_vault.ui.sidebar — right sidebar sub-views."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from gi.repository import GLib
from markdown_vault.core.event_router import FileEvent
from markdown_vault.ui.sidebar import Sidebar


def _outline_text(row):
    """Extract heading text from an outline row (indent guides + a button)."""
    return list(row)[-1].get_child().get_text()


class TestSidebarOutline(unittest.TestCase):
    """Tests for outline (heading) extraction."""

    def setUp(self):
        self.sidebar = Sidebar()

    def test_outline_empty_text(self):
        self.sidebar._refresh_outline("")
        # Should not crash, list should be empty
        children = list(self.sidebar._outline_list["list"])
        self.assertEqual(len(children), 0)

    def test_outline_single_heading(self):
        text = "# Title\n\nContent"
        self.sidebar._refresh_outline(text)
        children = list(self.sidebar._outline_list["list"])
        self.assertEqual(len(children), 1)
        self.assertIn("Title", _outline_text(children[0]))

    def test_outline_multiple_levels(self):
        text = "# H1\n\n## H2\n\n### H3"
        self.sidebar._refresh_outline(text)
        children = list(self.sidebar._outline_list["list"])
        self.assertEqual(len(children), 3)

    def test_outline_strips_inline_markdown_from_heading(self):
        # An imported heading wrapped in bold must read as clean text, not **...**.
        self.sidebar._refresh_outline("# **Attention Is All You Need**\n\nbody")
        children = list(self.sidebar._outline_list["list"])
        self.assertEqual(_outline_text(children[0]), "Attention Is All You Need")

    def test_outline_skips_code_fences(self):
        """Headings inside ``` fenced code blocks should be ignored."""
        text = """# Real Title

```python
# Not a heading
def foo():
    pass
```

## Real H2"""
        self.sidebar._refresh_outline(text)
        children = list(self.sidebar._outline_list["list"])
        # Should only find "Real Title" and "Real H2"
        self.assertEqual(len(children), 2)
        self.assertIn("Real Title", _outline_text(children[0]))
        self.assertIn("Real H2", _outline_text(children[1]))

    def test_outline_skips_indented_fences(self):
        """Indented fenced code blocks (in lists) should also be tracked."""
        text = """# Title

- List item with code:

```python
# Not a heading
print("hello")
```

## Real H2"""
        self.sidebar._refresh_outline(text)
        children = list(self.sidebar._outline_list["list"])
        self.assertEqual(len(children), 2)
        self.assertIn("Title", _outline_text(children[0]))
        self.assertIn("Real H2", _outline_text(children[1]))

    def test_outline_tilde_fences(self):
        """~~~ fences should also be tracked."""
        text = """# Title

~~~
# Not a heading
~~~

## Real H2"""
        self.sidebar._refresh_outline(text)
        children = list(self.sidebar._outline_list["list"])
        self.assertEqual(len(children), 2)

    def test_outline_nested_fences(self):
        """Nested fences (outer ~~~ inner ```) should be handled."""
        text = """# Title

~~~markdown
```python
# Not a heading
```
~~~

## Real H2"""
        self.sidebar._refresh_outline(text)
        children = list(self.sidebar._outline_list["list"])
        self.assertEqual(len(children), 2)


class TestSidebarDetails(unittest.TestCase):
    """Tests for file details view."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.sidebar = Sidebar()

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_refresh_details_with_file(self):
        fp = Path(self._tmp) / "note.md"
        fp.write_text("Hello world\nSecond line")
        self.sidebar._refresh_details(str(fp), "Hello world\nSecond line")
        label_text = self.sidebar._details_label.get_text()
        self.assertIn("note.md", label_text)
        self.assertIn("Words: 4", label_text)
        self.assertIn("Lines: 2", label_text)


class TestSidebarGit(unittest.TestCase):
    """Tests for git view."""

    def setUp(self):
        self.sidebar = Sidebar()

    def test_refresh_git_no_file(self):
        self.sidebar._refresh_git(None)
        self.assertEqual(self.sidebar._git_status_label.get_text(), "No file open")

    def test_refresh_git_not_a_repo(self):
        import tempfile, time
        with tempfile.TemporaryDirectory() as tmpdir:
            self.sidebar._refresh_git(tmpdir + "/file.md")
            time.sleep(0.2)
            # Process pending GLib idle callbacks (no main loop in tests)
            ctx = GLib.MainContext.default()
            while ctx.pending():
                ctx.iteration(False)
            self.assertEqual(self.sidebar._git_status_label.get_text(), "Not a git repository")


class TestSidebarBacklinks(unittest.TestCase):
    """Tests for backlinks view."""

    def setUp(self):
        self.sidebar = Sidebar()

    def test_refresh_backlinks_no_file(self):
        self.sidebar._refresh_backlinks(None)
        children = list(self.sidebar._backlinks_list["list"])
        self.assertEqual(len(children), 1)
        self.assertIn("Open a file", children[0].get_text())


class TestSidebarRefreshExternalEvent(unittest.TestCase):
    """Tests for Sidebar.refresh() — must not hijack the sidebar
    away from the active tab on external file events."""

    def _make_sidebar(self, get_active_tab_info=None):
        from unittest.mock import MagicMock
        sidebar = Sidebar(get_active_tab_info=get_active_tab_info)
        sidebar._refresh_outline = MagicMock()
        sidebar._refresh_backlinks = MagicMock()
        sidebar._refresh_details = MagicMock()
        sidebar._refresh_git = MagicMock()
        sidebar.get_visible = MagicMock(return_value=False)
        return sidebar

    def test_external_event_uses_active_tab_info(self):
        """Event file differs from active tab: sidebar should refresh
        with the active tab's file/text, not the event's file."""
        active_file = "/vault/active.md"
        active_text = "# Active\n\nContent"
        sidebar = self._make_sidebar(
            get_active_tab_info=lambda: (active_file, active_text),
        )
        sidebar._current_file = active_file

        sidebar.refresh(FileEvent("/vault", "/vault/other.md", "created"))

        sidebar._refresh_outline.assert_called_once_with(active_text)
        sidebar._refresh_backlinks.assert_called_once_with(active_file)
        sidebar._refresh_details.assert_called_once_with(active_file, active_text)

    def test_external_event_content_changed_uses_active_tab(self):
        """content_changed should go through update_text_only with
        the active tab's info."""
        active_file = "/vault/active.md"
        active_text = "# Active"
        sidebar = self._make_sidebar(
            get_active_tab_info=lambda: (active_file, active_text),
        )
        sidebar._current_file = active_file

        sidebar.refresh(
            FileEvent("/vault", "/vault/active.md", "content_changed"),
        )

        sidebar._refresh_outline.assert_called_once_with(active_text)
        sidebar._refresh_details.assert_called_once_with(active_file, active_text)

    def test_external_event_no_active_tab_does_nothing(self):
        """When get_active_tab_info returns (None, ''), refresh
        should not touch sidebar state."""
        sidebar = self._make_sidebar(
            get_active_tab_info=lambda: (None, ""),
        )

        sidebar.refresh(FileEvent("/vault", "/vault/other.md", "created"))

        sidebar._refresh_outline.assert_not_called()
        sidebar._refresh_backlinks.assert_not_called()
        sidebar._refresh_details.assert_not_called()

    def test_external_event_no_callback_does_nothing(self):
        """When no get_active_tab_info is provided, refresh
        should not hijack to the event's file."""
        sidebar = self._make_sidebar(get_active_tab_info=None)

        sidebar.refresh(FileEvent("/vault", "/vault/other.md", "created"))

        sidebar._refresh_outline.assert_not_called()
        sidebar._refresh_backlinks.assert_not_called()
        sidebar._refresh_details.assert_not_called()


class TestSidebarGitBatching(unittest.TestCase):
    """F21: _refresh_git must open one batch_reads block, so the three hardened reads
    (is_git_repo, get_status, get_diff_stat) enumerate the repo config once, not three times.
    Guards the wiring — the memoisation mechanism itself is covered in
    test_git_integration. Remove the `with batch_reads()` from _refresh_git and this
    reddens while the mechanism test stays green."""

    def setUp(self):
        import os
        self.sidebar = Sidebar()
        self._tmp = Path(tempfile.mkdtemp())
        os.system(f"git init {self._tmp} >/dev/null 2>&1")
        os.system(f"git -C {self._tmp} config user.email 't@t.c'")
        os.system(f"git -C {self._tmp} config user.name 'T'")
        (self._tmp / "note.md").write_text("one\n")
        os.system(f"git -C {self._tmp} add note.md >/dev/null 2>&1")
        os.system(f"git -C {self._tmp} commit -m x >/dev/null 2>&1")
        (self._tmp / "note.md").write_text("one\ntwo\n")

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_one_refresh_enumerates_config_once(self):
        import threading
        import unittest.mock as mock
        import markdown_vault.ui.sidebar as sb
        from markdown_vault.vault import git_integration as gi
        real_run = gi._run_git
        enumerations = []
        threads = []

        def counting(args, cwd, harden=True):
            if args[:3] == ["config", "--list", "--show-scope"]:
                enumerations.append(1)
            return real_run(args, cwd, harden=harden)

        class _CapturingThread(threading.Thread):
            def __init__(self, target, daemon=None):
                super().__init__(target=target, daemon=daemon)
                threads.append(self)

        # A REAL worker thread, joined — not a synchronous stub. batch_reads caches
        # thread-locally, so a block opened on the CALLER thread would leave the worker
        # uncached; a same-thread stub cannot see that (ZL1). idle_add is a no-op: every
        # counted git call is inside _work(), _apply (which it schedules) touches no git.
        with mock.patch.object(sb.threading, "Thread", _CapturingThread), \
             mock.patch.object(sb.GLib, "idle_add", lambda *a, **k: None), \
             mock.patch.object(gi, "_run_git", side_effect=counting):
            self.sidebar._refresh_git(str(self._tmp / "note.md"))
            # join is load-bearing, not tidiness: the enumeration is the FIRST git call in
            # the worker, so the counter reads 1 mid-flight even when batching is broken —
            # measured. Without the join a real regression (3 enumerations) passes green.
            for t in threads:
                t.join(timeout=10)
        self.assertEqual(len(enumerations), 1,
                         "one refresh must enumerate once — and the batch block must sit on "
                         "the worker thread; batch_reads is thread-local, so a block opened "
                         "on the caller thread would leave the worker uncached")


class TestSidebarCards(unittest.TestCase):
    """The Cards sub-view: collecting a graph node as a card."""

    def setUp(self):
        self.sidebar = Sidebar()

    def _note(self, directory, body):
        path = Path(directory) / "note.md"
        path.write_text(body, encoding="utf-8")
        return str(path)

    def test_add_card_for_node_resolves_and_switches(self):
        with tempfile.TemporaryDirectory() as d:
            self.sidebar.set_vault_paths([d])
            path = self._note(
                d, "---\ntitle: My Note\ndescription: A short note.\n---\nx\n")
            self.assertTrue(self.sidebar.add_card_for_node(path, "#abcdef", switch=True))
            cards = self.sidebar._cards_panel._store.cards()
            self.assertEqual(len(cards), 1)
            self.assertEqual(cards[0].title, "My Note")
            self.assertEqual(cards[0].desc, "A short note.")
            self.assertEqual(cards[0].vault, Path(d).name)
            self.assertEqual(cards[0].color, "#abcdef")
            self.assertEqual(self.sidebar._stack.get_visible_child_name(), "cards")

    def test_switch_false_pulses_icon_and_keeps_current_tab(self):
        with tempfile.TemporaryDirectory() as d:
            self.sidebar.set_vault_paths([d])
            path = self._note(d, "# Body\n")
            before = self.sidebar._stack.get_visible_child_name()
            self.sidebar.add_card_for_node(path, "#000000", switch=False)
            self.assertEqual(self.sidebar._stack.get_visible_child_name(), before)
            self.assertIn(
                "card-pulse", self.sidebar._rail_buttons["cards"].get_css_classes())

    def test_duplicate_node_not_collected_twice(self):
        with tempfile.TemporaryDirectory() as d:
            self.sidebar.set_vault_paths([d])
            path = self._note(d, "# Body\n")
            self.assertTrue(self.sidebar.add_card_for_node(path, "#111111"))
            self.assertFalse(self.sidebar.add_card_for_node(path, "#222222"))
            self.assertEqual(len(self.sidebar._cards_panel._store), 1)

    def test_vault_label_uses_containing_root(self):
        self.sidebar.set_vault_paths(["/home/u/Notes", "/home/u/Other"])
        self.assertEqual(
            self.sidebar._vault_label_for("/home/u/Notes/sub/a.md"), "Notes")

    def test_vault_label_falls_back_to_parent_dir(self):
        self.sidebar.set_vault_paths(["/other/vault"])
        self.assertEqual(self.sidebar._vault_label_for("/some/place/note.md"), "place")


class TestSidebarMiniGraph(unittest.TestCase):
    """The sidebar's lazily-built mini-graph."""

    def test_mini_graph_asks_for_the_bottom_corner(self):
        # The narrow sidebar graph must place the node-info panel bottom-right
        # (bottom_panel=True), clear of the top-left legend.
        sidebar = Sidebar()
        sidebar._get_graph_payload = lambda _p: {"nodes": [], "edges": []}
        sidebar._graph_panel = mock.Mock()   # stand-in, skips the real widget check
        with mock.patch("markdown_vault.graph.graph_view.GraphView") as gv:
            sidebar._refresh_graph()
        self.assertTrue(gv.call_args.kwargs.get("bottom_panel"))


if __name__ == "__main__":
    unittest.main()