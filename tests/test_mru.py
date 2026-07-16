"""Tests for MRU tab management (src/mru.py)."""

import tempfile
import unittest
from pathlib import Path

from src.mru import MRUManager, MRUSwitcher


class TestMRUManager(unittest.TestCase):
    """Tests for the MRUManager business logic."""

    def setUp(self):
        self.mru = MRUManager()
        self._tmp = tempfile.mkdtemp()
        self._files = []
        for name in ("a.md", "b.md", "c.md"):
            p = Path(self._tmp) / name
            p.touch()
            self._files.append(str(p))

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _path(self, idx: int) -> str:
        return self._files[idx]

    # ── push ────────────────────────────────────────────────────────

    def test_push_adds_to_list(self):
        self.mru.push(self._path(0))
        self.assertEqual(self.mru.tabs, [self._path(0)])

    def test_push_moves_to_front(self):
        self.mru.push(self._path(0))
        self.mru.push(self._path(1))
        self.mru.push(self._path(2))
        self.assertEqual(self.mru.tabs, [
            self._path(2), self._path(1), self._path(0),
        ])

    def test_push_moves_existing_to_front(self):
        self.mru.push(self._path(0))
        self.mru.push(self._path(1))
        self.mru.push(self._path(0))
        self.assertEqual(self.mru.tabs, [self._path(0), self._path(1)])

    def test_push_resets_position(self):
        self.mru.push(self._path(0))
        self.mru.push(self._path(1))
        self.mru.push(self._path(2))
        self.mru.next()
        self.mru.push(self._path(1))
        self.assertEqual(self.mru.pos, 0)

    def test_push_returns_copy(self):
        self.mru.push(self._path(0))
        tabs = self.mru.tabs
        tabs.append("/x.md")
        self.assertEqual(self.mru.tabs, [self._path(0)])

    # ── remove ──────────────────────────────────────────────────────

    def test_remove_existing(self):
        self.mru.push(self._path(0))
        self.mru.push(self._path(1))
        self.mru.remove(self._path(0))
        self.assertEqual(self.mru.tabs, [self._path(1)])

    def test_remove_nonexistent_is_noop(self):
        self.mru.push(self._path(0))
        self.mru.remove("/nonexistent.md")
        self.assertEqual(self.mru.tabs, [self._path(0)])

    def test_remove_adjusts_position(self):
        self.mru.push(self._path(0))
        self.mru.push(self._path(1))
        self.mru.push(self._path(2))
        # MRU = [c, b, a], pos=0
        self.mru.next()  # pos=1 (b)
        self.mru.remove(self._path(1))
        # MRU = [c, a], pos was 1, now clamped to 1 (len=2, max=1)
        self.assertEqual(self.mru.tabs, [self._path(2), self._path(0)])
        self.assertEqual(self.mru.pos, 1)

    def test_remove_clamps_pos_to_zero(self):
        self.mru.push(self._path(0))
        self.mru.push(self._path(1))
        # pos=0, remove MRU[0] → pos clamped to 0
        self.mru.remove(self._path(1))
        self.assertEqual(self.mru.pos, 0)

    # ── next / prev ────────────────────────────────────────────────

    def test_next_returns_none_when_empty(self):
        self.assertIsNone(self.mru.next())

    def test_next_returns_none_with_one_tab(self):
        self.mru.push(self._path(0))
        self.assertIsNone(self.mru.next())

    def test_next_advances_position(self):
        self.mru.push(self._path(0))
        self.mru.push(self._path(1))
        self.mru.push(self._path(2))
        result = self.mru.next()
        self.assertEqual(result, self._path(1))
        self.assertEqual(self.mru.pos, 1)

    def test_next_returns_none_at_end(self):
        self.mru.push(self._path(0))
        self.mru.push(self._path(1))
        self.mru.next()  # pos=1
        result = self.mru.next()  # already at end → None
        self.assertIsNone(result)
        self.assertEqual(self.mru.pos, 1)

    def test_prev_returns_none_at_start(self):
        self.mru.push(self._path(0))
        self.mru.push(self._path(1))
        self.assertIsNone(self.mru.prev())

    def test_prev_goes_back(self):
        self.mru.push(self._path(0))
        self.mru.push(self._path(1))
        self.mru.push(self._path(2))
        self.mru.next()  # pos=1
        result = self.mru.prev()
        self.assertEqual(result, self._path(2))
        self.assertEqual(self.mru.pos, 0)

    def test_next_skips_missing_files(self):
        self.mru.push(self._path(0))
        self.mru.push("/nonexistent_xyz.md")
        self.mru.push(self._path(2))
        # MRU = [c, nonexistent, a], pos=0
        result = self.mru.next()
        # Should skip nonexistent at pos=1, land on a at pos=2
        self.assertEqual(result, self._path(0))
        self.assertEqual(self.mru.pos, 2)

    def test_prev_skips_missing_files(self):
        self.mru.push(self._path(2))
        self.mru.push("/nonexistent_xyz.md")
        self.mru.push(self._path(0))
        # MRU = [a, nonexistent, c], pos=0
        self.mru.next()  # pos=1 = nonexistent → skipped → pos=2 (c)
        # Now MRU = [a, nonexistent, c], pos=2
        result = self.mru.prev()
        # Should skip nonexistent at pos=1, land on a at pos=0
        self.assertEqual(result, self._path(0))
        self.assertEqual(self.mru.pos, 0)

    def test_next_all_missing_returns_none(self):
        self.mru.push("/nonexistent1.md")
        self.mru.push("/nonexistent2.md")
        # Both files missing, next should return None
        self.assertIsNone(self.mru.next())

    def test_prev_all_missing_returns_none(self):
        self.mru.push("/nonexistent1.md")
        self.mru.push("/nonexistent2.md")
        self.mru.next()  # tries to go forward, all missing
        self.mru._mru_pos = 1  # force position
        result = self.mru.prev()
        self.assertIsNone(result)

    # ── clear ─────────────────────────────────────────────────────

    def test_clear_empties_list(self):
        self.mru.push(self._path(0))
        self.mru.push(self._path(1))
        self.mru.clear()
        self.assertEqual(self.mru.tabs, [])

    def test_clear_resets_position(self):
        self.mru.push(self._path(0))
        self.mru.push(self._path(1))
        self.mru.next()
        self.mru.clear()
        self.assertEqual(self.mru.pos, 0)

    def test_clear_on_empty_is_noop(self):
        self.mru.clear()
        self.assertEqual(self.mru.tabs, [])

    def test_push_after_clear(self):
        self.mru.push(self._path(0))
        self.mru.clear()
        self.mru.push(self._path(1))
        self.assertEqual(self.mru.tabs, [self._path(1)])

    # ── tabs property ──────────────────────────────────────────────

    def test_tabs_returns_copy(self):
        self.mru.push(self._path(0))
        self.mru.push(self._path(1))
        result = self.mru.tabs
        result.append("/x.md")
        self.assertEqual(self.mru.tabs, [self._path(1), self._path(0)])

    def test_tabs_order(self):
        self.mru.push(self._path(0))
        self.mru.push(self._path(1))
        self.mru.push(self._path(2))
        self.assertEqual(self.mru.tabs, [
            self._path(2), self._path(1), self._path(0),
        ])

    def test_tabs_empty(self):
        self.assertEqual(self.mru.tabs, [])

    # ── rename ───────────────────────────────────────────────────────

    def test_rename_preserves_index_and_pos(self):
        self.mru.push(self._path(0))
        self.mru.push(self._path(1))
        self.mru.push(self._path(2))
        # MRU = [c, b, a], pos=0
        self.mru.next()  # pos=1 (b)
        new_path = "/tmp/z.md"
        self.mru.rename(self._path(1), new_path)
        self.assertEqual(self.mru.tabs, [self._path(2), new_path, self._path(0)])
        self.assertEqual(self.mru.pos, 1)

    def test_rename_current_preserves_pos_zero(self):
        self.mru.push(self._path(0))
        self.mru.push(self._path(1))
        # MRU = [b, a], pos=0 (b)
        new_path = "/tmp/z.md"
        self.mru.rename(self._path(1), new_path)
        self.assertEqual(self.mru.tabs, [new_path, self._path(0)])
        self.assertEqual(self.mru.pos, 0)

    def test_rename_nonexistent_is_noop(self):
        self.mru.push(self._path(0))
        self.mru.rename("/nonexistent.md", "/tmp/z.md")
        self.assertEqual(self.mru.tabs, [self._path(0)])


class TestMRUSwitcherStructure(unittest.TestCase):
    """Structural tests for the MRUSwitcher GTK widget."""

    def _source(self) -> str:
        return (Path(__file__).resolve().parent.parent / "src" / "mru.py").read_text(
            encoding="utf-8"
        )

    def test_module_has_mru_switcher_class(self):
        self.assertIn("class MRUSwitcher", self._source())

    def test_has_is_open_classmethod(self):
        self.assertIn("def is_open(cls)", self._source())

    def test_has_cycle_existing_classmethod(self):
        self.assertIn("def cycle_existing(cls", self._source())

    def test_has_instance_class_attribute(self):
        self.assertIn("_instance", self._source())

    def test_has_commit_method(self):
        self.assertIn("def _commit(self)", self._source())

    def test_has_cancel_method(self):
        self.assertIn("def _cancel(self)", self._source())

    def test_has_key_pressed_handler(self):
        self.assertIn("def _on_key_pressed(self", self._source())

    def test_has_key_released_handler(self):
        self.assertIn("def _on_key_released(self", self._source())

    def test_has_close_request_handler(self):
        self.assertIn("def _on_close_request(self", self._source())

    def test_singleton_resets_on_close(self):
        self.assertIn("MRUSwitcher._instance = None", self._source())

    def test_ctrl_release_commits(self):
        src = self._source()
        self.assertIn("KEY_Control_L", src)
        self.assertIn("KEY_Control_R", src)

    def test_uses_open_file_callback(self):
        self.assertIn("self._open_file(target)", self._source())

    def test_no_initial_direction_param(self):
        src = self._source()
        # Verify __init__ does not accept initial_direction
        self.assertNotIn("initial_direction", src)

    def test_no_set_hide_on_close(self):
        self.assertNotIn("set_hide_on_close", self._source())

    def test_destroys_on_close(self):
        self.assertIn("self.destroy()", self._source())


class TestMRUManagerSingleton(unittest.TestCase):
    """Test MRUSwitcher._instance class-level singleton tracking."""

    def setUp(self):
        self._saved = MRUSwitcher._instance

    def tearDown(self):
        MRUSwitcher._instance = self._saved

    def test_is_open_false_initially(self):
        MRUSwitcher._instance = None
        self.assertFalse(MRUSwitcher.is_open())

    def test_is_open_true_when_set(self):
        MRUSwitcher._instance = "fake"
        self.assertTrue(MRUSwitcher.is_open())

    def test_cycle_existing_noop_when_closed(self):
        MRUSwitcher._instance = None
        MRUSwitcher.cycle_existing(+1)  # should not raise

    def test_cycle_existing_calls_accelerator(self):
        class FakeSwitcher:
            def __init__(self):
                self.last_dir = None
            def cycle_from_accelerator(self, direction):
                self.last_dir = direction
        fake = FakeSwitcher()
        MRUSwitcher._instance = fake
        MRUSwitcher.cycle_existing(-1)
        self.assertEqual(fake.last_dir, -1)


if __name__ == "__main__":
    unittest.main()
