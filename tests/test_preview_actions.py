"""Tests for markdown_vault.app.preview_actions.

The point of this module is that a preview signal names no file: the handler has
to find the tab that owns the emitting widget, and that tab is often *not* the
current one (a background render, a download finishing while the user has moved
on). Both tests below pin exactly that.
"""

import unittest
import unittest.mock

from markdown_vault.app.preview_actions import PreviewActions


class FakeTab:
    def __init__(self, path):
        self.preview = unittest.mock.Mock(name=f"preview:{path}")
        self.editor = unittest.mock.Mock(name=f"editor:{path}")
        self.editor.file_path = path


class FakeTabBar:
    """Only the surface PreviewActions uses."""

    def __init__(self, *tabs):
        self._tabs = list(tabs)

    def all_tabs(self):
        return list(self._tabs)

    def close(self, tab):
        self._tabs.remove(tab)


def _actions(tab_bar):
    return PreviewActions(tab_bar=tab_bar, sidebar=unittest.mock.Mock(),
                          refresh_preview=unittest.mock.Mock(),
                          toast=unittest.mock.Mock())


class TestTabOfPreview(unittest.TestCase):
    def test_finds_the_emitting_tab_not_the_current_one(self):
        background, current = FakeTab("/n/a.md"), FakeTab("/n/b.md")
        actions = _actions(FakeTabBar(background, current))
        self.assertIs(actions._tab_of(background.preview), background)

    def test_an_unknown_preview_yields_none(self):
        actions = _actions(FakeTabBar(FakeTab("/n/a.md")))
        self.assertIsNone(actions._tab_of(unittest.mock.Mock()))


class TestImageDownloadedTabGone(unittest.TestCase):
    """The download runs on a worker; the tab may be closed when it returns."""

    def test_a_closed_tab_is_not_written_to(self):
        tab = FakeTab("/n/a.md")
        bar = FakeTabBar(tab)
        actions = _actions(bar)
        bar.close(tab)
        actions._image_downloaded(tab, "https://x/i.png", "attachments/i.png", None)
        tab.editor.get_text.assert_not_called()

    def test_an_open_tab_is_rewritten(self):
        tab = FakeTab("/n/a.md")
        tab.editor.get_text.return_value = "![i](https://x/i.png)"
        actions = _actions(FakeTabBar(tab))
        with unittest.mock.patch(
                "markdown_vault.importers.web_import.rewrite_image_url",
                return_value="![i](attachments/i.png)") as rewrite:
            actions._image_downloaded(tab, "https://x/i.png", "attachments/i.png",
                                      None)
        rewrite.assert_called_once()
        tab.editor._buffer.set_text.assert_called_once_with("![i](attachments/i.png)")


if __name__ == "__main__":
    unittest.main()
