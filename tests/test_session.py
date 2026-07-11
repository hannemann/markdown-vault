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
        self._orig_session = _ses.SESSION_FILE
        _cfg.CONFIG_DIR = Path(self._tmpdir)
        _ses.SESSION_FILE = Path(self._tmpdir) / "session.json"

    def tearDown(self):
        _cfg.CONFIG_DIR = self._orig_dir
        _ses.SESSION_FILE = self._orig_session
        shutil.rmtree(self._tmpdir, ignore_errors=True)


class TestSessionDefaults(_TempSessionMixin, unittest.TestCase):
    """Verify default session values."""

    def test_load_defaults_when_no_file(self):
        data = _ses.load_session()
        self.assertEqual(data["window"]["width"], 1200)
        self.assertEqual(data["window"]["height"], 800)
        self.assertFalse(data["sidebar_visible"])
        self.assertIsNone(data["active_tab"])
        self.assertEqual(data["tabs"], [])
        self.assertEqual(data["expanded_vaults"], [])

    def test_load_defaults_on_corrupt_json(self):
        _ses.SESSION_FILE.write_text("{bad json", encoding="utf-8")
        data = _ses.load_session()
        self.assertEqual(data["tabs"], [])


class TestSessionZoomFields(_TempSessionMixin, unittest.TestCase):
    """Verify zoom fields are saved and restored."""

    def test_save_and_load_zoom_fields(self):
        tabs = [
            {
                "path": "/tmp/note.md",
                "view_mode": "split",
                "split_position": 400,
                "editor_zoom": 1.3,
                "preview_zoom": 0.8,
            }
        ]
        _ses.save_session(
            width=1000,
            height=800,
            sidebar_visible=True,
            tabs=tabs,
            active_tab="/tmp/note.md",
            expanded_vaults=["/tmp"],
        )
        loaded = _ses.load_session()
        self.assertEqual(len(loaded["tabs"]), 1)
        tab = loaded["tabs"][0]
        self.assertAlmostEqual(tab["editor_zoom"], 1.3)
        self.assertAlmostEqual(tab["preview_zoom"], 0.8)

    def test_save_without_zoom_fields(self):
        tabs = [
            {
                "path": "/tmp/note.md",
                "view_mode": "edit",
                "split_position": 600,
            }
        ]
        _ses.save_session(
            width=1000,
            height=800,
            sidebar_visible=False,
            tabs=tabs,
            active_tab=None,
        )
        loaded = _ses.load_session()
        tab = loaded["tabs"][0]
        self.assertNotIn("editor_zoom", tab)
        self.assertNotIn("preview_zoom", tab)

    def test_round_trip_preserves_all_fields(self):
        _ses.save_session(
            width=1400,
            height=900,
            sidebar_visible=True,
            tabs=[
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
            active_tab="/tmp/b.md",
            expanded_vaults=["/tmp"],
        )
        loaded = _ses.load_session()
        self.assertEqual(loaded["window"]["width"], 1400)
        self.assertTrue(loaded["sidebar_visible"])
        self.assertEqual(len(loaded["tabs"]), 2)
        self.assertAlmostEqual(loaded["tabs"][0]["editor_zoom"], 2.0)
        self.assertAlmostEqual(loaded["tabs"][1]["preview_zoom"], 1.25)
        self.assertEqual(loaded["active_tab"], "/tmp/b.md")
        self.assertEqual(loaded["expanded_vaults"], ["/tmp"])


if __name__ == "__main__":
    unittest.main()
