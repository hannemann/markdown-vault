"""Tests for markdown_vault.core.config — vault configuration management."""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

# Patch CONFIG_DIR / CONFIG_FILE before importing the module under test
# so that no real user config is touched.
import markdown_vault.core.config as _cfg


class _TempConfigMixin:
    """Redirect config to a temporary directory for each test."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._orig_dir = _cfg.CONFIG_DIR
        self._orig_file = _cfg.CONFIG_FILE
        _cfg.CONFIG_DIR = Path(self._tmpdir)
        _cfg.CONFIG_FILE = Path(self._tmpdir) / "vaults.yaml"
        _cfg._vaults_cache = None
        # The owned settings are process state: without this, one test's object
        # (loaded from a temp dir that is about to be deleted) would answer the
        # next test's reads.
        _cfg._settings_singleton = None

    def tearDown(self):
        _cfg.CONFIG_DIR = self._orig_dir
        _cfg.CONFIG_FILE = self._orig_file
        _cfg._vaults_cache = None
        _cfg._settings_singleton = None
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


class TestSettingsAreOneObject(_TempConfigMixin, unittest.TestCase):
    """The app has exactly one settings state, owned here.

    Two components each holding their own snapshot is how an update gets lost: the
    whole block is written back, so whoever saves last resets every key the other
    changed meanwhile — and reads go stale in between, which is why call sites grew
    ad-hoc reloads. `settings()` hands out *the* object, so there is nothing that
    could drift apart.
    """

    def test_every_caller_gets_the_same_object(self):
        self.assertIs(_cfg.settings(), _cfg.settings())

    def test_a_change_by_one_caller_is_visible_to_the_other(self):
        a, b = _cfg.settings(), _cfg.settings()
        a["editor_font_size"] = 21
        self.assertEqual(b["editor_font_size"], 21)

    def test_saving_needs_no_argument(self):
        _cfg.settings()["autosave_interval"] = 99
        _cfg.save_settings()
        self.assertEqual(_cfg.load_settings()["autosave_interval"], 99)

    def test_a_second_writer_cannot_undo_the_first(self):
        # The lost update this ticket is about: two independent changes, one file.
        _cfg.settings()["autosave_interval"] = 60      # e.g. the app window
        _cfg.save_settings()
        _cfg.settings()["editor_font_size"] = 18       # e.g. the dialog
        _cfg.save_settings()
        written = _cfg.load_settings()
        self.assertEqual(written["autosave_interval"], 60)
        self.assertEqual(written["editor_font_size"], 18)

    def test_load_settings_still_returns_an_independent_copy(self):
        # It stays available for tests and one-off reads — but it is a copy, so a
        # caller cannot accidentally treat it as the app's live state.
        copy = _cfg.load_settings()
        copy["editor_font_size"] = 99
        self.assertNotEqual(_cfg.settings()["editor_font_size"], 99)

    def test_reload_replaces_the_contents_not_the_object(self):
        # Callers hold the object for the process lifetime; handing out a new one
        # on reload would resurrect exactly the stale-snapshot problem.
        live = _cfg.settings()
        live["autosave_interval"] = 60
        _cfg.save_settings()
        _cfg.CONFIG_FILE.write_text("settings:\n  autosave_interval: 5\n",
                                    encoding="utf-8")
        _cfg.reload_settings()
        self.assertIs(_cfg.settings(), live)
        self.assertEqual(live["autosave_interval"], 5)


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

    def test_ask_num_ctx_default_and_round_trip(self):
        self.assertEqual(_cfg.default("ask_num_ctx"), 8192)
        self.assertEqual(_cfg.load_settings()["ask_num_ctx"], 8192)
        settings = _cfg.load_settings()
        settings["ask_num_ctx"] = 16384
        _cfg.save_settings(settings)
        self.assertEqual(_cfg.load_settings()["ask_num_ctx"], 16384)

    def test_ask_top_k_default_and_round_trip(self):
        self.assertEqual(_cfg.default("ask_top_k"), 10)
        self.assertEqual(_cfg.load_settings()["ask_top_k"], 10)
        settings = _cfg.load_settings()
        settings["ask_top_k"] = 5
        _cfg.save_settings(settings)
        self.assertEqual(_cfg.load_settings()["ask_top_k"], 5)

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


class TestDefaultAndMigration(unittest.TestCase):
    def test_default_returns_builtin(self):
        self.assertEqual(_cfg.default("ask_model"), "llama3.2")
        self.assertEqual(_cfg.default("ask_backend"), "local")
        self.assertEqual(_cfg.default("ask_engine"), "auto")

    def test_kv_cache_defaults(self):
        self.assertEqual(_cfg.default("ask_kv_type_k"), "f16")
        self.assertEqual(_cfg.default("ask_kv_type_v"), "f16")
        self.assertFalse(_cfg.default("ask_flash_attn"))
        self.assertTrue(_cfg.default("ask_use_mmap"))
        self.assertEqual(_cfg.default("ask_max_tokens"), 1024)

    def test_default_gguf_path_is_under_state_dir(self):
        self.assertTrue(_cfg.default_gguf_path().endswith("models/model.gguf"))

    def test_model_filename_from_url(self):
        self.assertEqual(
            _cfg.model_filename_from_url("https://h/x/Foo-Q4_K_M.gguf"),
            "Foo-Q4_K_M.gguf")
        self.assertEqual(_cfg.model_filename_from_url("https://h/x/nope"),
                         "model.gguf")

    def test_resolve_model_prefers_explicit_existing_file(self):
        import os
        import tempfile
        f = tempfile.NamedTemporaryFile(suffix=".gguf", delete=False)
        f.write(b"GGUF\x00\x00\x00\x00")
        f.close()
        try:
            self.assertEqual(
                _cfg.resolve_model_path({"ask_gguf_path": f.name}), f.name)
        finally:
            os.unlink(f.name)

    def test_is_gguf_checks_magic(self):
        import os
        import tempfile
        good = tempfile.NamedTemporaryFile(suffix=".gguf", delete=False)
        good.write(b"GGUF\x00\x00\x00\x00")
        good.close()
        bad = tempfile.NamedTemporaryFile(suffix=".gguf", delete=False)
        bad.write(b"<!DOCTYPE html>")
        bad.close()
        try:
            self.assertTrue(_cfg.is_gguf(good.name))
            self.assertFalse(_cfg.is_gguf(bad.name))
            self.assertFalse(_cfg.is_gguf("/no/such/file.gguf"))
        finally:
            os.unlink(good.name)
            os.unlink(bad.name)

    def test_gguf_n_layers_reads_block_count(self):
        import os
        import struct
        import tempfile
        # minimal GGUF: magic, version, tensor_count=0, 2 KV pairs (a string one
        # to exercise skipping, then the uint32 block_count).
        buf = b"GGUF" + struct.pack("<IQQ", 3, 0, 2)
        for key, vtype, val in [(b"general.name", 8, b"x"),
                                (b"llama.block_count", 4, 32)]:
            buf += struct.pack("<Q", len(key)) + key + struct.pack("<I", vtype)
            if vtype == 8:
                buf += struct.pack("<Q", len(val)) + val
            else:
                buf += struct.pack("<I", val)
        f = tempfile.NamedTemporaryFile(suffix=".gguf", delete=False)
        f.write(buf)
        f.close()
        try:
            self.assertEqual(_cfg.gguf_n_layers(f.name), 32)
            self.assertIsNone(_cfg.gguf_n_layers("/no/such.gguf"))
        finally:
            os.unlink(f.name)

    def test_normalize_gguf_url_blob_to_resolve(self):
        blob = "https://huggingface.co/x/y/blob/main/m.gguf"
        self.assertEqual(_cfg.normalize_gguf_url(blob),
                         "https://huggingface.co/x/y/resolve/main/m.gguf")
        keep = "https://huggingface.co/x/y/resolve/main/m.gguf"
        self.assertEqual(_cfg.normalize_gguf_url(keep), keep)     # already raw

    def test_list_models_skips_non_gguf(self):
        import tempfile
        from pathlib import Path
        d = Path(tempfile.mkdtemp())
        (d / "real.gguf").write_bytes(b"GGUF\x00\x00")
        (d / "page.gguf").write_bytes(b"<html>")     # HTML masquerading as a model
        orig = _cfg.models_dir
        _cfg.models_dir = lambda: d
        try:
            self.assertEqual([p.name for p in _cfg.list_models()], ["real.gguf"])
        finally:
            _cfg.models_dir = orig

    def test_resolve_model_falls_back_when_folder_empty(self):
        orig = _cfg.list_models
        _cfg.list_models = lambda: []
        try:
            self.assertEqual(_cfg.resolve_model_path({}), "")
            # a stale explicit path is still named (so the reason can point at it)
            self.assertEqual(
                _cfg.resolve_model_path({"ask_gguf_path": "/gone.gguf"}),
                "/gone.gguf")
        finally:
            _cfg.list_models = orig

    def test_default_unknown_key_is_empty(self):
        self.assertEqual(_cfg.default("no_such_key"), "")

    def test_migrate_derives_onnx_dir_from_old_model(self):
        s = {"semantic_onnx_dir": "", "semantic_onnx_model": "/x/y/model.onnx"}
        _cfg._migrate_settings(s)
        self.assertEqual(s["semantic_onnx_dir"], "/x/y")

    def test_migrate_keeps_existing_dir(self):
        s = {"semantic_onnx_dir": "/keep", "semantic_onnx_model": "/x/y/model.onnx"}
        _cfg._migrate_settings(s)
        self.assertEqual(s["semantic_onnx_dir"], "/keep")

    def test_migrate_noop_without_old_keys(self):
        s = {"semantic_onnx_dir": ""}
        _cfg._migrate_settings(s)
        self.assertEqual(s["semantic_onnx_dir"], "")


class TestSettingsSecretMasking(_TempConfigMixin, unittest.TestCase):
    """A secret in settings (legacy plaintext) must not reach the debug log."""

    def test_api_key_masked_in_debug_dump(self):
        _cfg.save_settings({"ask_api_key": "sk-super-secret", "autosave_interval": 30})
        _cfg._last_logged_settings = None                 # force the debug dump
        with self.assertLogs("markdown_vault.core.config", level="DEBUG") as cm:
            _cfg.load_settings()
        blob = "\n".join(cm.output)
        self.assertNotIn("sk-super-secret", blob)
        self.assertIn("***", blob)


class TestSettingsWriteProvenance(_TempConfigMixin, unittest.TestCase):
    """A settings write must say what it changed — the missing piece that made an
    unexplained reset take an afternoon to track down. INFO, so it survives a reset
    (which restores loglevel: info and would silence DEBUG)."""

    def test_changed_keys_are_logged(self):
        _cfg.save_settings({"autosave_interval": 30, "loglevel": "info"})
        with self.assertLogs("markdown_vault.core.config", level="INFO") as cm:
            _cfg.save_settings({"autosave_interval": 90, "loglevel": "info"})
        blob = "\n".join(cm.output)
        self.assertIn("autosave_interval", blob)
        self.assertIn("90", blob)
        self.assertNotIn("loglevel", blob)      # unchanged keys are not noise

    def test_secret_values_are_masked(self):
        _cfg.save_settings({})
        with self.assertLogs("markdown_vault.core.config", level="INFO") as cm:
            _cfg.save_settings({"ask_api_key": "sk-super-secret"})
        blob = "\n".join(cm.output)
        self.assertNotIn("sk-super-secret", blob)
        self.assertIn("ask_api_key", blob)

    def test_dropping_keys_warns(self):
        # Exactly the shape of the leaked-timer write: a tiny snapshot replacing a
        # full settings block. It must not pass silently.
        _cfg.save_settings({"autosave_interval": 30, "loglevel": "debug",
                            "semantic_search_enabled": True})
        with self.assertLogs("markdown_vault.core.config", level="WARNING") as cm:
            _cfg.save_settings({"ask_system_prompt": ""})
        blob = "\n".join(cm.output)
        self.assertIn("semantic_search_enabled", blob)
        self.assertIn("loglevel", blob)

    def test_log_names_the_caller(self):
        # "which keys" without "from where" leaves the five writers indistinguishable —
        # exactly the question the reset investigation could not answer.
        _cfg.save_settings({"autosave_interval": 30})
        with self.assertLogs("markdown_vault.core.config", level="INFO") as cm:
            _cfg.save_settings({"autosave_interval": 90})
        self.assertIn("test_config.py:", "\n".join(cm.output))

    def test_drop_warning_names_the_caller(self):
        _cfg.save_settings({"autosave_interval": 30, "loglevel": "debug"})
        with self.assertLogs("markdown_vault.core.config", level="WARNING") as cm:
            _cfg.save_settings({"autosave_interval": 30})
        self.assertIn("test_config.py:", "\n".join(cm.output))

    def test_no_log_when_nothing_changed(self):
        _cfg.save_settings({"autosave_interval": 30})
        with self.assertNoLogs("markdown_vault.core.config", level="INFO"):
            _cfg.save_settings({"autosave_interval": 30})


class TestMissingConfigVisibility(_TempConfigMixin, unittest.TestCase):
    """A config file that vanished is the likeliest silent-reset route, so it must be
    visible at a level that a reset (loglevel -> info) does not hide."""

    def test_missing_file_with_existing_dir_warns(self):
        _cfg.CONFIG_DIR.mkdir(parents=True, exist_ok=True)   # we have run before
        with self.assertLogs("markdown_vault.core.config", level="WARNING") as cm:
            _cfg.load_settings()
        self.assertIn("config", "\n".join(cm.output).lower())

    def test_genuine_first_run_stays_quiet(self):
        import shutil
        shutil.rmtree(_cfg.CONFIG_DIR, ignore_errors=True)   # nothing has run yet
        with self.assertNoLogs("markdown_vault.core.config", level="WARNING"):
            _cfg.load_settings()


if __name__ == "__main__":
    unittest.main()
