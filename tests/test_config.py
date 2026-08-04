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
        _cfg._vaults_cache = None

    def tearDown(self):
        _cfg.CONFIG_DIR = self._orig_dir
        _cfg.CONFIG_FILE = self._orig_file
        _cfg._vaults_cache = None
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

    def test_loads_vault_icon(self):
        _cfg.CONFIG_FILE.write_text(
            "vaults:\n  - name: Notes\n    path: /tmp/notes\n    icon: 🧠\n",
            encoding="utf-8",
        )
        result = _cfg.load_vaults()
        self.assertEqual(result[0].get("icon"), "🧠")

    def test_missing_icon_absent(self):
        _cfg.CONFIG_FILE.write_text(
            "vaults:\n  - name: Notes\n    path: /tmp/notes\n", encoding="utf-8"
        )
        self.assertNotIn("icon", _cfg.load_vaults()[0])

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

    def test_uniquifies_duplicate_names(self):
        _cfg.CONFIG_FILE.write_text(
            "vaults:\n"
            "  - name: Notes\n    path: /tmp/a\n"
            "  - name: Notes\n    path: /tmp/b\n"
            "  - name: Notes\n    path: /tmp/c\n",
            encoding="utf-8",
        )
        result = _cfg.load_vaults()
        # All three paths survive (distinct), names are made unique.
        self.assertEqual([v["path"] for v in result], ["/tmp/a", "/tmp/b", "/tmp/c"])
        self.assertEqual([v["name"] for v in result], ["Notes", "Notes(2)", "Notes(3)"])

    def test_uniquify_avoids_clashing_with_existing_suffix(self):
        _cfg.CONFIG_FILE.write_text(
            "vaults:\n"
            "  - name: Notes\n    path: /tmp/a\n"
            "  - name: Notes (2)\n    path: /tmp/b\n"
            "  - name: Notes\n    path: /tmp/c\n",
            encoding="utf-8",
        )
        names = [v["name"] for v in _cfg.load_vaults()]
        # Pre-existing "Notes (2)" sanitizes to "Notes(2)"; the second plain
        # "Notes" must then not collide with it.
        self.assertEqual(names, ["Notes", "Notes(2)", "Notes(3)"])
        self.assertEqual(len(set(names)), 3)

    def test_sanitizes_forbidden_chars_in_name(self):
        _cfg.CONFIG_FILE.write_text(
            "vaults:\n  - name: 'a>b|c'\n    path: /tmp/x\n", encoding="utf-8"
        )
        result = _cfg.load_vaults()
        self.assertEqual(result[0]["name"], "abc")

    def test_sanitize_falls_back_to_dir_name(self):
        _cfg.CONFIG_FILE.write_text(
            "vaults:\n  - name: '>>||'\n    path: /tmp/RealDir\n", encoding="utf-8"
        )
        result = _cfg.load_vaults()
        self.assertEqual(result[0]["name"], "RealDir")

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

    def test_rename_vault(self):
        _cfg.add_vault("Notes", "/tmp/notes")
        result = _cfg.rename_vault("/tmp/notes", "Documents")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "Documents")
        self.assertEqual(result[0]["path"], "/tmp/notes")

    def test_rename_vault_preserves_other_vaults(self):
        _cfg.add_vault("A", "/tmp/a")
        _cfg.add_vault("B", "/tmp/b")
        result = _cfg.rename_vault("/tmp/a", "Alpha")
        self.assertEqual(len(result), 2)
        names = {v["name"]: v["path"] for v in result}
        self.assertEqual(names["Alpha"], "/tmp/a")
        self.assertEqual(names["B"], "/tmp/b")

    def test_rename_nonexistent_is_noop(self):
        result = _cfg.rename_vault("/nonexistent", "X")
        self.assertEqual(len(result), 0)

    def test_set_vault_icon_roundtrips(self):
        _cfg.add_vault("Notes", "/tmp/notes")
        _cfg.set_vault_icon("/tmp/notes", "🧠")
        _cfg._vaults_cache = None  # force re-read from disk
        self.assertEqual(_cfg.load_vaults()[0].get("icon"), "🧠")

    def test_clear_vault_icon(self):
        _cfg.add_vault("Notes", "/tmp/notes")
        _cfg.set_vault_icon("/tmp/notes", "🧠")
        _cfg.set_vault_icon("/tmp/notes", None)
        _cfg._vaults_cache = None
        self.assertNotIn("icon", _cfg.load_vaults()[0])

    def test_set_icon_preserves_name(self):
        _cfg.add_vault("Notes", "/tmp/notes")
        result = _cfg.set_vault_icon("/tmp/notes", "📚")
        self.assertEqual(result[0]["name"], "Notes")
        self.assertEqual(result[0]["path"], "/tmp/notes")

    def test_set_vault_icon_mono_roundtrips(self):
        _cfg.add_vault("Notes", "/tmp/notes")
        _cfg.set_vault_icon("/tmp/notes", "🧠", mono=True)
        _cfg._vaults_cache = None
        v = _cfg.load_vaults()[0]
        self.assertEqual(v.get("icon"), "🧠")
        self.assertTrue(v.get("mono"))

    def test_mono_cleared_when_false(self):
        _cfg.add_vault("Notes", "/tmp/notes")
        _cfg.set_vault_icon("/tmp/notes", "🧠", mono=True)
        _cfg.set_vault_icon("/tmp/notes", "🧠", mono=False)
        _cfg._vaults_cache = None
        self.assertNotIn("mono", _cfg.load_vaults()[0])


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


class TestWebkitEnv(_TempConfigMixin, unittest.TestCase):
    """Tests for ``apply_webkit_env`` (WebKit VRAM/GPU switches)."""

    def test_webkit_defaults_are_off(self):
        s = _cfg.load_settings()
        self.assertFalse(s["webkit_disable_dmabuf"])
        self.assertFalse(s["webkit_disable_compositing"])

    def test_apply_webkit_env_sets_both_when_true(self):
        import os
        from unittest import mock

        settings = {"webkit_disable_dmabuf": True, "webkit_disable_compositing": True}
        with mock.patch.dict(os.environ, {}, clear=True):
            _cfg.apply_webkit_env(settings)
            self.assertEqual(os.environ["WEBKIT_DISABLE_DMABUF_RENDERER"], "1")
            self.assertEqual(os.environ["WEBKIT_DISABLE_COMPOSITING_MODE"], "1")

    def test_apply_webkit_env_leaves_unset_when_false(self):
        import os
        from unittest import mock

        settings = {"webkit_disable_dmabuf": False, "webkit_disable_compositing": False}
        with mock.patch.dict(os.environ, {}, clear=True):
            _cfg.apply_webkit_env(settings)
            self.assertNotIn("WEBKIT_DISABLE_DMABUF_RENDERER", os.environ)
            self.assertNotIn("WEBKIT_DISABLE_COMPOSITING_MODE", os.environ)

    def test_apply_webkit_env_loads_settings_when_none(self):
        import os
        from unittest import mock

        settings = _cfg.load_settings()
        settings["webkit_disable_dmabuf"] = True
        _cfg.save_settings(settings)
        with mock.patch.dict(os.environ, {}, clear=True):
            _cfg.apply_webkit_env()
            self.assertEqual(os.environ["WEBKIT_DISABLE_DMABUF_RENDERER"], "1")
            self.assertNotIn("WEBKIT_DISABLE_COMPOSITING_MODE", os.environ)


if __name__ == "__main__":
    unittest.main()
