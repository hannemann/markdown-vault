"""Tests for navigation history (src/history.py)."""

import tempfile
import unittest
from pathlib import Path

from markdown_vault.core.history import NavHistory
from markdown_vault.core.path_utils import find_vault_for_dir


class TestNavHistory(unittest.TestCase):
    """Tests for NavHistory browser-style navigation."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._files = []
        for i, name in enumerate(("a.md", "b.md", "c.md", "d.md")):
            p = Path(self._tmp) / name
            p.touch()
            self._files.append(str(p))

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _path(self, idx: int) -> str:
        return self._files[idx]

    # ── push ────────────────────────────────────────────────────────

    def test_push_first(self):
        h = NavHistory()
        h.push(self._path(0))
        self.assertEqual(h.history, [self._path(0)])
        self.assertEqual(h.pos, 0)

    def test_push_multiple(self):
        h = NavHistory()
        h.push(self._path(0))
        h.push(self._path(1))
        h.push(self._path(2))
        self.assertEqual(h.history, [self._path(0), self._path(1), self._path(2)])
        self.assertEqual(h.pos, 2)

    def test_push_consecutive_duplicate_ignored(self):
        h = NavHistory()
        h.push(self._path(0))
        h.push(self._path(0))  # same as current
        self.assertEqual(h.history, [self._path(0)])
        self.assertEqual(h.pos, 0)

    def test_push_truncates_forward(self):
        h = NavHistory()
        h.push(self._path(0))
        h.push(self._path(1))
        h.push(self._path(2))
        h.back()  # pos = 1 (b)
        h.push(self._path(3))  # should truncate c
        self.assertEqual(h.history, [self._path(0), self._path(1), self._path(3)])
        self.assertEqual(h.pos, 2)

    def test_push_suppress(self):
        h = NavHistory()
        h.suppress = True
        h.push(self._path(0))
        self.assertEqual(h.history, [])
        h.suppress = False
        h.push(self._path(0))
        self.assertEqual(h.history, [self._path(0)])

    # ── back / forward ──────────────────────────────────────────────

    def test_back(self):
        h = NavHistory()
        h.push(self._path(0))
        h.push(self._path(1))
        h.push(self._path(2))
        self.assertEqual(h.back(), self._path(1))
        self.assertEqual(h.pos, 1)
        self.assertEqual(h.back(), self._path(0))
        self.assertEqual(h.pos, 0)
        self.assertIsNone(h.back())

    def test_forward(self):
        h = NavHistory()
        h.push(self._path(0))
        h.push(self._path(1))
        h.push(self._path(2))
        h.back()  # pos=1
        self.assertEqual(h.forward(), self._path(2))
        self.assertEqual(h.pos, 2)
        self.assertIsNone(h.forward())

    def test_back_skips_missing(self):
        h = NavHistory()
        missing = "/nonexistent/missing.md"
        h.push(self._path(0))
        h.push(missing)  # will be pushed but skipped on back
        h.push(self._path(1))
        # History: [a, missing, b], pos=2
        # back() goes to pos=1 (missing), doesn't exist, goes to pos=0 (a)
        self.assertEqual(h.back(), self._path(0))
        # At a, can't go back further
        self.assertIsNone(h.back())

    def test_forward_skips_missing(self):
        h = NavHistory()
        missing = "/nonexistent/missing.md"
        h.push(self._path(0))
        h.push(missing)
        h.push(self._path(1))
        h.back()  # to missing
        h.back()  # to a
        self.assertEqual(h.forward(), self._path(1))  # skips missing

    def test_back_restores_pos_on_failure(self):
        """If back() returns None, _pos should be restored to original."""
        h = NavHistory()
        missing = "/nonexistent/missing.md"
        h.push(self._path(0))
        h.push(missing)
        # pos=1 (at missing)
        result = h.back()  # should skip missing, go to a.md (pos=0)
        self.assertEqual(result, self._path(0))
        # Now at pos=0, back() should return None and pos should stay 0
        result2 = h.back()
        self.assertIsNone(result2)
        self.assertEqual(h.pos, 0)

    def test_back_restores_pos_when_all_missing(self):
        """If back() finds NO valid entries, _pos should be restored to original."""
        h = NavHistory()
        missing1 = "/nonexistent/missing1.md"
        missing2 = "/nonexistent/missing2.md"
        h.push(missing1)
        h.push(missing2)
        # pos=1 (at missing2)
        # back() goes to pos=0 (missing1) -> doesn't exist -> loop exits, returns None
        # BUG: pos is now 0 (pointing to missing1), should be restored to 1
        result = h.back()
        self.assertIsNone(result)
        self.assertEqual(h.pos, 1)  # Should be restored to original position

    def test_forward_restores_pos_on_failure(self):
        """If forward() returns None, _pos should be restored to original."""
        h = NavHistory()
        missing = "/nonexistent/missing.md"
        h.push(self._path(0))
        h.push(missing)
        h.push(self._path(1))
        h.back()  # to missing (pos=1)
        h.back()  # to a (pos=0)
        # Now forward() should skip missing and go to b (pos=2)
        result = h.forward()
        self.assertEqual(result, self._path(1))
        self.assertEqual(h.pos, 2)
        # forward() again should return None and pos should stay at 2
        result2 = h.forward()
        self.assertIsNone(result2)
        self.assertEqual(h.pos, 2)

    # ── can_go_back / can_go_forward ────────────────────────────────

    def test_can_go_back(self):
        h = NavHistory()
        self.assertFalse(h.can_go_back())
        h.push(self._path(0))
        self.assertFalse(h.can_go_back())
        h.push(self._path(1))
        self.assertTrue(h.can_go_back())

    def test_can_go_forward(self):
        h = NavHistory()
        self.assertFalse(h.can_go_forward())
        h.push(self._path(0))
        h.push(self._path(1))
        h.back()
        self.assertTrue(h.can_go_forward())

    def test_can_go_back_false_when_only_missing_behind(self):
        """R29.4: can_go_back mirrors back(), which skips missing files — so it
        must be False when every earlier entry's file is gone."""
        h = NavHistory()
        h.push("/nonexistent/gone.md")
        h.push(self._path(0))
        # History: [gone, a], pos=1. back() would skip gone and find nothing.
        self.assertFalse(h.can_go_back())
        self.assertIsNone(h.back())

    def test_can_go_forward_false_when_only_missing_ahead(self):
        h = NavHistory()
        h.push(self._path(0))
        h.push("/nonexistent/gone.md")
        h.back()  # to a (pos=0); only the missing entry lies ahead
        self.assertFalse(h.can_go_forward())

    # ── remove_path ─────────────────────────────────────────────────

    def test_remove_file(self):
        h = NavHistory()
        h.push(self._path(0))
        h.push(self._path(1))
        h.push(self._path(2))
        h.remove_path(self._path(1))
        self.assertEqual(h.history, [self._path(0), self._path(2)])
        self.assertEqual(h.pos, 1)  # was 2, removed index 1 before pos

    def test_remove_current_file(self):
        h = NavHistory()
        h.push(self._path(0))
        h.push(self._path(1))
        h.push(self._path(2))
        h.remove_path(self._path(2))
        self.assertEqual(h.history, [self._path(0), self._path(1)])
        self.assertEqual(h.pos, 1)  # clamped to end

    def test_remove_before_pos(self):
        h = NavHistory()
        h.push(self._path(0))
        h.push(self._path(1))
        h.push(self._path(2))
        h.push(self._path(3))
        h.remove_path(self._path(1))
        # pos was 3, removed one before pos -> pos = 2
        self.assertEqual(h.pos, 2)
        self.assertEqual(h.history, [self._path(0), self._path(2), self._path(3)])

    def test_remove_dir_removes_children(self):
        h = NavHistory()
        dir_path = str(Path(self._tmp) / "subdir")
        Path(dir_path).mkdir()
        child = str(Path(dir_path) / "child.md")
        Path(child).touch()
        h.push(self._path(0))
        h.push(dir_path)
        h.push(child)
        h.push(self._path(1))
        h.remove_path(dir_path, is_dir=True)
        self.assertEqual(h.history, [self._path(0), self._path(1)])
        self.assertEqual(h.pos, 1)

    def test_remove_nonexistent_is_noop(self):
        h = NavHistory()
        h.push(self._path(0))
        h.remove_path("/nonexistent.md")
        self.assertEqual(h.history, [self._path(0)])

    # ── remap_paths ─────────────────────────────────────────────────

    def test_remap_file(self):
        h = NavHistory()
        h.push(self._path(0))
        h.push(self._path(1))
        new_path = self._path(1).replace(".md", "_new.md")
        h.remap_paths(self._path(1), new_path)
        self.assertEqual(h.history, [self._path(0), new_path])
        self.assertEqual(h.pos, 1)

    def test_remap_dir(self):
        h = NavHistory()
        dir_path = str(Path(self._tmp) / "old")
        Path(dir_path).mkdir()
        child = str(Path(dir_path) / "file.md")
        Path(child).touch()
        h.push(self._path(0))
        h.push(dir_path)
        h.push(child)
        new_dir = str(Path(self._tmp) / "new")
        h.remap_paths(dir_path, new_dir)
        self.assertEqual(h.history, [self._path(0), new_dir, str(Path(new_dir) / "file.md")])

    # ── clear ───────────────────────────────────────────────────────

    def test_clear(self):
        h = NavHistory()
        h.push(self._path(0))
        h.clear()
        self.assertEqual(h.history, [])
        self.assertEqual(h.pos, -1)

    def test_current(self):
        h = NavHistory()
        self.assertIsNone(h.current)
        h.push(self._path(0))
        self.assertEqual(h.current, self._path(0))
        h.push(self._path(1))
        self.assertEqual(h.current, self._path(1))
        h.back()
        self.assertEqual(h.current, self._path(0))


class TestFindVaultForDir(unittest.TestCase):
    """Tests for find_vault_for_dir function."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._vault = Path(self._tmp) / "vault"
        self._vault.mkdir()
        (self._vault / "Page.md").write_text("# Page")
        (self._vault / "Sub").mkdir()
        (self._vault / "Sub" / "Deep.md").write_text("# Deep")

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    # ── find_vault_for_dir ────────────────────────────────────────

    def test_find_vault_for_dir_exact(self):
        result = find_vault_for_dir(str(self._vault), [str(self._vault)])
        self.assertEqual(result, str(self._vault))

    def test_find_vault_for_dir_subdirectory(self):
        subdir = self._vault / "Sub" / "Deep"
        subdir.mkdir(parents=True, exist_ok=True)
        result = find_vault_for_dir(str(subdir), [str(self._vault)])
        self.assertEqual(result, str(self._vault))

    def test_find_vault_for_dir_no_match(self):
        result = find_vault_for_dir("/nonexistent", [str(self._vault)])
        self.assertIsNone(result)

    def test_find_vault_for_dir_vault_root_file(self):
        """A file directly in vault root: parent = vault root."""
        result = find_vault_for_dir(str(self._vault), [str(self._vault)])
        self.assertEqual(result, str(self._vault))

    def test_find_vault_for_dir_multiple_vaults(self):
        other = Path(self._tmp) / "other"
        other.mkdir()
        (other / "note.md").write_text("# Note")
        result = find_vault_for_dir(str(self._vault), [str(other), str(self._vault)])
        self.assertEqual(result, str(self._vault))

    def test_find_vault_for_dir_uses_config_ssot(self):
        """Without a vault list, roots come from the config (SSOT)."""
        import markdown_vault.core.config as _cfg
        _cfg._vaults_cache = [{"name": "vault", "path": str(self._vault)}]
        try:
            result = find_vault_for_dir(str(self._vault / "Sub"))
            self.assertEqual(result, str(self._vault))
            self.assertIsNone(find_vault_for_dir("/nonexistent"))
        finally:
            _cfg._vaults_cache = None


class TestNavHistoryState(unittest.TestCase):
    """to_state / load_state for session persistence."""

    def test_round_trip(self):
        h = NavHistory()
        for p in ("/v/a.md", "/v/b.md", "/v/c.md"):
            h.push(p)
        state = h.to_state()
        # Entries serialise as dicts keyed by "path"; a position-less entry
        # carries only its path (no scroll/cursor keys) so the file stays lean.
        self.assertEqual(state, {"history": [{"path": "/v/a.md"},
                                             {"path": "/v/b.md"},
                                             {"path": "/v/c.md"}],
                                 "pos": 2})
        h2 = NavHistory()
        h2.load_state(state, exists=lambda p: True)
        self.assertEqual(h2.history, ["/v/a.md", "/v/b.md", "/v/c.md"])
        self.assertEqual(h2.pos, 2)

    def test_load_prunes_missing_and_keeps_position(self):
        # b.md is gone; pos was at c (index 2) → should land on c (now index 1).
        state = {"history": ["/v/a.md", "/v/b.md", "/v/c.md"], "pos": 2}
        h = NavHistory()
        h.load_state(state, exists=lambda p: p != "/v/b.md")
        self.assertEqual(h.history, ["/v/a.md", "/v/c.md"])
        self.assertEqual(h.current, "/v/c.md")

    def test_load_all_missing_is_empty(self):
        h = NavHistory()
        h.load_state({"history": ["/x.md"], "pos": 0}, exists=lambda p: False)
        self.assertEqual(h.history, [])
        self.assertEqual(h.pos, -1)

    def test_load_non_int_pos_does_not_crash(self):
        """A persisted null/garbage pos must fall back, not blow up restore."""
        h = NavHistory()
        h.load_state({"history": ["/a.md", "/b.md"], "pos": None},
                     exists=lambda p: True)
        self.assertEqual(h.history, ["/a.md", "/b.md"])
        self.assertEqual(h.pos, 1)  # fell back to last entry

    def test_push_caps_to_max_keeping_position(self):
        h = NavHistory()
        h.MAX_HISTORY = 3
        for i in range(5):
            h.push(f"/p{i}.md")
        self.assertEqual(h.history, ["/p2.md", "/p3.md", "/p4.md"])
        self.assertEqual(h.current, "/p4.md")

    def test_load_caps_to_max(self):
        h = NavHistory()
        h.MAX_HISTORY = 2
        h.load_state({"history": ["/a.md", "/b.md", "/c.md"], "pos": 2},
                     exists=lambda p: True)
        self.assertEqual(h.history, ["/b.md", "/c.md"])
        self.assertEqual(h.current, "/c.md")


class TestNavHistoryPositions(unittest.TestCase):
    """Position-carrying entries — feature: the history restores where the
    reader was, not just which file. The public path API (``history``,
    ``current``, ``back``/``forward``) stays string-valued; positions ride
    alongside on ``current_entry`` / ``entries``."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._a = str(Path(self._tmp) / "a.md")
        self._b = str(Path(self._tmp) / "b.md")
        Path(self._a).touch()
        Path(self._b).touch()

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_push_defaults_to_no_position(self):
        h = NavHistory()
        h.push("/v/a.md")
        e = h.current_entry
        self.assertEqual(e.path, "/v/a.md")
        self.assertIsNone(e.editor_scroll)
        self.assertIsNone(e.editor_cursor)
        self.assertIsNone(e.preview_scroll)
        self.assertFalse(e.has_position())

    def test_update_current_writes_only_given_fields(self):
        h = NavHistory()
        h.push("/v/a.md")
        h.update_current(editor_scroll=120.0, editor_cursor=42)
        e = h.current_entry
        self.assertEqual(e.editor_scroll, 120.0)
        self.assertEqual(e.editor_cursor, 42)
        self.assertIsNone(e.preview_scroll)  # untouched field stays None

    def test_update_current_is_noop_without_current(self):
        h = NavHistory()
        h.update_current(editor_scroll=1.0)  # empty history → no crash
        self.assertIsNone(h.current_entry)

    def test_position_travels_with_back_and_forward(self):
        h = NavHistory()
        h.push(self._a)
        h.update_current(preview_scroll=300.0)
        h.push(self._b)
        self.assertEqual(h.back(), self._a)
        self.assertEqual(h.current_entry.preview_scroll, 300.0)
        self.assertEqual(h.forward(), self._b)
        self.assertIsNone(h.current_entry.preview_scroll)

    def test_duplicate_same_path_same_position_collapses(self):
        h = NavHistory()
        h.push("/v/a.md", editor_scroll=100.0)
        h.push("/v/a.md", editor_scroll=100.0)
        self.assertEqual(h.history, ["/v/a.md"])

    def test_duplicate_same_path_different_position_kept(self):
        # An in-page jump has the same file but a different position — it must
        # not be swallowed by the classic path-only dedupe.
        h = NavHistory()
        h.push("/v/a.md", editor_scroll=100.0)
        h.push("/v/a.md", editor_scroll=500.0)
        self.assertEqual(h.history, ["/v/a.md", "/v/a.md"])
        self.assertEqual(h.entries[0].editor_scroll, 100.0)
        self.assertEqual(h.entries[1].editor_scroll, 500.0)

    def test_push_with_a_position_onto_a_position_less_entry_is_kept(self):
        # The ordinary case for an in-page jump: the note was just opened, so its
        # entry has no position yet, and the jump pushes one. It must become its
        # own entry — this is what lets an anchor jump be a plain history push
        # (the merge-in-page follow-up rests on it), and it was the one row of the
        # dedupe matrix without a guard.
        h = NavHistory()
        h.push("/v/a.md")
        h.push("/v/a.md", preview_scroll=250.0)
        self.assertEqual(h.history, ["/v/a.md", "/v/a.md"])
        self.assertIsNone(h.entries[0].preview_scroll)
        self.assertEqual(h.entries[1].preview_scroll, 250.0)

    def test_positionless_push_of_same_path_still_collapses(self):
        # Merely re-activating the current note (no explicit position) is not a
        # new entry, and must not wipe the position already recorded there.
        h = NavHistory()
        h.push("/v/a.md", editor_scroll=100.0)
        h.push("/v/a.md")
        self.assertEqual(h.history, ["/v/a.md"])
        self.assertEqual(h.current_entry.editor_scroll, 100.0)


class TestNavHistoryPositionState(unittest.TestCase):
    """Position round-trips through session state, and legacy string entries
    migrate rather than vanishing (which would look like a silent empty
    history after the upgrade)."""

    def test_round_trip_carries_position(self):
        h = NavHistory()
        h.push("/v/a.md", editor_scroll=10.0, editor_cursor=3)
        h.push("/v/b.md", preview_scroll=250.0)
        h2 = NavHistory()
        h2.load_state(h.to_state(), exists=lambda p: True)
        self.assertEqual(h2.history, ["/v/a.md", "/v/b.md"])
        self.assertEqual(h2.entries[0].editor_scroll, 10.0)
        self.assertEqual(h2.entries[0].editor_cursor, 3)
        self.assertEqual(h2.entries[1].preview_scroll, 250.0)

    def test_load_migrates_legacy_string_entries(self):
        state = {"history": ["/v/a.md", "/v/b.md"], "pos": 1}
        h = NavHistory()
        h.load_state(state, exists=lambda p: True)
        self.assertEqual(h.history, ["/v/a.md", "/v/b.md"])
        self.assertEqual(h.pos, 1)
        self.assertFalse(h.current_entry.has_position())

    def test_load_drops_garbage_entries(self):
        state = {"history": ["/v/a.md", 123, {"nopath": 1}, {"path": "/v/b.md"}],
                 "pos": 3}
        h = NavHistory()
        h.load_state(state, exists=lambda p: True)
        self.assertEqual(h.history, ["/v/a.md", "/v/b.md"])


if __name__ == "__main__":
    unittest.main()