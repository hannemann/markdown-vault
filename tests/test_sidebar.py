"""Tests for markdown_vault.sidebar — right sidebar sub-views."""

import tempfile
import unittest
from pathlib import Path

from gi.repository import GLib
from markdown_vault.core.event_router import FileEvent
from markdown_vault.sidebar import Sidebar


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


if __name__ == "__main__":
    unittest.main()