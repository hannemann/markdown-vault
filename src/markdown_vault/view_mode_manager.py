"""ViewModeManager — handles edit/preview/split view mode switching."""

import logging
from pathlib import Path

from gi.repository import GLib, Gtk

logger = logging.getLogger(__name__)

PREFERRED_VIEW_MODES = ("edit", "render", "split")


class ViewModeManager:
    """Manages view mode switching (edit, preview, split) and preview refresh."""

    PREVIEW_DEBOUNCE_MS = 500

    def __init__(
        self,
        tab_bar,
        view_toggle_buttons: dict[str, Gtk.ToggleButton],
        sidebar,
        backlink_index,
    ) -> None:
        self._tab_bar = tab_bar
        self._view_toggle_buttons = view_toggle_buttons
        self._sidebar = sidebar
        self._backlink_index = backlink_index
        self._preview_debounce_id: int | None = None

    # ── View Mode Switching ───────────────────────────────────────

    def set_view_mode(self, mode: str) -> None:
        """Switch the current tab's view mode."""
        if mode not in PREFERRED_VIEW_MODES:
            logger.warning("Invalid view mode '%s'", mode)
            return
        tab = self._tab_bar.get_current_tab()
        if not tab:
            return
        # Remember the divider position when leaving split, so a round-trip
        # through another mode restores it instead of re-centering.
        if tab.view_mode == "split" and mode != "split":
            self._remember_split(tab)
        tab.view_mode = mode
        self.sync_view_toggle(mode)
        self.apply_view_mode()
        if mode == "split":
            self._restore_split(tab)

    def apply_view_mode(self) -> None:
        """Show/hide editor and preview based on the current tab's view mode."""
        tab = self._tab_bar.get_current_tab()
        if not tab:
            return
        mode = tab.view_mode
        tab.editor.set_visible(mode in ("edit", "split"))
        tab.preview.set_visible(mode in ("render", "split"))
        if mode in ("render", "split"):
            self.refresh_preview()

    def _remember_split(self, tab) -> None:
        """Store the divider position as a width fraction (kept until close)."""
        split = getattr(tab, "split", None)
        if split is None:
            return
        width = split.get_width()
        pos = split.get_position()
        if width > 1 and 0 < pos < width:
            tab.split_ratio = pos / width

    def _restore_split(self, tab) -> None:
        """Restore the remembered ratio, or center 50/50 on first use."""
        split = getattr(tab, "split", None)
        if split is None:
            return
        ratio = getattr(tab, "split_ratio", None)
        if not self._set_split_position(split, ratio):
            # Not allocated yet (e.g. window not mapped) — try once more later.
            GLib.idle_add(self._deferred_split, split, ratio)

    @staticmethod
    def _set_split_position(split, ratio) -> bool:
        """Position the divider by *ratio* (or center if None); True if applied."""
        width = split.get_width()
        if width <= 1:
            return False
        split.set_position(round(ratio * width) if ratio else width // 2)
        return True

    def _deferred_split(self, split, ratio) -> bool:
        self._set_split_position(split, ratio)
        return False  # one-shot for idle_add

    def sync_view_toggle(self, mode: str) -> None:
        """Activate the header toggle button for *mode*.

        ``set_active(True)`` does emit ``toggled`` when the state changes — and
        that emission is load-bearing: ``_exit_graph_mode`` relies on it to
        restore the content stack.  (No-op when the button is already active.)
        """
        btn = self._view_toggle_buttons.get(mode)
        if btn:
            btn.set_active(True)

    # ── Preview Refresh ──────────────────────────────────────────

    def refresh_preview(self) -> None:
        """Update the preview for the current tab."""
        tab = self._tab_bar.get_current_tab()
        if not tab:
            return
        text = tab.editor.get_text()
        base_dir = (
            str(Path(tab.editor.file_path).parent)
            if tab.editor.file_path
            else ""
        )
        tab.preview.update_from_text(text, base_dir)

    def _schedule_preview_refresh(self) -> None:
        """Debounce preview refresh to reduce flicker during rapid typing."""
        if self._preview_debounce_id is not None:
            GLib.source_remove(self._preview_debounce_id)
        self._preview_debounce_id = GLib.timeout_add(
            self.PREVIEW_DEBOUNCE_MS, self._on_preview_debounce,
        )

    def _on_preview_debounce(self) -> bool:
        self._preview_debounce_id = None
        self.refresh_preview()
        tab = self._tab_bar.get_current_tab()
        if tab and tab.editor and tab.editor.file_path:
            self._sidebar.refresh_backlinks(tab.editor.file_path)
        return False

    # ── Editor Callbacks ─────────────────────────────────────────

    def on_editor_text_changed(self, editor) -> None:
        """Update preview and sidebar when editor content changes."""
        tab = self._tab_bar.get_current_tab()
        if tab and tab.editor is editor:
            if editor.file_path:
                self._backlink_index.update_file(
                    editor.file_path, editor.get_text()
                )
            if tab.preview.get_visible():
                self._schedule_preview_refresh()
            self._sidebar.update_text_only(
                editor.file_path, editor.get_text()
            )

    def cancel_preview_debounce(self) -> None:
        """Cancel any pending preview debounce (e.g. on window close)."""
        if self._preview_debounce_id is not None:
            GLib.source_remove(self._preview_debounce_id)
            self._preview_debounce_id = None
