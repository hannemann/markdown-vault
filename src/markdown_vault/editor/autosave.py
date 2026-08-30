"""Periodic autosave manager for open editor tabs.

Decoupled from MainWindow — receives callbacks for getting dirty tabs
and saving them, so the timer logic has no direct dependency on the
UI layer.
"""

import logging
from pathlib import Path

from gi.repository import GLib

from markdown_vault.core.i18n import _

logger = logging.getLogger(__name__)


class AutosaveManager:
    """Manages a periodic GLib timer that saves all modified editor tabs.

    Parameters
    ----------
    interval : int
        Seconds between autosave ticks.  0 disables the timer.
    get_dirty_tabs : callable
        ``() -> list[Tab]`` — return tabs whose editor is modified.
    save_tab : callable
        ``(tab) -> bool`` — persist *tab* to disk, return True on success.
    on_save_failed : callable
        ``(file_path, msg) -> None`` — called when a save fails (e.g. to
        show an error banner).
    """

    def __init__(
        self,
        interval: int,
        get_dirty_tabs,
        save_tab,
        on_save_failed,
    ) -> None:
        self._interval = interval
        self._get_dirty_tabs = get_dirty_tabs
        self._save_tab = save_tab
        self._on_save_failed = on_save_failed
        self._timer_id: int | None = None

    def start(self) -> None:
        """Start the autosave timer."""
        if self._interval <= 0:
            return
        self._timer_id = GLib.timeout_add_seconds(
            self._interval, self._tick,
        )

    def _tick(self) -> bool:
        """Save all modified buffers; returns True to keep the timer alive."""
        for tab in self._get_dirty_tabs():
            if tab.save_error:
                logger.debug(
                    "autosave: skipping %s (save_error=%s)",
                    tab.file_path, tab.save_error,
                )
                continue
            if self._save_tab(tab):
                pass  # save_tab handles monitor skip
            else:
                msg = _('Could not save "{name}". {reason}').format(
                    name=Path(tab.file_path).name,
                    reason=tab.editor.last_save_error or "")
                logger.warning("autosave: save failed for %s: %s", tab.file_path, msg)
                self._on_save_failed(tab.file_path, msg)
        return True  # Keep the GLib timeout running.

    def cancel(self) -> None:
        """Stop the autosave timer."""
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
            self._timer_id = None

    def restart(self) -> None:
        """Cancel and restart the autosave timer."""
        self.cancel()
        self.start()

    def update_interval(self, new_interval: int) -> None:
        """Change the interval and restart the timer."""
        self._interval = new_interval
        self.restart()
