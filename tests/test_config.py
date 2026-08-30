"""Tests for markdown_vault.core.config — vault configuration management."""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

# Patch CONFIG_DIR / CONFIG_FILE before importing the module under test
# so that no real user config is touched.
import support

import markdown_vault.core.config as _cfg


class _TempConfigMixin:
    """Redirect config to a temporary directory for each test."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._orig_dir = _cfg.CONFIG_DIR
        self._orig_file = _cfg.CONFIG_FILE
        _cfg.CONFIG_DIR = Path(self._tmpdir)
        _cfg.CONFIG_FILE = Path(self._tmpdir) / "settings.yaml"
        # The settings write goes through StateFS now, and its guard reads paths.CONFIG_DIR
        # — the OWNER — not config's rebindable alias, so rebinding the two names above does
        # not move the allowed root with them. Register the temp dir explicitly.
        ctx = support.state_roots(self._tmpdir)
        ctx.__enter__()
        self.addCleanup(ctx.__exit__, None, None, None)
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

    def test_the_settings_write_goes_through_state_fs(self):
        import unittest.mock as mock
        with mock.patch("markdown_vault.core.state_fs.write_text_atomic") as w, \
             mock.patch("markdown_vault.core.state_fs.mkdir") as md:
            _cfg.save_vaults([{"name": "V", "path": "/tmp/v"}])
        w.assert_called_once()
        md.assert_called()

    def test_config_does_not_import_state_fs_at_module_level(self):
        """The import must stay inside the functions that write.

        state_fs imports config at module level (for the vault and model roots), so a
        module-level import back would close a cycle — and the failure mode is not a clean
        error but an import order the next unrelated change can flip. Pinned structurally
        because a cycle that only bites in one import order is not reliably reproducible
        from a test that merely imports the package.
        """
        import ast
        import inspect
        tree = ast.parse(inspect.getsource(_cfg))
        top_level = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
        names = [a.name for n in top_level if isinstance(n, ast.Import) for a in n.names]
        names += [n.module or "" for n in top_level if isinstance(n, ast.ImportFrom)]
        self.assertNotIn(
            "markdown_vault.core.state_fs", names,
            "config must import state_fs inside the writing functions, not at module level: "
            "state_fs imports config, so this closes an import cycle.")

    def test_load_vaults_reads_the_cache_global_only_once(self):
        """load_vaults must bind the cache to a LOCAL before using it.

        Reading the global twice — once to test for None, once to copy — leaves a window in
        which ``_invalidate_cache`` (which ``save_settings`` calls on every settings change)
        nulls it, so the copy hits ``list(None)``. Reachable since the semantic index's
        background thread started calling this through StateFS's guard on every write.

        Checked structurally because it cannot be checked behaviourally: the window is a
        single bytecode boundary. A threaded probe — one thread looping load_vaults, another
        looping _invalidate_cache 20000 times — was tried against the unfixed code and never
        fired, so as a regression guard it would have been worthless while looking like
        proof. The shape IS the property here.
        """
        import ast
        import inspect
        tree = ast.parse(inspect.getsource(_cfg.load_vaults).lstrip())
        reads = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Name) and n.id == "_vaults_cache"
                 and isinstance(n.ctx, ast.Load)]
        self.assertLessEqual(
            len(reads), 1,
            "load_vaults reads the _vaults_cache global more than once; an invalidation "
            "between the reads makes it list(None). Bind it to a local first.")


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
        _cfg.set_setting(settings, "autosave.interval", 999)
        _cfg.set_setting(settings, "editor.font_size", 22)
        _cfg.save_settings(settings)
        _cfg.save_vaults([{"name": "Notes", "path": "/tmp/notes"}])
        loaded = _cfg.load_settings()
        self.assertEqual(_cfg.get_setting(loaded, "autosave.interval"), 999)
        self.assertEqual(_cfg.get_setting(loaded, "editor.font_size"), 22)

    def test_save_vaults_logs_when_existing_file_is_corrupt(self):
        # A corrupt settings.yaml can't be merged, so the sibling settings block is
        # dropped on the next write — that silent partial loss must be diagnosable.
        _cfg.CONFIG_FILE.write_text("{{invalid yaml::", encoding="utf-8")
        with self.assertLogs("markdown_vault.core.config", level="WARNING"):
            _cfg.save_vaults([{"name": "V", "path": "/tmp/v"}])

    def test_save_settings_logs_when_existing_file_is_corrupt(self):
        _cfg.CONFIG_FILE.write_text("{{invalid yaml::", encoding="utf-8")
        with self.assertLogs("markdown_vault.core.config", level="WARNING"):
            _cfg.save_settings({"autosave": {"interval": 30}})


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
        _cfg.set_setting(a, "editor.font_size", 21)
        self.assertEqual(_cfg.get_setting(b, "editor.font_size"), 21)

    def test_saving_needs_no_argument(self):
        _cfg.set_setting(_cfg.settings(), "autosave.interval", 99)
        _cfg.save_settings()
        self.assertEqual(_cfg.get_setting(_cfg.load_settings(), "autosave.interval"), 99)

    def test_a_second_writer_cannot_undo_the_first(self):
        # The lost update this ticket is about: two independent changes, one file.
        _cfg.set_setting(_cfg.settings(), "autosave.interval", 60)   # e.g. app window
        _cfg.save_settings()
        _cfg.set_setting(_cfg.settings(), "editor.font_size", 18)    # e.g. the dialog
        _cfg.save_settings()
        written = _cfg.load_settings()
        self.assertEqual(_cfg.get_setting(written, "autosave.interval"), 60)
        self.assertEqual(_cfg.get_setting(written, "editor.font_size"), 18)

    def test_load_settings_still_returns_an_independent_copy(self):
        # It stays available for tests and one-off reads — but it is a copy, so a
        # caller cannot accidentally treat it as the app's live state.
        copy = _cfg.load_settings()
        _cfg.set_setting(copy, "editor.font_size", 99)
        self.assertNotEqual(_cfg.get_setting(_cfg.settings(), "editor.font_size"), 99)

    def test_reload_replaces_the_contents_not_the_object(self):
        # Callers hold the object for the process lifetime; handing out a new one
        # on reload would resurrect exactly the stale-snapshot problem.
        live = _cfg.settings()
        _cfg.set_setting(live, "autosave.interval", 60)
        _cfg.save_settings()
        _cfg.CONFIG_FILE.write_text("settings:\n  autosave:\n    interval: 5\n",
                                    encoding="utf-8")
        _cfg.reload_settings()
        self.assertIs(_cfg.settings(), live)
        self.assertEqual(_cfg.get_setting(live, "autosave.interval"), 5)


class TestSettings(_TempConfigMixin, unittest.TestCase):
    """Tests for ``load_settings`` and ``save_settings``."""

    def test_load_settings_defaults_when_no_file(self):
        s = _cfg.load_settings()
        self.assertEqual(_cfg.get_setting(s, "autosave.interval"), 30)
        self.assertEqual(_cfg.get_setting(s, "editor.font_size"), 14)
        self.assertEqual(_cfg.get_setting(s, "editor.tab_width"), 4)
        self.assertTrue(_cfg.get_setting(s, "editor.wrap_text"))
        self.assertAlmostEqual(_cfg.get_setting(s, "preview.zoom"), 1.0)
        self.assertEqual(_cfg.get_setting(s, "view.default_mode"), "edit")

    def test_load_settings_on_corrupt_yaml(self):
        _cfg.CONFIG_FILE.write_text("{{bad yaml", encoding="utf-8")
        s = _cfg.load_settings()
        self.assertEqual(_cfg.get_setting(s, "autosave.interval"), 30)

    def test_ask_num_ctx_default_and_round_trip(self):
        self.assertEqual(_cfg.default("ask.num_ctx"), 8192)
        self.assertEqual(_cfg.get_setting(_cfg.load_settings(), "ask.num_ctx"), 8192)
        settings = _cfg.load_settings()
        _cfg.set_setting(settings, "ask.num_ctx", 16384)
        _cfg.save_settings(settings)
        self.assertEqual(_cfg.get_setting(_cfg.load_settings(), "ask.num_ctx"), 16384)

    def test_ask_top_k_default_and_round_trip(self):
        self.assertEqual(_cfg.default("ask.top_k"), 10)
        self.assertEqual(_cfg.get_setting(_cfg.load_settings(), "ask.top_k"), 10)
        settings = _cfg.load_settings()
        _cfg.set_setting(settings, "ask.top_k", 5)
        _cfg.save_settings(settings)
        self.assertEqual(_cfg.get_setting(_cfg.load_settings(), "ask.top_k"), 5)

    def test_save_and_load_round_trip(self):
        settings = _cfg.load_settings()
        _cfg.set_setting(settings, "autosave.interval", 60)
        _cfg.set_setting(settings, "editor.font_size", 18)
        _cfg.set_setting(settings, "editor.tab_width", 2)
        _cfg.set_setting(settings, "editor.wrap_text", False)
        _cfg.set_setting(settings, "preview.zoom", 1.5)
        _cfg.save_settings(settings)
        loaded = _cfg.load_settings()
        self.assertEqual(_cfg.get_setting(loaded, "autosave.interval"), 60)
        self.assertEqual(_cfg.get_setting(loaded, "editor.font_size"), 18)
        self.assertEqual(_cfg.get_setting(loaded, "editor.tab_width"), 2)
        self.assertFalse(_cfg.get_setting(loaded, "editor.wrap_text"))
        self.assertAlmostEqual(_cfg.get_setting(loaded, "preview.zoom"), 1.5)

    def test_save_settings_preserves_vaults(self):
        _cfg.add_vault("Notes", "/tmp/notes")
        settings = _cfg.load_settings()
        _cfg.set_setting(settings, "autosave.interval", 120)
        _cfg.save_settings(settings)
        vaults = _cfg.load_vaults()
        self.assertEqual(len(vaults), 1)
        self.assertEqual(vaults[0]["name"], "Notes")

    def test_save_settings_creates_dir(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        _cfg.save_settings({"autosave": {"interval": 10}})
        s = _cfg.load_settings()
        self.assertEqual(_cfg.get_setting(s, "autosave.interval"), 10)

    def test_load_settings_empty_yaml_key(self):
        _cfg.CONFIG_FILE.write_text("settings:\n", encoding="utf-8")
        s = _cfg.load_settings()
        self.assertEqual(_cfg.get_setting(s, "autosave.interval"), 30)

    def test_no_tmp_files_after_save(self):
        _cfg.save_vaults([{"name": "A", "path": "/tmp/a"}])
        tmp_files = list(Path(self._tmpdir).glob("*.tmp"))
        self.assertEqual(len(tmp_files), 0)


class TestWebkitEnv(_TempConfigMixin, unittest.TestCase):
    """Tests for ``apply_webkit_env`` (WebKit VRAM/GPU switches)."""

    def test_webkit_defaults_are_off(self):
        s = _cfg.load_settings()
        self.assertFalse(_cfg.get_setting(s, "webkit.disable_dmabuf"))
        self.assertFalse(_cfg.get_setting(s, "webkit.disable_compositing"))

    def test_apply_webkit_env_sets_both_when_true(self):
        import os
        from unittest import mock

        settings = {"webkit": {"disable_dmabuf": True, "disable_compositing": True}}
        with mock.patch.dict(os.environ, {}, clear=True):
            _cfg.apply_webkit_env(settings)
            self.assertEqual(os.environ["WEBKIT_DISABLE_DMABUF_RENDERER"], "1")
            self.assertEqual(os.environ["WEBKIT_DISABLE_COMPOSITING_MODE"], "1")

    def test_apply_webkit_env_leaves_unset_when_false(self):
        import os
        from unittest import mock

        settings = {"webkit": {"disable_dmabuf": False, "disable_compositing": False}}
        with mock.patch.dict(os.environ, {}, clear=True):
            _cfg.apply_webkit_env(settings)
            self.assertNotIn("WEBKIT_DISABLE_DMABUF_RENDERER", os.environ)
            self.assertNotIn("WEBKIT_DISABLE_COMPOSITING_MODE", os.environ)

    def test_apply_webkit_env_loads_settings_when_none(self):
        import os
        from unittest import mock

        settings = _cfg.load_settings()
        _cfg.set_setting(settings, "webkit.disable_dmabuf", True)
        _cfg.save_settings(settings)
        with mock.patch.dict(os.environ, {}, clear=True):
            _cfg.apply_webkit_env()
            self.assertEqual(os.environ["WEBKIT_DISABLE_DMABUF_RENDERER"], "1")
            self.assertNotIn("WEBKIT_DISABLE_COMPOSITING_MODE", os.environ)


class TestDefaultAndMigration(unittest.TestCase):
    def test_default_returns_builtin(self):
        self.assertEqual(_cfg.default("ask.server.model"), "llama3.2")
        self.assertEqual(_cfg.default("ask.backend"), "local")
        self.assertEqual(_cfg.default("ask.engine"), "auto")

    def test_semantic_openai_defaults(self):
        # The embedding side gains an OpenAI-compatible backend, mirroring Ask.
        self.assertEqual(_cfg.default("semantic.openai.url"), "http://localhost:8080")
        # default() returns "" for unknown keys too, so prove the key is registered.
        self.assertIn("model", _cfg._DEFAULT_SETTINGS["semantic"]["openai"])
        self.assertEqual(_cfg.default("semantic.openai.model"), "")

    def test_semantic_api_key_is_a_secret(self):
        # Defence in depth: the key lives in the keyring, but a hand-edited
        # settings.yaml plaintext value must be masked in the debug dump, like
        # ask_api_key.
        self.assertTrue(_cfg._is_secret("semantic.openai.api_key"))

    def test_kv_cache_defaults(self):
        self.assertEqual(_cfg.default("ask.local.kv_type_k"), "f16")
        self.assertEqual(_cfg.default("ask.local.kv_type_v"), "f16")
        self.assertFalse(_cfg.default("ask.local.flash_attn"))
        self.assertTrue(_cfg.default("ask.local.use_mmap"))
        self.assertEqual(_cfg.default("ask.max_tokens"), 1024)

    def test_model_filename_from_url(self):
        self.assertEqual(
            _cfg.model_filename_from_url("https://h/x/Foo-Q4_K_M.gguf"),
            "Foo-Q4_K_M.gguf")
        self.assertEqual(_cfg.model_filename_from_url("https://h/x/nope"),
                         "model.gguf")

    def test_resolve_drops_absolute_path_tolerance(self):
        # A hand-edited absolute path is not honoured: only its basename inside
        # ask.gguf.dir is loaded (the "filename" name is now honest).
        d = tempfile.mkdtemp()
        (Path(d) / "m.gguf").write_bytes(b"GGUF\x00\x00\x00\x00")
        try:
            s = {"ask": {"gguf": {"dir": d, "filename": "/somewhere/else/m.gguf"}}}
            self.assertEqual(_cfg.resolve_model_path(s), str(Path(d) / "m.gguf"))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_is_onnx_accepts_a_protobuf_start_and_rejects_a_page(self):
        # ONNX is protobuf and has no ASCII magic like GGUF's. Every model checked on this
        # machine starts 08 <varint> — field 1 (ir_version), which protobuf writers emit
        # first — and that is enough to separate a model from what actually turns up by
        # mistake: an HTML error page, a captive-portal login, a JSON error body. Without
        # it those reach a NATIVE parser, which this module elsewhere calls a memory-safety
        # surface rather than a parse error.
        import tempfile
        from pathlib import Path
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        good = Path(d) / "model.onnx"
        good.write_bytes(bytes([0x08, 0x06, 0x12, 0x07]) + b"pytorch")
        for name, content in (("page.onnx", b"<!DOCTYPE html><html>"),
                              ("err.onnx", b'{"error": "not found"}'),
                              ("text.onnx", b"Not Found\n")):
            with self.subTest(content=name):
                bad = Path(d) / name
                bad.write_bytes(content)
                self.assertFalse(_cfg.is_onnx(bad))
        self.assertTrue(_cfg.is_onnx(good))
        self.assertFalse(_cfg.is_onnx(Path(d) / "missing.onnx"))
        (Path(d) / "empty.onnx").write_bytes(b"")
        self.assertFalse(_cfg.is_onnx(Path(d) / "empty.onnx"))

    def test_is_tokenizer_json_wants_a_tokenizer_not_merely_json(self):
        # Parsing alone lets the failure it is meant to catch straight through: a server's
        # JSON error body IS valid JSON. Requiring the "model" key — the tokenizer
        # algorithm, present in every HF tokenizer.json — separates the two. Named for what
        # it checks, since it is no longer a generic JSON test.
        import tempfile
        from pathlib import Path
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        good = Path(d) / "tokenizer.json"
        good.write_text('{"version": "1.0", "model": {"type": "BPE"}}', encoding="utf-8")
        self.assertTrue(_cfg.is_tokenizer_json(good))
        for name, content in (("page.json", "<!DOCTYPE html><html>"),
                              ("err.json", '{"error": "not found"}'),
                              ("list.json", '["not", "an", "object"]'),
                              ("cut.json", '{"version": "1.0", "mod')):
            with self.subTest(content=name):
                bad = Path(d) / name
                bad.write_text(content, encoding="utf-8")
                self.assertFalse(_cfg.is_tokenizer_json(bad))
        self.assertFalse(_cfg.is_tokenizer_json(Path(d) / "missing.json"))

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
        # An explicit ask.gguf.dir is searched instead of the shared models_dir.
        self.assertEqual(
            [p.name for p in _cfg.list_models({"ask": {"gguf": {"dir": str(d)}}})],
            ["real.gguf"])

    def test_ask_models_dir_defaults_to_models_dir(self):
        self.assertEqual(_cfg.ask_models_dir({}), _cfg.models_dir())
        self.assertEqual(_cfg.ask_models_dir({"ask": {"gguf": {"dir": ""}}}),
                         _cfg.models_dir())

    def test_ask_models_dir_uses_the_configured_folder(self):
        from pathlib import Path
        self.assertEqual(
            _cfg.ask_models_dir({"ask": {"gguf": {"dir": "/custom/models"}}}),
            Path("/custom/models"))

    def test_resolve_reassembles_folder_and_filename(self):
        import tempfile
        from pathlib import Path
        d = Path(tempfile.mkdtemp())
        (d / "m.gguf").write_bytes(b"GGUF\x00\x00")
        s = {"ask": {"gguf": {"dir": str(d), "filename": "m.gguf"}}}
        self.assertEqual(_cfg.resolve_model_path(s), str(d / "m.gguf"))

    def test_resolve_set_but_unresolvable_returns_empty(self):
        # A chosen model that is gone yields "" — the caller blocks and names the
        # wanted model; resolve does NOT leak the raw value or silently switch.
        import tempfile
        from pathlib import Path
        d = Path(tempfile.mkdtemp())               # empty folder, no fallback either
        s = {"ask": {"gguf": {"dir": str(d), "filename": "gone.gguf"}}}
        self.assertEqual(_cfg.resolve_model_path(s), "")

    def test_resolve_empty_choice_falls_back_to_newest(self):
        import tempfile
        from pathlib import Path
        d = Path(tempfile.mkdtemp())
        (d / "m.gguf").write_bytes(b"GGUF\x00\x00")
        s = {"ask": {"gguf": {"dir": str(d)}}}     # no gguf.path → newest
        self.assertEqual(_cfg.resolve_model_path(s), str(d / "m.gguf"))

    def test_resolve_empty_folder_and_choice_returns_empty(self):
        import tempfile
        from pathlib import Path
        d = Path(tempfile.mkdtemp())
        self.assertEqual(
            _cfg.resolve_model_path({"ask": {"gguf": {"dir": str(d)}}}), "")

    def test_wanted_path_names_a_gone_choice(self):
        # resolve_model_path returns "" for a gone choice, but the error banner
        # needs the wanted name — this keeps it.
        from pathlib import Path
        s = {"ask": {"gguf": {"dir": "/m", "filename": "gone.gguf"}}}
        self.assertEqual(_cfg.ask_gguf_wanted_path(s), str(Path("/m") / "gone.gguf"))

    def test_wanted_path_empty_choice_uses_resolve(self):
        import tempfile
        from pathlib import Path
        d = Path(tempfile.mkdtemp())
        (d / "m.gguf").write_bytes(b"GGUF\x00\x00")
        self.assertEqual(
            _cfg.ask_gguf_wanted_path({"ask": {"gguf": {"dir": str(d)}}}),
            str(d / "m.gguf"))

    def test_default_unknown_key_is_empty(self):
        self.assertEqual(_cfg.default("no_such_key"), "")

    def test_migrate_derives_onnx_dir_from_old_model(self):
        s = {"semantic": {"onnx": {"dir": ""}},
             "semantic_onnx_model": "/x/y/model.onnx"}
        _cfg._migrate_settings(s)
        self.assertEqual(_cfg.get_setting(s, "semantic.onnx.dir"), "/x/y")

    def test_migrate_keeps_existing_dir(self):
        s = {"semantic": {"onnx": {"dir": "/keep"}},
             "semantic_onnx_model": "/x/y/model.onnx"}
        _cfg._migrate_settings(s)
        self.assertEqual(_cfg.get_setting(s, "semantic.onnx.dir"), "/keep")

    def test_migrate_noop_without_old_keys(self):
        s = {"semantic": {"onnx": {"dir": ""}}}
        _cfg._migrate_settings(s)
        self.assertEqual(_cfg.get_setting(s, "semantic.onnx.dir"), "")


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
        _cfg.save_settings({"autosave": {"interval": 30}, "log": {"level": "info"}})
        with self.assertLogs("markdown_vault.core.config", level="INFO") as cm:
            _cfg.save_settings({"autosave": {"interval": 90}, "log": {"level": "info"}})
        blob = "\n".join(cm.output)
        self.assertIn("autosave.interval", blob)
        self.assertIn("90", blob)
        self.assertNotIn("log.level", blob)     # unchanged leaves are not noise

    def test_secret_values_are_masked(self):
        _cfg.save_settings({})
        with self.assertLogs("markdown_vault.core.config", level="INFO") as cm:
            _cfg.save_settings({"ask": {"api_key": "sk-super-secret"}})
        blob = "\n".join(cm.output)
        self.assertNotIn("sk-super-secret", blob)
        self.assertIn("ask.api_key", blob)

    def test_dropping_keys_warns(self):
        # Exactly the shape of the leaked-timer write: a tiny snapshot replacing a
        # full settings block. It must not pass silently — and a dropped *nested
        # leaf* must be seen even though the top-level branch set is unchanged.
        _cfg.save_settings({"autosave": {"interval": 30}, "log": {"level": "debug"},
                            "semantic": {"enabled": True}})
        with self.assertLogs("markdown_vault.core.config", level="WARNING") as cm:
            _cfg.save_settings({"ask": {"system_prompt": ""}})
        blob = "\n".join(cm.output)
        self.assertIn("semantic.enabled", blob)
        self.assertIn("log.level", blob)

    def test_log_names_the_caller(self):
        # "which keys" without "from where" leaves the five writers indistinguishable —
        # exactly the question the reset investigation could not answer.
        _cfg.save_settings({"autosave": {"interval": 30}})
        with self.assertLogs("markdown_vault.core.config", level="INFO") as cm:
            _cfg.save_settings({"autosave": {"interval": 90}})
        self.assertIn("test_config.py:", "\n".join(cm.output))

    def test_drop_warning_names_the_caller(self):
        _cfg.save_settings({"autosave": {"interval": 30}, "log": {"level": "debug"}})
        with self.assertLogs("markdown_vault.core.config", level="WARNING") as cm:
            _cfg.save_settings({"autosave": {"interval": 30}})
        self.assertIn("test_config.py:", "\n".join(cm.output))

    def test_no_log_when_nothing_changed(self):
        _cfg.save_settings({"autosave": {"interval": 30}})
        with self.assertNoLogs("markdown_vault.core.config", level="INFO"):
            _cfg.save_settings({"autosave": {"interval": 30}})


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


class TestNestedAccessors(unittest.TestCase):
    """The dotted-path accessors over the nested settings tree."""

    def test_get_setting_reads_nested_path(self):
        s = {"ask": {"server": {"url": "http://x"}}}
        self.assertEqual(_cfg.get_setting(s, "ask.server.url"), "http://x")

    def test_get_setting_missing_returns_default(self):
        self.assertEqual(_cfg.get_setting({}, "ask.server.url", "d"), "d")
        self.assertIsNone(_cfg.get_setting({"ask": {}}, "ask.server.url"))

    def test_set_setting_creates_branches(self):
        s = {}
        _cfg.set_setting(s, "ask.server.url", "http://y")
        self.assertEqual(s, {"ask": {"server": {"url": "http://y"}}})

    def test_set_setting_keeps_siblings(self):
        s = {"ask": {"server": {"url": "a", "model": "m"}}}
        _cfg.set_setting(s, "ask.server.url", "b")
        self.assertEqual(s["ask"]["server"], {"url": "b", "model": "m"})

    def test_default_reads_nested_default(self):
        self.assertEqual(_cfg.default("autosave.interval"), 30)
        self.assertEqual(_cfg.default("ask.server.url"), "http://localhost:11434")

    def test_default_unknown_returns_empty_string(self):
        self.assertEqual(_cfg.default("nope.nothing"), "")

    def test_flatten_produces_dotted_leaves(self):
        flat = _cfg._flatten({"a": {"b": 1, "c": {"d": 2}}, "e": 3})
        self.assertEqual(flat, {"a.b": 1, "a.c.d": 2, "e": 3})

    def test_flatten_stops_at_opaque_leaves(self):
        s = {"ask": {"server": {"model_by_endpoint": {"ollama|x": "m"}}}}
        self.assertEqual(
            _cfg._flatten(s),
            {"ask.server.model_by_endpoint": {"ollama|x": "m"}})

    def test_opaque_leaves_default_to_empty(self):
        # _deep_merge relies on this: with an empty default, merging a user's dict
        # is the same as replacing it. A non-empty default would silently resurrect
        # entries the user deleted by hand.
        for path in _cfg._OPAQUE_LEAVES:
            self.assertEqual(_cfg.default(path), {})

    def test_deep_merge_keeps_sibling_defaults(self):
        base = {"ask": {"top_k": 10, "server": {"url": "d"}}}
        _cfg._deep_merge(base, {"ask": {"top_k": 12}})
        self.assertEqual(base, {"ask": {"top_k": 12, "server": {"url": "d"}}})

    def test_default_settings_not_shared(self):
        # A nested branch must be deep-copied, never aliased to the module default.
        a = _cfg.load_settings()
        _cfg.set_setting(a, "ask.top_k", 999)
        self.assertEqual(_cfg.default("ask.top_k"), 10)


class TestNestedMechanisms(_TempConfigMixin, unittest.TestCase):
    """The five silently-degrading mechanisms, moved to flattened leaves."""

    def test_dropped_nested_leaf_still_warns(self):
        # url dropped, but the top-level 'ask' branch stays: the exact case the old
        # top-level diff would miss (set(before) == set(after)).
        before = {"ask": {"server": {"url": "x", "model": "m"}}}
        after = {"ask": {"server": {"model": "m"}}}
        self.assertEqual(set(before), set(after))
        with self.assertLogs("markdown_vault.core.config", level="WARNING") as cm:
            _cfg._log_settings_write(before, after)
        self.assertIn("ask.server.url", "\n".join(cm.output))

    def test_changed_nested_leaf_reported_as_leaf(self):
        before = {"ask": {"top_k": 10}}
        after = {"ask": {"top_k": 12}}
        with self.assertLogs("markdown_vault.core.config", level="INFO") as cm:
            _cfg._log_settings_write(before, after)
        joined = "\n".join(cm.output)
        self.assertIn("ask.top_k", joined)   # the leaf, not a whole-branch diff

    def test_nested_secret_is_masked_in_debug_dump(self):
        _cfg.CONFIG_FILE.write_text(
            "settings:\n  ask:\n    api_key: supersecret\n", encoding="utf-8")
        _cfg._last_logged_settings = None
        with self.assertLogs("markdown_vault.core.config", level="DEBUG") as cm:
            _cfg.load_settings()
        joined = "\n".join(cm.output)
        self.assertNotIn("supersecret", joined)
        self.assertIn("***", joined)

    def test_existing_migration_works_against_nested(self):
        s = {"semantic_onnx_model": "/models/m.onnx"}
        _cfg._migrate_settings(s)
        self.assertEqual(_cfg.get_setting(s, "semantic.onnx.dir"), "/models")

    def test_webkit_env_exports_from_nested_leaf(self):
        import os as _os
        _os.environ.pop("WEBKIT_DISABLE_DMABUF_RENDERER", None)
        self.addCleanup(_os.environ.pop, "WEBKIT_DISABLE_DMABUF_RENDERER", None)
        _cfg.apply_webkit_env({"webkit": {"disable_dmabuf": True}})
        self.assertEqual(_os.environ.get("WEBKIT_DISABLE_DMABUF_RENDERER"), "1")

    def test_load_deep_merges_partial_branch(self):
        # A user file overriding one ask leaf keeps the branch's other defaults.
        _cfg.CONFIG_FILE.write_text(
            "settings:\n  ask:\n    top_k: 3\n", encoding="utf-8")
        s = _cfg.load_settings()
        self.assertEqual(_cfg.get_setting(s, "ask.top_k"), 3)
        self.assertEqual(_cfg.get_setting(s, "ask.server.url"), "http://localhost:11434")


class TestSettingsValidation(_TempConfigMixin, unittest.TestCase):
    """Warn (never block) on an invalid settings.yaml at load, against the schema."""

    _LOG = "markdown_vault.core.config"

    def _write(self, body):
        _cfg.CONFIG_FILE.write_text("settings:\n" + body, encoding="utf-8")

    def test_unknown_path_warns_and_names_the_file(self):
        self._write("  ask:\n    bogus_leaf: 1\n")
        with self.assertLogs(self._LOG, "WARNING") as cm:
            s = _cfg.load_settings()
        blob = "\n".join(cm.output)
        self.assertIn("ask.bogus_leaf", blob)
        self.assertIn(str(_cfg.CONFIG_FILE), blob)          # tells the user where to edit
        self.assertEqual(_cfg.get_setting(s, "ask.bogus_leaf"), 1)   # survives — no data loss

    def test_wrong_type_warns_and_resets_to_default(self):
        self._write("  ask:\n    top_k: ten\n")
        with self.assertLogs(self._LOG, "WARNING") as cm:
            s = _cfg.load_settings()
        self.assertIn("ask.top_k", "\n".join(cm.output))
        self.assertEqual(_cfg.get_setting(s, "ask.top_k"), 10)

    def test_invalid_enum_warns_and_resets(self):
        self._write("  ask:\n    engine: automatic\n")
        with self.assertLogs(self._LOG, "WARNING") as cm:
            s = _cfg.load_settings()
        self.assertIn("ask.engine", "\n".join(cm.output))
        self.assertEqual(_cfg.get_setting(s, "ask.engine"), "auto")

    def test_invalid_log_level_warns_and_defaults(self):
        # The setting that configures logging still produces a visible warning.
        self._write("  log:\n    level: verbose\n")
        with self.assertLogs(self._LOG, "WARNING") as cm:
            s = _cfg.load_settings()
        self.assertIn("log.level", "\n".join(cm.output))
        self.assertEqual(_cfg.get_setting(s, "log.level"), "info")

    def test_integer_satisfies_a_number_leaf(self):
        # preview.zoom is a "number"; an integer 2 is a valid number, not a warning.
        self._write("  preview:\n    zoom: 2\n")
        with self.assertNoLogs(self._LOG, "WARNING"):
            s = _cfg.load_settings()
        self.assertEqual(_cfg.get_setting(s, "preview.zoom"), 2)

    def test_debug_flags_are_type_validated(self):
        self._write("  debug:\n    active: 3\n    dump:\n      tabs: 3\n")
        with self.assertLogs(self._LOG, "WARNING") as cm:
            _cfg.load_settings()
        blob = "\n".join(cm.output)
        self.assertIn("debug.active", blob)
        self.assertIn("debug.dump.tabs", blob)

    def test_debug_dump_open_key_with_boolean_is_ok(self):
        # An open map: any component name is valid, only the boolean value matters.
        self._write("  debug:\n    dump:\n      a_new_component: true\n")
        with self.assertNoLogs(self._LOG, "WARNING"):
            _cfg.load_settings()

    def test_valid_settings_emit_no_validation_warning(self):
        self._write("  ask:\n    top_k: 5\n  editor:\n    font_size: 16\n")
        with self.assertNoLogs(self._LOG, "WARNING"):
            _cfg.load_settings()

    def test_missing_schema_skips_validation_without_crashing(self):
        from unittest import mock
        self._write("  ask:\n    bogus_leaf: 1\n")
        with mock.patch.object(_cfg, "_load_schema", return_value=None):
            with self.assertNoLogs(self._LOG, "WARNING"):
                s = _cfg.load_settings()
        self.assertEqual(_cfg.get_setting(s, "ask.bogus_leaf"), 1)


if __name__ == "__main__":
    unittest.main()
