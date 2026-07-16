"""Tests for markdown_vault.sidebar — right sidebar sub-views."""

import tempfile
import unittest
from pathlib import Path

from src.sidebar import Sidebar


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
        self.assertIn("Title", children[0].get_text())

    def test_outline_multiple_levels(self):
        text = "# H1\n\n## H2\n\n### H3"
        self.sidebar._refresh_outline(text)
        children = list(self.sidebar._outline_list["list"])
        self.assertEqual(len(children), 3)

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
        self.assertIn("Real Title", children[0].get_text())
        self.assertIn("Real H2", children[1].get_text())

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
        self.assertIn("Title", children[0].get_text())
        self.assertIn("Real H2", children[1].get_text())

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
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            self.sidebar._refresh_git(tmpdir + "/file.md")
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


if __name__ == "__main__":
    unittest.main()