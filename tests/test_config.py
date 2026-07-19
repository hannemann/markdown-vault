"""Tests for markdown_vault.config — vault configuration management."""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

# Patch CONFIG_DIR / CONFIG_FILE before importing the module under test
# so that no real user config is touched.
import markdown_vault.config as _cfg


class _TempConfigMixin:
    """Redirect config to a temporary directory for each test."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._orig_dir = _cfg.CONFIG_DIR
        self._orig_file = _cfg.CONFIG_FILE
        _cfg.CONFIG_DIR = Path(self._tmpdir)
        _cfg.CONFIG_FILE = Path(self._tmpdir) / "vaults.yaml"

    def tearDown(self):
        _cfg.CONFIG_DIR = self._orig_dir
        _cfg.CONFIG_FILE = self._orig_file
        shutil.rmtree(self._tmpdir, ignore_errors=True)


class TestLoadVaults(_TempConfigMixin, unittest.TestCase):
    """Tests for ``load_vaults``."""

    def test_returns_empty_when_no_file(self):
        self.assertEqual(_cfg.load_vaults(), [])

    def test_returns_empty_on_corrupt_yaml(self):
        _cfg.CONFIG_FILE.write_text("{{invalid yaml::", encoding="utf-8")
        self.assertEqual(_cfg.load_vaults(), [])

    def test_loads_vaults_from_yaml(self):
        _cfg.CONFIG_FILE.write_text(
            "vaults:\n  - name: Notes\n    path: /tmp/notes\n", encoding="utf-8"
        )
        result = _cfg.load_vaults()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "Notes")
        self.assertEqual(result[0]["path"], "/tmp/notes")

    def test_resolves_paths_to_absolute(self):
        _cfg.CONFIG_FILE.write_text(
            "vaults:\n  - name: Rel\n    path: relative/path\n", encoding="utf-8"
        )
        result = _cfg.load_vaults()
        self.assertTrue(os.path.isabs(result[0]["path"]))

    def test_deduplicates_paths(self):
        _cfg.CONFIG_FILE.write_text(
            "vaults:\n"
            "  - name: A\n    path: /tmp/x\n"
            "  - name: B\n    path: /tmp/x\n",
            encoding="utf-8",
        )
        result = _cfg.load_vaults()
        self.assertEqual(len(result), 1)

    def test_skips_empty_paths(self):
        _cfg.CONFIG_FILE.write_text(
            "vaults:\n  - name: Empty\n    path: ''\n", encoding="utf-8"
        )
        self.assertEqual(_cfg.load_vaults(), [])

    def test_load_vaults_empty_yaml_key(self):
        _cfg.CONFIG_FILE.write_text("vaults:\n", encoding="utf-8")
        self.assertEqual(_cfg.load_vaults(), [])

    def test_load_vaults_none_yaml_key(self):
        _cfg.CONFIG_FILE.write_text("vaults: null\n", encoding="utf-8")
        self.assertEqual(_cfg.load_vaults(), [])


class TestSaveVaults(_TempConfigMixin, unittest.TestCase):
    """Tests for ``save_vaults``."""

    def test_creates_config_dir(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        _cfg.save_vaults([])
        self.assertTrue(_cfg.CONFIG_DIR.exists())

    def test_saves_and_round_trips(self):
        vaults = [{"name": "Work", "path": "/home/user/work"}]
        _cfg.save_vaults(vaults)
        loaded = _cfg.load_vaults()
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["name"], "Work")

    def test_deduplicates_on_save(self):
        vaults = [
            {"name": "A", "path": "/tmp/a"},
            {"name": "A2", "path": "/tmp/a"},
        ]
        _cfg.save_vaults(vaults)
        loaded = _cfg.load_vaults()
        self.assertEqual(len(loaded), 1)

    def test_save_vaults_preserves_settings(self):
        settings = _cfg.load_settings()
        settings["autosave_interval"] = 999
        settings["editor_font_size"] = 22
        _cfg.save_settings(settings)
        _cfg.save_vaults([{"name": "Notes", "path": "/tmp/notes"}])
        loaded = _cfg.load_settings()
        self.assertEqual(loaded["autosave_interval"], 999)
        self.assertEqual(loaded["editor_font_size"], 22)


class TestAddVault(_TempConfigMixin, unittest.TestCase):
    """Tests for ``add_vault``."""

    def test_adds_vault(self):
        result = _cfg.add_vault("Notes", "/tmp/notes")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "Notes")

    def test_adds_multiple(self):
        _cfg.add_vault("A", "/tmp/a")
        _cfg.add_vault("B", "/tmp/b")
        result = _cfg.load_vaults()
        self.assertEqual(len(result), 2)

    def test_deduplicates_on_add(self):
        _cfg.add_vault("A", "/tmp/a")
        _cfg.add_vault("B", "/tmp/a")
        result = _cfg.load_vaults()
        self.assertEqual(len(result), 1)


class TestRemoveVault(_TempConfigMixin, unittest.TestCase):
    """Tests for ``remove_vault``."""

    def test_removes_vault(self):
        _cfg.add_vault("Notes", "/tmp/notes")
        result = _cfg.remove_vault("/tmp/notes")
        self.assertEqual(len(result), 0)

    def test_remove_nonexistent_is_noop(self):
        result = _cfg.remove_vault("/nonexistent")
        self.assertEqual(len(result), 0)


class TestSettings(_TempConfigMixin, unittest.TestCase):
    """Tests for ``load_settings`` and ``save_settings``."""

    def test_load_settings_defaults_when_no_file(self):
        s = _cfg.load_settings()
        self.assertEqual(s["autosave_interval"], 30)
        self.assertEqual(s["editor_font_size"], 14)
        self.assertEqual(s["editor_tab_width"], 4)
        self.assertTrue(s["editor_wrap_text"])
        self.assertAlmostEqual(s["preview_zoom"], 1.0)
        self.assertEqual(s["default_view_mode"], "edit")

    def test_load_settings_on_corrupt_yaml(self):
        _cfg.CONFIG_FILE.write_text("{{bad yaml", encoding="utf-8")
        s = _cfg.load_settings()
        self.assertEqual(s["autosave_interval"], 30)

    def test_save_and_load_round_trip(self):
        settings = _cfg.load_settings()
        settings["autosave_interval"] = 60
        settings["editor_font_size"] = 18
        settings["editor_tab_width"] = 2
        settings["editor_wrap_text"] = False
        settings["preview_zoom"] = 1.5
        _cfg.save_settings(settings)
        loaded = _cfg.load_settings()
        self.assertEqual(loaded["autosave_interval"], 60)
        self.assertEqual(loaded["editor_font_size"], 18)
        self.assertEqual(loaded["editor_tab_width"], 2)
        self.assertFalse(loaded["editor_wrap_text"])
        self.assertAlmostEqual(loaded["preview_zoom"], 1.5)

    def test_save_settings_preserves_vaults(self):
        _cfg.add_vault("Notes", "/tmp/notes")
        settings = _cfg.load_settings()
        settings["autosave_interval"] = 120
        _cfg.save_settings(settings)
        vaults = _cfg.load_vaults()
        self.assertEqual(len(vaults), 1)
        self.assertEqual(vaults[0]["name"], "Notes")

    def test_save_settings_creates_dir(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        _cfg.save_settings({"autosave_interval": 10})
        s = _cfg.load_settings()
        self.assertEqual(s["autosave_interval"], 10)

    def test_load_settings_empty_yaml_key(self):
        _cfg.CONFIG_FILE.write_text("settings:\n", encoding="utf-8")
        s = _cfg.load_settings()
        self.assertEqual(s["autosave_interval"], 30)

    def test_no_tmp_files_after_save(self):
        _cfg.save_vaults([{"name": "A", "path": "/tmp/a"}])
        tmp_files = list(Path(self._tmpdir).glob("*.tmp"))
        self.assertEqual(len(tmp_files), 0)


if __name__ == "__main__":
    unittest.main()
