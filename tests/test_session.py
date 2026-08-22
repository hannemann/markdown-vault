"""Tests for markdown_vault.core.session — session persistence."""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

import markdown_vault.core.session as _ses
import markdown_vault.core.config as _cfg


class _TempSessionMixin:
    """Redirect session file to a temp dir."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._orig_dir = _cfg.CONFIG_DIR
        self._orig_file = _cfg.CONFIG_FILE
        self._orig_session = _ses.SESSION_FILE
        # session.json lives in STATE_DIR now, and saving creates it — redirect that
        # too, or the test would mkdir the developer's real state dir.
        self._orig_state = _cfg.STATE_DIR
        _cfg.CONFIG_DIR = Path(self._tmpdir)
        _cfg.CONFIG_FILE = Path(self._tmpdir) / "settings.yaml"
        _cfg.STATE_DIR = Path(self._tmpdir) / "state"
        _ses.SESSION_FILE = Path(self._tmpdir) / "session.json"
        _cfg._vaults_cache = None

    def tearDown(self):
        _cfg.CONFIG_DIR = self._orig_dir
        _cfg.CONFIG_FILE = self._orig_file
        _cfg.STATE_DIR = self._orig_state
        _ses.SESSION_FILE = self._orig_session
        _cfg._vaults_cache = None
        shutil.rmtree(self._tmpdir, ignore_errors=True)


class TestSessionDefaults(_TempSessionMixin, unittest.TestCase):
    """Verify default session values."""

    def test_load_defaults_when_no_file(self):
        data = _ses.load_session()
        self.assertEqual(data["window"]["width"], 1200)
        self.assertEqual(data["window"]["height"], 800)
        self.assertFalse(data["sidebar_visible"])
        self.assertIsNone(data["active_vault"])
        self.assertEqual(data["vault_sessions"], {})
        self.assertEqual(data["expanded_vaults"], [])

    def test_load_defaults_on_corrupt_json(self):
        _ses.SESSION_FILE.write_text("{bad json", encoding="utf-8")
        data = _ses.load_session()
        self.assertEqual(data["vault_sessions"], {})


class TestSessionVaultSessions(_TempSessionMixin, unittest.TestCase):
    """Verify vault_sessions are saved and restored."""

    def test_save_and_load_vault_sessions(self):
        tabs = [
            {
                "path": "/tmp/note.md",
                "view_mode": "split",
                "split_position": 400,
                "editor_zoom": 1.3,
                "preview_zoom": 0.8,
            }
        ]
        vault_sessions = {
            "/tmp": {
                "tabs": tabs,
                "active_tab": "/tmp/note.md",
                "mru": ["/tmp/note.md"],
            }
        }
        _ses.save_session(
            width=1000,
            height=800,
            sidebar_visible=True,
            active_vault="/tmp",
            vault_sessions=vault_sessions,
            expanded_vaults=["/tmp"],
        )
        loaded = _ses.load_session()
        self.assertEqual(loaded["active_vault"], "/tmp")
        vs = loaded["vault_sessions"]["/tmp"]
        self.assertEqual(len(vs["tabs"]), 1)
        self.assertAlmostEqual(vs["tabs"][0]["editor_zoom"], 1.3)
        self.assertAlmostEqual(vs["tabs"][0]["preview_zoom"], 0.8)
        self.assertEqual(vs["active_tab"], "/tmp/note.md")
        self.assertEqual(vs["mru"], ["/tmp/note.md"])

    def test_ask_last_question_round_trips(self):
        _ses.save_session(
            width=1000,
            height=800,
            sidebar_visible=False,
            active_vault="/tmp",
            vault_sessions={},
            ask_last_question="which planet is heaviest?",
        )
        loaded = _ses.load_session()
        self.assertEqual(loaded["ask_last_question"], "which planet is heaviest?")

    def test_ask_last_question_defaults_empty(self):
        self.assertEqual(_ses.load_session()["ask_last_question"], "")

    def test_save_without_zoom_fields(self):
        tabs = [
            {
                "path": "/tmp/note.md",
                "view_mode": "edit",
                "split_position": 600,
            }
        ]
        vault_sessions = {
            "/tmp": {"tabs": tabs, "active_tab": None}
        }
        _ses.save_session(
            width=1000,
            height=800,
            sidebar_visible=False,
            active_vault="/tmp",
            vault_sessions=vault_sessions,
        )
        loaded = _ses.load_session()
        tab = loaded["vault_sessions"]["/tmp"]["tabs"][0]
        self.assertNotIn("editor_zoom", tab)
        self.assertNotIn("preview_zoom", tab)

    def test_round_trip_preserves_all_fields(self):
        vault_sessions = {
            "/tmp": {
                "tabs": [
                    {
                        "path": "/tmp/a.md",
                        "view_mode": "render",
                        "split_position": 500,
                        "editor_zoom": 2.0,
                        "preview_zoom": 0.5,
                    },
                    {
                        "path": "/tmp/b.md",
                        "view_mode": "split",
                        "split_position": 300,
                        "editor_zoom": 0.75,
                        "preview_zoom": 1.25,
                    },
                ],
                "active_tab": "/tmp/b.md",
                "mru": ["/tmp/b.md", "/tmp/a.md"],
            }
        }
        _ses.save_session(
            width=1400,
            height=900,
            sidebar_visible=True,
            active_vault="/tmp",
            vault_sessions=vault_sessions,
            expanded_vaults=["/tmp"],
        )
        loaded = _ses.load_session()
        self.assertEqual(loaded["window"]["width"], 1400)
        self.assertTrue(loaded["sidebar_visible"])
        self.assertEqual(loaded["active_vault"], "/tmp")
        vs = loaded["vault_sessions"]["/tmp"]
        self.assertEqual(len(vs["tabs"]), 2)
        self.assertAlmostEqual(vs["tabs"][0]["editor_zoom"], 2.0)
        self.assertAlmostEqual(vs["tabs"][1]["preview_zoom"], 1.25)
        self.assertEqual(vs["active_tab"], "/tmp/b.md")
        self.assertEqual(vs["mru"], ["/tmp/b.md", "/tmp/a.md"])
        self.assertEqual(loaded["expanded_vaults"], ["/tmp"])


class TestPruneVaultSession(_TempSessionMixin, unittest.TestCase):
    """Verify prune_vault_session removes missing files."""

    def test_removes_missing_files(self):
        tmpdir = Path(self._tmpdir)
        existing = tmpdir / "note.md"
        existing.touch()
        vault_session = {
            "tabs": [
                {"path": str(existing), "view_mode": "edit"},
                {"path": str(tmpdir / "deleted.md"), "view_mode": "split"},
            ],
            "active_tab": str(tmpdir / "deleted.md"),
        }
        pruned = _ses.prune_vault_session(vault_session)
        self.assertEqual(len(pruned["tabs"]), 1)
        self.assertEqual(pruned["tabs"][0]["path"], str(existing))
        self.assertIsNone(pruned["active_tab"])

    def test_keeps_existing_files(self):
        tmpdir = Path(self._tmpdir)
        existing = tmpdir / "note.md"
        existing.touch()
        vault_session = {
            "tabs": [{"path": str(existing), "view_mode": "edit"}],
            "active_tab": str(existing),
        }
        pruned = _ses.prune_vault_session(vault_session)
        self.assertEqual(len(pruned["tabs"]), 1)
        self.assertEqual(pruned["active_tab"], str(existing))

    def test_prune_handles_empty_path(self):
        """Empty string path should be treated as missing."""
        vault_session = {
            "tabs": [
                {"path": "", "view_mode": "edit"},
                {"path": str(Path(self._tmpdir) / "note.md"), "view_mode": "edit"},
            ],
            "active_tab": "",
            "mru": ["", str(Path(self._tmpdir) / "note.md")],
        }
        Path(self._tmpdir, "note.md").touch()
        pruned = _ses.prune_vault_session(vault_session)
        self.assertEqual(len(pruned["tabs"]), 1)
        self.assertEqual(pruned["tabs"][0]["path"], str(Path(self._tmpdir) / "note.md"))
        self.assertIsNone(pruned["active_tab"])  # Empty active_tab is cleared
        self.assertEqual(pruned["mru"], [str(Path(self._tmpdir) / "note.md")])

    def test_prune_handles_missing_path_key(self):
        """Tab with no path key should be removed."""
        vault_session = {
            "tabs": [
                {"view_mode": "edit"},  # no path key
                {"path": str(Path(self._tmpdir) / "note.md"), "view_mode": "edit"},
            ],
            "active_tab": str(Path(self._tmpdir) / "note.md"),
            "mru": [str(Path(self._tmpdir) / "note.md")],
        }
        Path(self._tmpdir, "note.md").touch()
        pruned = _ses.prune_vault_session(vault_session)
        self.assertEqual(len(pruned["tabs"]), 1)
        self.assertEqual(pruned["tabs"][0]["path"], str(Path(self._tmpdir) / "note.md"))


class TestLegacyMigration(_TempSessionMixin, unittest.TestCase):
    """Verify old-style sessions are migrated."""

    def test_migrates_legacy_tabs(self):
        # Create a vault config in the temp dir.
        vault_dir = Path(self._tmpdir) / "testvault"
        vault_dir.mkdir()
        note = vault_dir / "note.md"
        note.touch()
        # Write settings.yaml with our test vault.
        import yaml
        (_cfg.CONFIG_DIR / "settings.yaml").write_text(
            yaml.dump({"vaults": [{"name": "test", "path": str(vault_dir)}]}),
            encoding="utf-8",
        )
        # Write a legacy session with top-level tabs.
        _ses.SESSION_FILE.write_text(json.dumps({
            "window": {"width": 800, "height": 600},
            "sidebar_visible": False,
            "active_tab": str(note),
            "tabs": [{"path": str(note), "view_mode": "edit"}],
            "expanded_vaults": [],
        }), encoding="utf-8")
        loaded = _ses.load_session()
        self.assertNotIn("tabs", loaded)
        self.assertNotIn("active_tab", loaded)
        self.assertEqual(loaded["active_vault"], str(vault_dir))
        self.assertIn(str(vault_dir), loaded["vault_sessions"])
        vs = loaded["vault_sessions"][str(vault_dir)]
        self.assertEqual(len(vs["tabs"]), 1)
        self.assertEqual(vs["active_tab"], str(note))


class TestSanitize(unittest.TestCase):
    """_sanitize drops exactly the malformed entries that crash restore, keeps
    the rest, and repairs dangling references."""

    def _data(self, tabs, active=None, mru=None, nav=None):
        return {"vault_sessions": {"/v": {"tabs": tabs, "active_tab": active,
                                          "mru": mru or []}},
                "nav_history": nav or {"history": [], "pos": -1}}

    def _tabs(self, d):
        return [t["path"] for t in d["vault_sessions"]["/v"]["tabs"]]

    def test_keeps_valid_canonical_md(self):
        d = self._data([{"path": "/vault/a.md"}, {"path": "/vault/sub/b.md"}])
        _ses._sanitize(d)
        self.assertEqual(self._tabs(d), ["/vault/a.md", "/vault/sub/b.md"])

    def test_drops_dotdot_dot_and_relative(self):
        d = self._data([{"path": "/vault/../other/a.md"}, {"path": "/vault/./b.md"},
                        {"path": "relative/c.md"}, {"path": "/vault/keep.md"}])
        _ses._sanitize(d)
        self.assertEqual(self._tabs(d), ["/vault/keep.md"])

    def test_drops_directory_and_non_md(self):
        vdir = tempfile.mkdtemp()
        os.mkdir(os.path.join(vdir, "dir.md"))          # a directory ending in .md
        try:
            d = self._data([{"path": os.path.join(vdir, "dir.md")},
                            {"path": vdir},              # vault dir, no .md
                            {"path": "/vault/real.md"}])
            _ses._sanitize(d)
            self.assertEqual(self._tabs(d), ["/vault/real.md"])
        finally:
            shutil.rmtree(vdir, ignore_errors=True)

    def test_drops_non_dict_and_missing_path(self):
        d = self._data(["notadict", {"nopath": 1}, {"path": None}, {"path": "/v/ok.md"}])
        _ses._sanitize(d)
        self.assertEqual(self._tabs(d), ["/v/ok.md"])

    def test_active_tab_reset_to_survivor(self):
        d = self._data([{"path": "/v/keep.md"}], active="/v/./bad.md")
        _ses._sanitize(d)
        self.assertEqual(d["vault_sessions"]["/v"]["active_tab"], "/v/keep.md")

    def test_active_tab_none_when_all_dropped(self):
        d = self._data([{"path": "/v/./bad.md"}], active="/v/./bad.md")
        _ses._sanitize(d)
        self.assertIsNone(d["vault_sessions"]["/v"]["active_tab"])

    def test_mru_filtered_to_survivors(self):
        d = self._data([{"path": "/v/a.md"}], mru=["/v/a.md", "/v/../x.md", "/v/gone.md"])
        _ses._sanitize(d)
        self.assertEqual(d["vault_sessions"]["/v"]["mru"], ["/v/a.md"])

    def test_nav_history_filtered_and_pos_clamped(self):
        d = self._data([], nav={"history": ["/v/a.md", "/v/../b.md", "/v/c.md"], "pos": 2})
        _ses._sanitize(d)
        self.assertEqual(d["nav_history"]["history"], ["/v/a.md", "/v/c.md"])
        self.assertEqual(d["nav_history"]["pos"], 1)

    def test_nav_history_keeps_dict_entries_with_positions(self):
        # New-form entries carry a position; the sanitizer must keep them intact
        # (dropping them would look like a silent empty history after upgrade).
        nav = {"history": [{"path": "/v/a.md", "editor_scroll": 120.0},
                           {"path": "/v/b.md", "preview_scroll": 300.0}], "pos": 1}
        d = self._data([], nav=nav)
        _ses._sanitize(d)
        self.assertEqual(d["nav_history"]["history"],
                         [{"path": "/v/a.md", "editor_scroll": 120.0},
                          {"path": "/v/b.md", "preview_scroll": 300.0}])
        self.assertEqual(d["nav_history"]["pos"], 1)

    def test_nav_history_drops_dict_with_noncanonical_path(self):
        nav = {"history": [{"path": "/v/../b.md"}, {"nopath": 1}, "notadict-butok",
                           {"path": "/v/keep.md", "editor_cursor": 5}], "pos": 3}
        d = self._data([], nav=nav)
        _ses._sanitize(d)
        # "notadict-butok" is not an absolute .md path either → dropped; only the
        # canonical dict survives.
        self.assertEqual(d["nav_history"]["history"],
                         [{"path": "/v/keep.md", "editor_cursor": 5}])
        self.assertEqual(d["nav_history"]["pos"], 0)

    def test_non_dict_containers_reset(self):
        d = {"vault_sessions": "garbage"}
        _ses._sanitize(d)
        self.assertEqual(d["vault_sessions"], {})
        d = {"vault_sessions": {"/v": "notadict", "/w": {"tabs": [{"path": "/w/a.md"}]}}}
        _ses._sanitize(d)
        self.assertNotIn("/v", d["vault_sessions"])
        self.assertIn("/w", d["vault_sessions"])


class TestLoadSanitizes(_TempSessionMixin, unittest.TestCase):
    def test_bad_tab_is_dropped_on_load(self):
        _ses.SESSION_FILE.write_text(json.dumps({
            "vault_sessions": {"/v": {
                "tabs": [{"path": "/v/../poison/Business"}, {"path": "/v/good.md"}],
                "active_tab": "/v/../poison/Business", "mru": []}}}), encoding="utf-8")
        data = _ses.load_session()
        vs = data["vault_sessions"]["/v"]
        self.assertEqual([t["path"] for t in vs["tabs"]], ["/v/good.md"])
        self.assertEqual(vs["active_tab"], "/v/good.md")


class TestPruneHardened(unittest.TestCase):
    def test_prune_drops_directory_that_exists(self):
        vdir = tempfile.mkdtemp()
        try:
            note = os.path.join(vdir, "n.md")
            open(note, "w").close()
            pruned = _ses.prune_vault_session(
                {"tabs": [{"path": vdir}, {"path": os.path.join(vdir, "x/./n.md")},
                          {"path": note}],
                 "active_tab": vdir, "mru": [vdir, note]})
            self.assertEqual([t["path"] for t in pruned["tabs"]], [note])
            self.assertIsNone(pruned["active_tab"])
            self.assertEqual(pruned["mru"], [note])
        finally:
            shutil.rmtree(vdir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
