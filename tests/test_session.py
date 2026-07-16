"""Tests for markdown_vault.session — session persistence."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

import src.session as _ses
import src.config as _cfg


class _TempSessionMixin:
    """Redirect session file to a temp dir."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._orig_dir = _cfg.CONFIG_DIR
        self._orig_file = _cfg.CONFIG_FILE
        self._orig_session = _ses.SESSION_FILE
        _cfg.CONFIG_DIR = Path(self._tmpdir)
        _cfg.CONFIG_FILE = Path(self._tmpdir) / "vaults.yaml"
        _ses.SESSION_FILE = Path(self._tmpdir) / "session.json"

    def tearDown(self):
        _cfg.CONFIG_DIR = self._orig_dir
        _cfg.CONFIG_FILE = self._orig_file
        _ses.SESSION_FILE = self._orig_session
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
        # Write vaults.yaml with our test vault.
        import yaml
        (_cfg.CONFIG_DIR / "vaults.yaml").write_text(
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


if __name__ == "__main__":
    unittest.main()
