"""Navigation history management (browser-style back/forward).

Provides a ``NavHistory`` class that manages a list of :class:`NavEntry`
records — a file path plus the reader's position in it — with a current
position index, supporting push, back, forward, and removal operations with
correct position adjustment.

The public *path* API stays string-valued: :attr:`NavHistory.history` yields
paths, and :meth:`~NavHistory.back`/:meth:`~NavHistory.forward`/
:attr:`~NavHistory.current` return the path to open. The stored *position*
(editor scroll + caret, preview scroll) rides alongside on
:attr:`~NavHistory.current_entry` / :attr:`~NavHistory.entries`, so a caller
that only navigates never has to know positions exist.
"""

import logging
import os
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# The position fields an entry carries, in serialisation order. Kept as data so
# to_dict/from_state and equality never drift from the dataclass definition.
_POSITION_FIELDS = ("editor_scroll", "editor_cursor", "preview_scroll")


@dataclass
class NavEntry:
    """One history entry: a note path plus where the reader was in it.

    Positions are per view and optional (``None`` = unknown): ``editor_scroll``
    and ``editor_cursor`` for the editor, ``preview_scroll`` for the preview.
    Edit mode fills the editor pair, preview mode the preview value, split mode
    all three; a fresh entry (just navigated to, not yet scrolled) carries none.
    """

    path: str
    editor_scroll: float | None = None
    editor_cursor: int | None = None
    preview_scroll: float | None = None

    def has_position(self) -> bool:
        """True if any position field is set — i.e. the entry knows where the
        reader was, not merely which file."""
        return any(getattr(self, f) is not None for f in _POSITION_FIELDS)

    def same_position_as(self, other: "NavEntry") -> bool:
        """Whether *other* points at the same spot (all position fields equal)."""
        return all(getattr(self, f) == getattr(other, f) for f in _POSITION_FIELDS)

    def to_dict(self) -> dict:
        """Serialise to a JSON-friendly dict — ``path`` plus only the position
        fields that are set, so a position-less entry stays a bare ``{"path": …}``."""
        d: dict = {"path": self.path}
        for f in _POSITION_FIELDS:
            v = getattr(self, f)
            if v is not None:
                d[f] = v
        return d

    @classmethod
    def from_state(cls, raw) -> "NavEntry | None":
        """Rebuild an entry from persisted state, tolerating both forms:

        - a plain path string (legacy sessions, pre-position) → a position-less
          entry, so an upgrade does not silently drop the whole history;
        - a dict with a ``path`` and optional position fields (current form).

        Anything else (a non-string path, a dict without a usable path) returns
        ``None`` so the caller can drop it.
        """
        if isinstance(raw, str):
            return cls(raw) if raw else None
        if isinstance(raw, dict):
            path = raw.get("path")
            if not isinstance(path, str) or not path:
                return None
            kwargs = {}
            for f in _POSITION_FIELDS:
                v = raw.get(f)
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    kwargs[f] = v
            return cls(path, **kwargs)
        return None


class NavHistory:
    """Browser-style navigation history with position tracking.

    The history maintains a list of :class:`NavEntry` records and a current
    position index. Push adds entries and truncates forward history. Back/forward
    move the position and skip missing files. Remove/remap operations adjust the
    position correctly.
    """

    # Cap the (global, persistent) history so it can't grow without bound.
    MAX_HISTORY = 500

    def __init__(self) -> None:
        self._history: list[NavEntry] = []
        self._pos: int = -1
        self._suppress: bool = False

    @property
    def pos(self) -> int:
        """Current position index (-1 means no current entry)."""
        return self._pos

    @property
    def history(self) -> list[str]:
        """The paths in order — the stable, string-valued view of the history."""
        return [e.path for e in self._history]

    @property
    def entries(self) -> list[NavEntry]:
        """A copy of the full entry list, positions included."""
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
        entry = self.current_entry
        return entry.path if entry is not None else None

    @property
    def current_entry(self) -> NavEntry | None:
        """The entry at the current position (path + position), or ``None``."""
        if 0 <= self._pos < len(self._history):
            return self._history[self._pos]
        return None

    def push(self, file_path: str, *, editor_scroll: float | None = None,
             editor_cursor: int | None = None,
             preview_scroll: float | None = None) -> None:
        """Append *file_path* to the history, optionally with a position.

        Any forward history is discarded (standard browser behaviour). A push
        that lands on the same path as the current entry collapses into it —
        *unless* it carries an explicit, differing position: an in-page jump is
        the same file at a new spot and must stay its own entry. A position-less
        re-push of the current path is a no-op that leaves the recorded position
        intact (merely re-activating the note is not "moved to the top").
        """
        if self._suppress:
            return
        new = NavEntry(file_path, editor_scroll=editor_scroll,
                       editor_cursor=editor_cursor, preview_scroll=preview_scroll)
        current = self.current_entry
        if current is not None and current.path == file_path:
            if not new.has_position() or new.same_position_as(current):
                return
        # Truncate forward history.
        self._history = self._history[: self._pos + 1]
        self._history.append(new)
        self._pos = len(self._history) - 1
        self._enforce_cap()

    def update_current(self, *, editor_scroll: float | None = None,
                       editor_cursor: int | None = None,
                       preview_scroll: float | None = None) -> None:
        """Write the reader's live position into the *current* entry.

        Only the fields passed as non-``None`` are updated; the rest keep their
        stored value. This is how the position is recorded continuously while a
        note is on screen, so it is already there — no async query — when the
        note is left. A no-op when the history is empty.
        """
        entry = self.current_entry
        if entry is None:
            return
        if editor_scroll is not None:
            entry.editor_scroll = editor_scroll
        if editor_cursor is not None:
            entry.editor_cursor = editor_cursor
        if preview_scroll is not None:
            entry.preview_scroll = preview_scroll

    def _enforce_cap(self) -> None:
        """Drop oldest entries beyond MAX_HISTORY, keeping *pos* on its entry."""
        overflow = len(self._history) - self.MAX_HISTORY
        if overflow > 0:
            self._history = self._history[overflow:]
            self._pos = max(-1, self._pos - overflow)

    def back(self) -> str | None:
        """Navigate to the previous entry, skipping missing files.

        Returns the file path if a valid previous entry was found,
        otherwise ``None``.
        """
        original_pos = self._pos
        while self._pos > 0:
            self._pos -= 1
            file_path = self._history[self._pos].path
            if Path(file_path).exists():
                return file_path
        self._pos = original_pos
        return None

    def forward(self) -> str | None:
        """Navigate to the next entry, skipping missing files.

        Returns the file path if a valid next entry was found,
        otherwise ``None``.
        """
        original_pos = self._pos
        while self._pos < len(self._history) - 1:
            self._pos += 1
            file_path = self._history[self._pos].path
            if Path(file_path).exists():
                return file_path
        self._pos = original_pos
        return None

    def remove_path(self, path: str, is_dir: bool = False) -> None:
        """Remove *path* from history and adjust position.

        If *is_dir* is true, also remove any paths that are inside
        the directory tree.
        """
        def _matches(p: str) -> bool:
            return p == path or (is_dir and p.startswith(path + os.sep))

        old_history = self._history
        self._history = [e for e in old_history if not _matches(e.path)]
        # Count how many removed entries were before the current position.
        removed_before = sum(1 for e in old_history[:self._pos] if _matches(e.path))
        self._pos = max(0, self._pos - removed_before)
        # Clamp position to valid range.
        if self._pos >= len(self._history):
            self._pos = len(self._history) - 1

    def remap_paths(self, old_path: str, new_path: str) -> None:
        """Rewrite history entries starting with *old_path* to *new_path*.

        Used when a file or directory is renamed. Positions are preserved.
        """
        for e in self._history:
            if e.path == old_path or e.path.startswith(old_path + os.sep):
                e.path = new_path + e.path[len(old_path):]
        # _pos doesn't change during rename — entries are replaced, not removed.

    def clear(self) -> None:
        """Reset history to empty state."""
        self._history = []
        self._pos = -1

    def to_state(self) -> dict:
        """Serialise for session persistence."""
        return {"history": [e.to_dict() for e in self._history], "pos": self._pos}

    def load_state(self, state: dict, exists=None) -> None:
        """Restore from :meth:`to_state`, dropping entries whose file no longer
        exists and keeping the position pointing at the same surviving entry.

        Accepts both the current dict form and legacy plain-string entries
        (migrated to position-less entries); malformed entries are dropped.

        *exists* is injectable for tests; defaults to real filesystem checks.
        """
        exists = exists or (lambda p: Path(p).exists())
        raw = [NavEntry.from_state(x) for x in (state.get("history") or [])]
        raw = [e for e in raw if e is not None]
        pos = state.get("pos", len(raw) - 1)
        if not isinstance(pos, int):  # a persisted null/garbage must not crash restore
            pos = len(raw) - 1
        kept: list[NavEntry] = []
        removed_before = 0
        for i, e in enumerate(raw):
            if exists(e.path):
                kept.append(e)
            elif i <= pos:
                removed_before += 1
        self._history = kept
        self._pos = max(0, min(len(kept) - 1, pos - removed_before)) if kept else -1
        self._enforce_cap()  # bound an old, pre-cap persisted history too

    def can_go_back(self) -> bool:
        """Whether a previous entry with an existing file is reachable.

        Mirrors :meth:`back`, which skips entries whose file is gone — so the
        nav button's dimming reflects what the button will actually do.
        """
        return any(Path(self._history[i].path).exists()
                   for i in range(self._pos - 1, -1, -1))

    def can_go_forward(self) -> bool:
        """Whether a next entry with an existing file is reachable (see
        :meth:`can_go_back`)."""
        return any(Path(self._history[i].path).exists()
                   for i in range(self._pos + 1, len(self._history)))
