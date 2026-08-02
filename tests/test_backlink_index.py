"""Tests for markdown_vault.backlink_index — incremental backlink index."""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import markdown_vault.config as _cfg
from markdown_vault.backlink_index import BacklinkIndex, scan_vaults


class TestScanVaults(unittest.TestCase):
    """Tests for the pure scan_vaults() function (R5.3 off-thread build)."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._vault = Path(self._tmp) / "vault"
        self._vault.mkdir()
        _cfg._vaults_cache = [{"name": "vault", "path": str(self._vault)}]

    def tearDown(self):
        _cfg._vaults_cache = None
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_scan_vaults_returns_backlink_maps(self):
        (self._vault / "Page.md").write_text("# Page\n")
        (self._vault / "Note.md").write_text("See [[Page]].\n")
        target_to_sources, source_to_targets = scan_vaults(
            [{"name": "vault", "path": str(self._vault)}]
        )
        self.assertIn(
            str(self._vault / "Note.md"),
            next(iter(target_to_sources.values())),
        )
        self.assertEqual(
            len(source_to_targets[str(self._vault / "Note.md")]), 1,
        )

    def test_scan_vaults_returns_fresh_dicts(self):
        t1, s1 = scan_vaults([{"name": "vault", "path": str(self._vault)}])
        t2, s2 = scan_vaults([{"name": "vault", "path": str(self._vault)}])
        self.assertIsNot(t1, t2)
        self.assertIsNot(s1, s2)

    def test_scan_vaults_empty_vault(self):
        target_to_sources, source_to_targets = scan_vaults(
            [{"name": "vault", "path": str(self._vault)}]
        )
        self.assertEqual(target_to_sources, {})
        self.assertEqual(source_to_targets, {})

    def test_build_matches_scan_vaults(self):
        (self._vault / "Page.md").write_text("# Page\n")
        (self._vault / "Note.md").write_text("See [[Page]].\n")
        target_to_sources, source_to_targets = scan_vaults(
            [{"name": "vault", "path": str(self._vault)}]
        )
        idx = BacklinkIndex()
        idx.build([{"name": "vault", "path": str(self._vault)}])
        self.assertEqual(idx._target_to_sources, target_to_sources)
        self.assertEqual(idx._source_to_targets, source_to_targets)

    def test_set_index_swaps_maps(self):
        idx = BacklinkIndex()
        idx.set_index({}, {})
        t, s = scan_vaults([{"name": "vault", "path": str(self._vault)}])
        idx.set_index(t, s)
        self.assertIs(idx._target_to_sources, t)
        self.assertIs(idx._source_to_targets, s)


class TestApplyBacklinkBuild(unittest.TestCase):
    """R5.3/R16: main-thread swap callback for the async backlink scan."""

    def _make_fake_window(self, **overrides):
        import unittest.mock
        import markdown_vault.app_window as aw

        class FakeWindow:
            def __init__(self):
                self._backlink_index = BacklinkIndex()
                self._build_generation = 1
                self._rebuild_timeout = None
                self._dump_debug = unittest.mock.Mock()
                self._sidebar = unittest.mock.Mock()
                self._schedule_backlink_build = unittest.mock.Mock()
                self._file_index = unittest.mock.Mock()
                self._vault_tree = unittest.mock.Mock()
                self._vault_tree._vaults = []

            _apply_backlink_build = aw.MainWindow._apply_backlink_build
            _coalesce_backlink_rebuild = aw.MainWindow._coalesce_backlink_rebuild
            _do_backlink_rebuild = aw.MainWindow._do_backlink_rebuild
            _cancel_backlink_rebuild = aw.MainWindow._cancel_backlink_rebuild

        win = FakeWindow()
        for key, value in overrides.items():
            setattr(win, key, value)
        win._get_active_tab_info = unittest.mock.Mock(return_value=("/vault/file.md", ""))
        return win

    def test_apply_backlink_build_swaps_index(self):
        win = self._make_fake_window()
        target_to_sources = {"vault:VaultA?path=Page": {"/tmp/VaultA/Note.md"}}
        source_to_targets = {"/tmp/VaultA/Note.md": {"vault:VaultA?path=Page"}}
        result = win._apply_backlink_build(1, 0, target_to_sources, source_to_targets)
        self.assertFalse(result)
        self.assertIs(win._backlink_index._target_to_sources, target_to_sources)
        self.assertIs(win._backlink_index._source_to_targets, source_to_targets)
        win._dump_debug.assert_called_once_with(["backlink_index"])

    def test_apply_backlink_build_discards_stale_generation(self):
        """R16.2: a worker result from an older generation must be discarded."""
        win = self._make_fake_window()
        win._build_generation = 2
        result = win._apply_backlink_build(
            1, 0, {"k": {"s"}}, {"s": {"k"}},
        )
        self.assertFalse(result)
        self.assertEqual(win._backlink_index._target_to_sources, {})
        self.assertEqual(win._backlink_index._source_to_targets, {})
        win._dump_debug.assert_not_called()
        win._schedule_backlink_build.assert_not_called()

    def test_apply_backlink_build_reschedules_on_mutation(self):
        """R16.1/R17.1: incremental mutations during the build window must not
        be lost — the stale snapshot is discarded and a fresh rescan is
        coalesced (never a synchronous reschedule, which could livelock)."""
        import unittest.mock

        win = self._make_fake_window(
            _coalesce_backlink_rebuild=unittest.mock.Mock(),
        )
        win._backlink_index._mutation_seq = 6
        result = win._apply_backlink_build(
            1, 5, {"k": {"s"}}, {"s": {"k"}},
        )
        self.assertFalse(result)
        self.assertEqual(win._backlink_index._target_to_sources, {})
        win._coalesce_backlink_rebuild.assert_called_once_with()
        win._schedule_backlink_build.assert_not_called()
        win._dump_debug.assert_not_called()

    def test_coalesce_backlink_rebuild_debounces(self):
        """R17.1: consecutive coalesce calls while a rebuild is already
        pending must not stack further timers (livelock protection)."""
        import markdown_vault.app_window as aw

        win = self._make_fake_window()
        with patch("markdown_vault.app_window.GLib") as glib:
            win._coalesce_backlink_rebuild()
            win._coalesce_backlink_rebuild()
        self.assertEqual(glib.timeout_add.call_count, 1)
        self.assertIsNotNone(win._rebuild_timeout)
        self.assertEqual(
            glib.timeout_add.call_args.args[0],
            aw._BACKLINK_REBUILD_COOLDOWN_MS,
        )

    def test_do_backlink_rebuild_reschedules_from_config(self):
        """R17.1: the debounced rebuild clears the pending timer and schedules
        a full rescan from the config SSOT (not private vault_tree state)."""
        win = self._make_fake_window()
        win._rebuild_timeout = 123
        vaults = [{"name": "A", "path": "/A"}]
        with patch("markdown_vault.app_window.config.load_vaults", return_value=vaults):
            with patch("markdown_vault.app_window.GLib"):
                result = win._do_backlink_rebuild()
        self.assertFalse(result)
        self.assertIsNone(win._rebuild_timeout)
        win._schedule_backlink_build.assert_called_once_with(vaults)

    def test_cancel_backlink_rebuild_removes_pending_timer(self):
        """R18.1: cancelling a pending debounced rebuild must remove the GLib
        timer source so it cannot fire after window teardown."""
        win = self._make_fake_window()
        win._rebuild_timeout = 99
        with patch("markdown_vault.app_window.GLib") as glib:
            win._cancel_backlink_rebuild()
        glib.source_remove.assert_called_once_with(99)
        self.assertIsNone(win._rebuild_timeout)

    def test_cancel_backlink_rebuild_noop_when_none(self):
        """R18.1: cancelling with no pending timer must be a no-op."""
        win = self._make_fake_window()
        with patch("markdown_vault.app_window.GLib") as glib:
            win._cancel_backlink_rebuild()
        glib.source_remove.assert_not_called()
        self.assertIsNone(win._rebuild_timeout)

    def test_apply_backlink_build_refreshes_sidebar_backlinks(self):
        """R16.3: applying the build must refresh sidebar backlinks for the
        active file (a panel open at startup would otherwise stay empty)."""
        win = self._make_fake_window()
        result = win._apply_backlink_build(
            1, 0, {"k": {"s"}}, {"s": {"k"}},
        )
        self.assertFalse(result)
        win._sidebar.refresh_backlinks.assert_called_once_with("/vault/file.md")

    def test_on_vault_added_builds_full_vault_list(self):
        """R16.2: adding a vault must rebuild the full vault list from the
        config SSOT, never a single vault, so one vault can never replace the
        whole index."""
        import unittest.mock
        import markdown_vault.app_window as aw

        vaults = [
            {"name": "A", "path": "/A"},
            {"name": "B", "path": "/B"},
        ]
        win = self._make_fake_window()
        win._switch_vault = unittest.mock.Mock()
        with patch("markdown_vault.app_window.config.load_vaults", return_value=vaults):
            aw.MainWindow._on_vault_added(win, None, "/B")
        win._schedule_backlink_build.assert_called_once_with(vaults)
        win._file_index.build.assert_called_once_with(vaults)


class TestBacklinkIndexBuild(unittest.TestCase):
    """Tests for building the index from scratch."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._vault = Path(self._tmp) / "vault"
        self._vault.mkdir()
        _cfg._vaults_cache = [{"name": "vault", "path": str(self._vault)}]

    def tearDown(self):
        _cfg._vaults_cache = None
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_build_finds_backlinks(self):
        (self._vault / "Page.md").write_text("# Page\n")
        (self._vault / "Note.md").write_text("See [[Page]].\n")
        idx = BacklinkIndex()
        idx.build([{"name": "vault", "path": str(self._vault)}])
        backlinks = idx.find_backlinks(self._vault / "Page.md")
        self.assertEqual(len(backlinks), 1)
        self.assertTrue(backlinks[0].endswith("Note.md"))

    def test_build_ignores_non_md_files(self):
        (self._vault / "Page.md").write_text("# Page\n")
        (self._vault / "Note.txt").write_text("See [[Page]].\n")
        idx = BacklinkIndex()
        idx.build([{"name": "vault", "path": str(self._vault)}])
        backlinks = idx.find_backlinks(self._vault / "Page.md")
        self.assertEqual(len(backlinks), 0)

    def test_build_skips_unreadable_files(self):
        (self._vault / "Page.md").write_text("# Page\n")
        (self._vault / "Bad.md").write_bytes(b"\xff\xfe\x00binary")
        idx = BacklinkIndex()
        idx.build([{"name": "vault", "path": str(self._vault)}])
        backlinks = idx.find_backlinks(self._vault / "Page.md")
        self.assertEqual(len(backlinks), 0)

    def test_find_backlinks_empty_when_no_links(self):
        (self._vault / "Page.md").write_text("# Page\n")
        idx = BacklinkIndex()
        idx.build([{"name": "vault", "path": str(self._vault)}])
        backlinks = idx.find_backlinks(self._vault / "Page.md")
        self.assertEqual(len(backlinks), 0)

    def test_find_backlinks_sorted(self):
        (self._vault / "Target.md").write_text("# Target\n")
        (self._vault / "C.md").write_text("[[Target]].\n")
        (self._vault / "A.md").write_text("[[Target]].\n")
        (self._vault / "B.md").write_text("[[Target]].\n")
        idx = BacklinkIndex()
        idx.build([{"name": "vault", "path": str(self._vault)}])
        backlinks = idx.find_backlinks(self._vault / "Target.md")
        names = [Path(p).name for p in backlinks]
        self.assertEqual(names, ["A.md", "B.md", "C.md"])

    def test_unqualified_link_is_same_vault_only(self):
        """[[Page]] in vault A must NOT backlink vault B's Page.md."""
        vault_b = Path(self._tmp) / "vault_b"
        vault_b.mkdir()
        (vault_b / "Page.md").write_text("# Page B\n")
        (self._vault / "Note.md").write_text("[[Page]].\n")
        idx = BacklinkIndex()
        idx.build([
            {"name": "vault", "path": str(self._vault)},
            {"name": "vault_b", "path": str(vault_b)},
        ])
        self.assertNotIn(
            str(self._vault / "Note.md"),
            idx.find_backlinks(vault_b / "Page.md"),
        )

    def test_cross_vault_qualified_link(self):
        """[[VaultB>Page]] from vault A must backlink vault B's Page.md."""
        vault_b = Path(self._tmp) / "vault_b"
        vault_b.mkdir()
        (vault_b / "Page.md").write_text("# Page B\n")
        (self._vault / "Note.md").write_text("[[vault_b>Page]].\n")
        idx = BacklinkIndex()
        _cfg._vaults_cache = [
            {"name": "vault", "path": str(self._vault)},
            {"name": "vault_b", "path": str(vault_b)},
        ]
        idx.build([
            {"name": "vault", "path": str(self._vault)},
            {"name": "vault_b", "path": str(vault_b)},
        ])
        self.assertIn(
            str(self._vault / "Note.md"),
            idx.find_backlinks(vault_b / "Page.md"),
        )


class TestBacklinkIndexIncremental(unittest.TestCase):
    """Tests for incremental updates to the index."""

    def setUp(self):
        self._idx = BacklinkIndex()
        _cfg._vaults_cache = [{"name": "vault", "path": "/vault"}]

    def test_update_file_adds_links(self):
        path = "/vault/Note.md"
        self._idx.update_file(path, "See [[Page]].\n")
        backlinks = self._idx.find_backlinks(Path("/vault/Page.md"))
        self.assertIn(path, backlinks)

    def test_update_file_removes_old_links(self):
        path = "/vault/Note.md"
        self._idx.update_file(path, "See [[Page]].\n")
        self._idx.update_file(path, "No links here.\n")
        backlinks = self._idx.find_backlinks(Path("/vault/Page.md"))
        self.assertNotIn(path, backlinks)

    def test_remove_file(self):
        path = "/vault/Note.md"
        self._idx.update_file(path, "See [[Page]].\n")
        self._idx.remove_file(path)
        backlinks = self._idx.find_backlinks(Path("/vault/Page.md"))
        self.assertEqual(len(backlinks), 0)

    def test_rename_file(self):
        old_path = "/vault/Old.md"
        new_path = "/vault/New.md"
        self._idx.update_file(old_path, "See [[Page]].\n")
        self._idx.rename_file(old_path, new_path)
        backlinks = self._idx.find_backlinks(Path("/vault/Page.md"))
        self.assertIn(new_path, backlinks)
        self.assertNotIn(old_path, backlinks)

    def test_rename_file_preserves_other_targets(self):
        path = "/vault/Note.md"
        self._idx.update_file(path, "[[A]] and [[B]].\n")
        self._idx.rename_file(path, "/vault/Renamed.md")
        self.assertIn("/vault/Renamed.md", self._idx.find_backlinks(Path("/vault/A.md")))
        self.assertIn("/vault/Renamed.md", self._idx.find_backlinks(Path("/vault/B.md")))

    def test_remove_file_cleans_empty_stems(self):
        path = "/vault/Note.md"
        self._idx.update_file(path, "[[Only]].\n")
        self._idx.remove_file(path)
        # Internal state should be clean.
        self.assertEqual(len(self._idx._target_to_sources), 0)
        self.assertEqual(len(self._idx._source_to_targets), 0)

    def test_mutation_seq_increments_on_mutations(self):
        """R16.1: every incremental mutation bumps the sequence so the async
        build can detect edits made during its scan window."""
        before = self._idx.mutation_seq
        self._idx.update_file("/vault/Note.md", "[[Page]].\n")
        self.assertEqual(self._idx.mutation_seq, before + 1)
        self._idx.remove_file("/vault/Note.md")
        self.assertEqual(self._idx.mutation_seq, before + 2)
        self._idx.rename_file("/vault/A.md", "/vault/B.md")
        self.assertEqual(self._idx.mutation_seq, before + 3)
        self._idx.remove_wikilinks("/vault/C.md")
        self.assertEqual(self._idx.mutation_seq, before + 4)
        self._idx.rename_wikilinks("/vault/D.md", "/vault/E.md")
        self.assertEqual(self._idx.mutation_seq, before + 5)

    def test_mutation_seq_untouched_by_set_index(self):
        """R16.1: set_index is the build swap itself, not a user mutation —
        it must not advance the sequence or the reconciliation would loop."""
        before = self._idx.mutation_seq
        self._idx.set_index({"k": {"s"}}, {"s": {"k"}})
        self.assertEqual(self._idx.mutation_seq, before)


class TestBacklinkIndexAlias(unittest.TestCase):
    """Tests for wikilink alias parsing in the index."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._vault = Path(self._tmp) / "vault"
        self._vault.mkdir()
        _cfg._vaults_cache = [{"name": "vault", "path": str(self._vault)}]

    def tearDown(self):
        _cfg._vaults_cache = None
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_alias_does_not_create_separate_target(self):
        (self._vault / "Page.md").write_text("# Page\n")
        (self._vault / "Note.md").write_text("[[Page|my alias]].\n")
        idx = BacklinkIndex()
        idx.build([{"name": "vault", "path": str(self._vault)}])
        backlinks = idx.find_backlinks(self._vault / "Page.md")
        self.assertEqual(len(backlinks), 1)


if __name__ == "__main__":
    unittest.main()
