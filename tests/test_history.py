"""Tests for navigation history (src/history.py)."""

import tempfile
import unittest
from pathlib import Path

from markdown_vault.history import NavHistory
from markdown_vault.path_utils import find_vault_for_path, find_vault_for_dir


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


class TestFindVaultForPath(unittest.TestCase):
    """Tests for find_vault_for_path function."""

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

    def test_exact_md_file(self):
        target = str(self._vault / "Page.md")
        result = find_vault_for_path(target, [str(self._vault)])
        self.assertIsNotNone(result)
        self.assertEqual(result, str(self._vault))

    def test_without_extension(self):
        target = str(self._vault / "Page")
        result = find_vault_for_path(target, [str(self._vault)])
        self.assertIsNotNone(result)
        self.assertEqual(result, str(self._vault))

    def test_subdirectory(self):
        target = str(self._vault / "Sub" / "Deep")
        result = find_vault_for_path(target, [str(self._vault)])
        self.assertIsNotNone(result)
        self.assertEqual(result, str(self._vault))

    def test_multiple_vaults(self):
        other = Path(self._tmp) / "other"
        other.mkdir()
        (other / "Note.md").write_text("# Note")
        target = str(self._vault / "Page")
        result = find_vault_for_path(target, [str(other), str(self._vault)])
        self.assertEqual(result, str(self._vault))

    def test_no_match(self):
        result = find_vault_for_path("/nonexistent/Nope.md", [str(self._vault)])
        self.assertIsNone(result)

    def test_empty_vault_list(self):
        result = find_vault_for_path("/some/path", [])
        self.assertIsNone(result)

    def test_prefix_not_parent(self):
        # Vault "a" should not match file "ab.md"
        other = Path(self._tmp) / "a"
        other.mkdir()
        (other / "x.md").write_text("")
        target = str(Path(self._tmp) / "ab.md")
        Path(target).write_text("")
        result = find_vault_for_path(target, [str(other)])
        self.assertIsNone(result)

    # ── find_vault_for_dir (new contract) ───────────────────────────

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


if __name__ == "__main__":
    unittest.main()