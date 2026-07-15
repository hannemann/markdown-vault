"""Navigation history management (browser-style back/forward).

Provides a ``NavHistory`` class that manages a list of file paths with
a current position index, supporting push, back, forward, and removal
operations with correct position adjustment.
"""

import os
from pathlib import Path


class NavHistory:
    """Browser-style navigation history with position tracking.

    The history maintains a list of file paths and a current position
    index. Push adds entries and truncates forward history. Back/forward
    move the position and skip missing files. Remove/remap operations
    adjust the position correctly.
    """

    def __init__(self) -> None:
        self._history: list[str] = []
        self._pos: int = -1
        self._suppress: bool = False

    @property
    def pos(self) -> int:
        """Current position index (-1 means no current entry)."""
        return self._pos

    @property
    def history(self) -> list[str]:
        """Return a copy of the history list."""
        return list(self._history)

    @property
    def suppress(self) -> bool:
        return self._suppress

    @suppress.setter
    def suppress(self, value: bool) -> None:
        self._suppress = value

    @property
    def current(self) -> str | None:
        """The path at the current position, or ``None`` if empty."""
        if 0 <= self._pos < len(self._history):
            return self._history[self._pos]
        return None

    def push(self, file_path: str) -> None:
        """Append *file_path* to the history.

        Consecutive duplicates are collapsed and any forward history
        is discarded, matching standard browser behaviour.
        """
        if self._suppress:
            return
        # Don't push if we're already at this position.
        if self._pos >= 0 and self._history[self._pos] == file_path:
            return
        # Truncate forward history.
        self._history = self._history[: self._pos + 1]
        self._history.append(file_path)
        self._pos = len(self._history) - 1

    def back(self) -> str | None:
        """Navigate to the previous entry, skipping missing files.

        Returns the file path if a valid previous entry was found,
        otherwise ``None``.
        """
        while self._pos > 0:
            self._pos -= 1
            file_path = self._history[self._pos]
            if Path(file_path).exists():
                return file_path
        return None

    def forward(self) -> str | None:
        """Navigate to the next entry, skipping missing files.

        Returns the file path if a valid next entry was found,
        otherwise ``None``.
        """
        while self._pos < len(self._history) - 1:
            self._pos += 1
            file_path = self._history[self._pos]
            if Path(file_path).exists():
                return file_path
        return None

    def remove_path(self, path: str, is_dir: bool = False) -> None:
        """Remove *path* from history and adjust position.

        If *is_dir* is true, also remove any paths that are inside
        the directory tree.
        """
        old_history = self._history
        self._history = [
            p for p in old_history
            if p != path and not (is_dir and p.startswith(path + os.sep))
        ]
        # Count how many removed entries were before the current position.
        removed_before = sum(
            1 for p in old_history[:self._pos]
            if p == path or (is_dir and p.startswith(path + os.sep))
        )
        self._pos = max(0, self._pos - removed_before)
        # Clamp position to valid range.
        if self._pos >= len(self._history):
            self._pos = len(self._history) - 1

    def remap_paths(self, old_path: str, new_path: str) -> None:
        """Rewrite history entries starting with *old_path* to *new_path*.

        Used when a file or directory is renamed.
        """
        self._history = [
            new_path + p[len(old_path):]
            if p == old_path or p.startswith(old_path + os.sep)
            else p
            for p in self._history
        ]
        # _pos doesn't change during rename — entries are replaced, not removed.

    def clear(self) -> None:
        """Reset history to empty state."""
        self._history = []
        self._pos = -1

    def can_go_back(self) -> bool:
        """Whether there are valid previous entries."""
        return self._pos > 0

    def can_go_forward(self) -> bool:
        """Whether there are valid next entries."""
        return self._pos < len(self._history) - 1