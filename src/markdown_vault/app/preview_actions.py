"""What the user does *inside* the rendered note: ticking a checkbox and
downloading a remote image.

Both write into the note's source and then have to bring the rest back in sync —
re-render the preview, refresh the sidebar. Both also start from the same
question, which is easy to get wrong: *which* tab sent this? Not the current one.
A preview can emit while another tab is active (a background render, a download
finishing), and taking the current tab would edit the wrong file (R7.4).

Kept out of ``MainWindow`` because neither is window business; the window only
lends the tab bar and the two surfaces that need refreshing.
"""

import logging
import threading
from pathlib import Path

import gi

logger = logging.getLogger(__name__)

gi.require_version("Gtk", "4.0")

from gi.repository import GLib

from markdown_vault.core import path_utils
from markdown_vault.markdown import md_text


class PreviewActions:
    """Checkbox toggling and image download for the rendered view."""

    def __init__(self, *, tab_bar, sidebar, refresh_preview, toast) -> None:
        self._tab_bar = tab_bar
        self._sidebar = sidebar
        self._refresh_preview = refresh_preview
        self._toast = toast

    def _tab_of(self, preview):
        """The tab whose preview emitted the signal — never the current one."""
        return next((t for t in self._tab_bar._tabs.values()
                     if t.preview is preview), None)

    def _after_source_change(self, tab) -> None:
        """Bring the other views in line with the edited source."""
        if tab.preview.get_visible():
            self._refresh_preview()
        if self._sidebar.get_visible():
            self._sidebar.update_text_only(tab.editor.file_path, tab.editor.get_text())

    # ── checkbox ───────────────────────────────────────────────────

    def on_checkbox_toggled(self, preview, line: int, checked: bool) -> None:
        """Flip the task-list checkbox at *line* in the note's source."""
        tab = self._tab_of(preview)
        if not tab or not tab.editor.file_path:
            return
        lines = tab.editor.get_text().split("\n")
        if line < 0 or line >= len(lines):
            logger.debug("Checkbox line %s out of range (total %s)", line, len(lines))
            return
        new_line = md_text.set_checkbox_state(lines[line], checked)
        if new_line is None:
            return                      # not a checkbox line, or already correct

        # Replace just that line, as one undoable step.
        buffer = tab.editor._buffer
        _ok, start = buffer.get_iter_at_line(line)
        _ok, end = buffer.get_iter_at_line(line)
        end.forward_to_line_end()
        buffer.begin_user_action()
        buffer.delete(start, end)
        buffer.insert(start, new_line)
        buffer.end_user_action()
        self._after_source_change(tab)

    # ── image download ─────────────────────────────────────────────

    def on_image_download(self, preview, uri: str) -> None:
        """Right-click "Download Image": fetch a remote image into the note's
        ``attachments/<note-name>/`` and rewrite its source links to the local
        path. Runs on a worker thread so the UI never blocks."""
        tab = self._tab_of(preview)
        if not tab or not tab.editor.file_path:
            return
        file_path = tab.editor.file_path
        note_dir = Path(file_path).parent
        # Attachments mirror the note's location under the vault's attachments/
        # tree (…/attachments/<subfolder>/<note>/), linked relative to the note.
        vault_root = path_utils.find_vault_for_dir(str(note_dir)) or str(note_dir)
        from markdown_vault.importers import web_import
        dest_dir, rel_prefix = web_import.attachment_target(
            vault_root, note_dir, Path(file_path).stem)
        self._toast("Downloading image…")

        def worker():
            try:
                rel = web_import.save_one_image(uri, dest_dir, rel_prefix)
            except Exception as exc:    # never let a worker crash the app
                logger.warning("Image download failed for %s: %s", uri, exc,
                               exc_info=True)
                GLib.idle_add(self._image_downloaded, tab, uri, None, str(exc))
                return
            GLib.idle_add(self._image_downloaded, tab, uri, rel, None)

        threading.Thread(target=worker, daemon=True).start()

    def _image_downloaded(self, tab, uri: str, rel, error) -> bool:
        """Back on the main thread: rewrite the source (if the tab still exists)
        and report via a toast. Error toasts stay until dismissed."""
        if rel is None:
            self._toast(f"Image download failed{': ' + error if error else ''}",
                        timeout=0)
            return False
        if tab not in self._tab_bar._tabs.values():
            return False                # tab closed mid-download; file is on disk
        from markdown_vault.importers import web_import
        current = tab.editor.get_text()
        new_text = web_import.rewrite_image_url(current, uri, rel)
        if new_text != current:
            buffer = tab.editor._buffer
            buffer.begin_user_action()
            buffer.set_text(new_text)
            buffer.end_user_action()
            self._after_source_change(tab)
        self._toast("Image downloaded")
        return False
