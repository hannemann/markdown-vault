"""Tests for markdown_vault.ui.preferences.dialog — PreferencesDialog behaviour.

Characterisation tests (R119.2 / ticket A): construct the real Adw dialog headless
with ``config`` and the ``search``/``importers`` boundary mocked, and pin the stable
surface — the pages build, a setting change persists and emits ``settings-changed``,
values are read back from config on open, the four Search subpages construct, and an
unknown persisted backend still opens. This pins behaviour a later split (ticket B)
must keep; it deliberately does not test ``_build_*`` method names.
"""
import unittest
from unittest.mock import patch, MagicMock

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw  # type: ignore[attr-defined]

Adw.init()

# Import the boundary submodules so patch() can resolve them as package attributes —
# the packages have empty __init__.py, so the submodules are not attributes until
# imported. These imports are light (the heavy AI deps are function-local).
import markdown_vault.search.llama_runtime  # noqa: F401,E402
import markdown_vault.search.ask  # noqa: F401,E402
import markdown_vault.importers.document_import  # noqa: F401,E402
from markdown_vault.ui.preferences.dialog import PreferencesDialog  # noqa: E402
from markdown_vault.core import config  # noqa: E402
from gi.repository import GLib, Gtk  # noqa: E402
from markdown_vault.ui.preferences.ask_subpage import AskSubpageMixin  # noqa: E402
from markdown_vault.ui.preferences.embedding_subpage import EmbeddingSubpageMixin  # noqa: E402


def _dialog_error():
    return GLib.Error.new_literal(GLib.quark_from_string("test"), "boom", 0)


class TestPreferencesFolderChooserFailure(unittest.TestCase):
    """Seam guard: the two Preferences folder-chooser callbacks split a user cancel
    from a real dialog failure — silent on cancel, log + error dialog on a real
    failure. The shared helper is patched here (it has its own tests in
    test_dialogs); this pins the *caller's* response to each branch."""

    @patch("markdown_vault.ui.preferences.ask_subpage.dialogs")
    def test_models_dir_cancel_is_silent(self, mock_dialogs):
        mock_dialogs.dialog_cancelled.return_value = True
        me = MagicMock()
        dlg = MagicMock()
        dlg.select_folder_finish.side_effect = _dialog_error()
        with self.assertNoLogs("markdown_vault.ui.preferences.ask_subpage", level="WARNING"):
            AskSubpageMixin._on_models_dir_chosen(me, dlg, None)
        me._on_models_dir_selected.assert_not_called()
        mock_dialogs.show_error.assert_not_called()

    @patch("markdown_vault.ui.preferences.ask_subpage.dialogs")
    def test_models_dir_failure_logs_and_shows_error(self, mock_dialogs):
        mock_dialogs.dialog_cancelled.return_value = False
        me = MagicMock()
        dlg = MagicMock()
        dlg.select_folder_finish.side_effect = _dialog_error()
        with self.assertLogs("markdown_vault.ui.preferences.ask_subpage", level="WARNING"):
            AskSubpageMixin._on_models_dir_chosen(me, dlg, None)
        mock_dialogs.show_error.assert_called_once()

    @patch("markdown_vault.ui.preferences.embedding_subpage.dialogs")
    def test_onnx_dir_cancel_is_silent(self, mock_dialogs):
        mock_dialogs.dialog_cancelled.return_value = True
        me = MagicMock()
        dlg = MagicMock()
        dlg.select_folder_finish.side_effect = _dialog_error()
        with self.assertNoLogs("markdown_vault.ui.preferences.embedding_subpage", level="WARNING"):
            EmbeddingSubpageMixin._on_onnx_dir_chosen(me, dlg, None)
        me._on_onnx_dir_selected.assert_not_called()
        mock_dialogs.show_error.assert_not_called()

    @patch("markdown_vault.ui.preferences.embedding_subpage.dialogs")
    def test_onnx_dir_failure_logs_and_shows_error(self, mock_dialogs):
        mock_dialogs.dialog_cancelled.return_value = False
        me = MagicMock()
        dlg = MagicMock()
        dlg.select_folder_finish.side_effect = _dialog_error()
        with self.assertLogs("markdown_vault.ui.preferences.embedding_subpage", level="WARNING"):
            EmbeddingSubpageMixin._on_onnx_dir_chosen(me, dlg, None)
        mock_dialogs.show_error.assert_called_once()


class TestOnnxRuntimeProbe(unittest.TestCase):
    """The onnxruntime status line distinguishes genuinely-absent from
    present-but-unloadable (ZZ6): the common broken-install case (wheel against the
    wrong native env) raises a PLAIN ImportError, not ModuleNotFoundError, and must
    not be reported as 'not found — install it'."""

    @patch("markdown_vault.ui.preferences.embedding_subpage.importlib.import_module")
    def test_absent_says_install_it(self, mock_import):
        mock_import.side_effect = ModuleNotFoundError("no onnxruntime")
        me = MagicMock()
        with self.assertNoLogs("markdown_vault.ui.preferences.embedding_subpage",
                               level="WARNING"):
            line = EmbeddingSubpageMixin._onnxruntime_status(me)
        self.assertIn("not found", line)

    @patch("markdown_vault.ui.preferences.embedding_subpage.importlib.import_module")
    def test_broken_native_import_says_failed_to_load_and_logs(self, mock_import):
        mock_import.side_effect = ImportError("libonnxruntime.so: cannot open")
        me = MagicMock()
        with self.assertLogs("markdown_vault.ui.preferences.embedding_subpage",
                             level="WARNING"):
            line = EmbeddingSubpageMixin._onnxruntime_status(me)
        self.assertIn("failed to load", line)

# The settings tree is nested; fixtures and assertions below still name settings
# the old flat way for readability, so map each flat name to its dotted path. The
# dialog reads/writes the nested tree via config.get_setting/set_setting.
_DOTTED = {
    "autosave_interval": "autosave.interval",
    "default_view_mode": "view.default_mode",
    "editor_font_size": "editor.font_size",
    "editor_tab_width": "editor.tab_width",
    "editor_wrap_text": "editor.wrap_text",
    "preview_zoom": "preview.zoom",
    "preview_allow_remote_images": "preview.allow_remote_images",
    "semantic_search_enabled": "semantic.enabled",
    "semantic_backend": "semantic.backend",
    "semantic_min_score": "semantic.min_score",
    "semantic_openai_url": "semantic.openai.url",
    "semantic_openai_model": "semantic.openai.model",
    "ask_engine": "ask.engine",
    "ask_backend": "ask.backend",
    "ask_reasoning": "ask.reasoning",
    "ask_hybrid": "ask.hybrid",
    "ask_top_k": "ask.top_k",
    "ask_num_ctx": "ask.num_ctx",
    "ask_max_tokens": "ask.max_tokens",
    "ask_system_prompt": "ask.system_prompt",
    "ask_ollama_url": "ask.server.url",
    "ask_model": "ask.server.model",
    "ask_models_dir": "ask.gguf.dir",
    "ask_n_gpu_layers": "ask.local.n_gpu_layers",
    "ask_n_threads": "ask.local.n_threads",
    "ask_flash_attn": "ask.local.flash_attn",
    "ask_use_mmap": "ask.local.use_mmap",
}


def _nest(flat):
    """Build the nested settings tree from flat legacy names."""
    tree = {}
    for key, value in flat.items():
        config.set_setting(tree, _DOTTED.get(key, key), value)
    return tree


class _DialogTest(unittest.TestCase):
    """Mock config + the search/importers boundary so the dialog builds headless
    without loading the AI stack (llama_cpp/onnxruntime)."""

    def setUp(self):
        self._patchers = []
        self._dialogs = []
        self.saved = []

        def _p(target):
            m = patch(target)
            self._patchers.append(m)
            return m.start()

        # Never fetch a real model list: construction and every backend/URL change
        # would otherwise start a network thread whose GLib.idle_add callback fires
        # in a later test, with these mocks already gone.
        m = patch.object(PreferencesDialog, "_refresh_ask_models")
        self._patchers.append(m)
        m.start()

        # The dialog reads the app's owned settings object (config.settings), not a
        # private copy — patch that, and hand each test its own dict.
        self.load = _p("markdown_vault.ui.preferences.dialog.config.settings")
        self.load.return_value = {}
        _p("markdown_vault.ui.preferences.dialog.config.save_settings").side_effect = (
            lambda s: self.saved.append(dict(s)))

        ask = _p("markdown_vault.search.ask")
        ask.DEFAULT_SYSTEM_PROMPT = "You are a helpful assistant."
        _p("markdown_vault.search.llama_runtime").gpu_layers_advice.return_value = ""
        doc = _p("markdown_vault.importers.document_import")
        doc.whisper_model_name.return_value = "base"
        doc.is_available.return_value = None
        doc.whisper_model_ready.return_value = True
        doc.SUPPORTED_SUFFIXES = [".pdf"]

        # Keyring: back it with an in-memory dict so no real Secret Service is touched.
        self.secrets = {}
        _p("markdown_vault.core.secret_store.available").return_value = True
        _p("markdown_vault.core.secret_store.get_secret").side_effect = (
            lambda k: self.secrets.get(k, ""))
        def _store(k, v):
            # Mirror secret_store.set_secret: an empty value CLEARS the entry
            # (password_clear_sync), it does not store "".
            if v:
                self.secrets[k] = v
            else:
                self.secrets.pop(k, None)
            return True                                   # write succeeded
        _p("markdown_vault.core.secret_store.set_secret").side_effect = _store

    def tearDown(self):
        # Cancel debounced writes BEFORE the patches go away. A pending
        # GLib.timeout from _persist_debounced / _on_secret_changed would
        # otherwise fire once some later test runs a main loop — by then
        # config.save_settings is the real one again, and it would persist this
        # dialog's tiny snapshot over the developer's actual settings.yaml,
        # wiping every setting not in it (that really happened).
        from gi.repository import GLib  # type: ignore[attr-defined]
        for dlg in self._dialogs:
            for attr in ("_persist_id", "_secret_persist_id", "_ask_models_id",
                         "_sem_oai_models_id"):
                source = getattr(dlg, attr, None)
                if source is not None:
                    GLib.source_remove(source)
                    setattr(dlg, attr, None)
            dlg._pending_secret = None
        self._dialogs.clear()
        for m in reversed(self._patchers):
            m.stop()

    def _dialog(self, **settings):
        self.load.return_value = _nest(settings)
        dlg = PreferencesDialog()
        self._dialogs.append(dlg)   # so tearDown can cancel its pending writes
        return dlg

    def _assert_persisted(self, dlg, key, value):
        dotted = _DOTTED.get(key, key)
        self.assertEqual(config.get_setting(dlg._settings, dotted), value)
        self.assertTrue(self.saved and config.get_setting(self.saved[-1], dotted) == value)


class TestConstruction(_DialogTest):
    """The dialog builds headless and carries the signal + all four subpages."""

    def test_constructs_headless(self):
        self.assertIsNotNone(self._dialog())

    def test_all_seven_pages_added_in_order(self):
        # Adw.PreferencesDialog has no page enumerator and its children are not
        # built until presented, so spy on add() during construction. Asserting the
        # list pins both presence and tab order — exactly what a split (ticket B)
        # that wires up a page module wrongly, or forgets one, would lose.
        seen = []
        real_add = Adw.PreferencesDialog.add

        def spy(dialog, page):
            seen.append(page.get_title())
            return real_add(dialog, page)

        with patch.object(Adw.PreferencesDialog, "add", spy):
            self._dialog()
        self.assertEqual(seen, ["General", "Editor", "Preview", "Web",
                                "Search", "Keyboard", "Debug"])

    def test_settings_changed_signal_registered(self):
        from gi.repository import GObject  # type: ignore[attr-defined]
        self.assertTrue(GObject.signal_lookup("settings-changed", PreferencesDialog))

    def test_four_search_subpages_built(self):
        dlg = self._dialog()
        for attr in ("_emb_subpage", "_prompt_subpage", "_runtime_subpage",
                     "_ask_subpage"):
            self.assertTrue(hasattr(dlg, attr), attr)


class TestSettingsPersist(_DialogTest):
    """A change updates the in-memory settings, persists, and emits the signal."""

    def test_autosave_change_persists_and_emits(self):
        dlg = self._dialog(autosave_interval=30)
        got = []
        dlg.connect("settings-changed", lambda _d: got.append(True))
        dlg._autosave_row.get_adjustment().set_value(90)
        self.assertEqual(config.get_setting(dlg._settings, "autosave.interval"), 90)
        self.assertTrue(self.saved and
                        config.get_setting(self.saved[-1], "autosave.interval") == 90)
        self.assertEqual(got, [True])

    def test_save_failure_shows_error_and_no_signal(self):
        dlg = self._dialog(autosave_interval=30)
        got = []
        dlg.connect("settings-changed", lambda _d: got.append(True))
        with patch("markdown_vault.ui.preferences.dialog.config.save_settings",
                   side_effect=OSError("disk full")), \
                patch("markdown_vault.ui.preferences.dialog.dialogs.show_error") as err:
            dlg._autosave_row.get_adjustment().set_value(90)
        err.assert_called_once()
        self.assertEqual(got, [])          # no signal on a failed write


class TestPersistAcrossPages(_DialogTest):
    """The change→persist→emit contract holds across pages, not just General."""

    def test_editor_font_size(self):
        dlg = self._dialog(editor_font_size=12)
        dlg._font_row.get_adjustment().set_value(18)
        self._assert_persisted(dlg, "editor_font_size", 18)

    def test_editor_tab_width(self):
        dlg = self._dialog(editor_tab_width=4)
        dlg._tab_row.get_adjustment().set_value(8)
        self._assert_persisted(dlg, "editor_tab_width", 8)

    def test_editor_word_wrap_toggle(self):
        dlg = self._dialog(editor_wrap_text=False)
        dlg._wrap_row.set_active(True)
        self._assert_persisted(dlg, "editor_wrap_text", True)

    def test_preview_zoom(self):
        dlg = self._dialog(preview_zoom=1.0)
        dlg._zoom_row.get_adjustment().set_value(1.5)
        self._assert_persisted(dlg, "preview_zoom", 1.5)

    def test_preview_remote_images_toggle(self):
        dlg = self._dialog(preview_allow_remote_images=False)
        dlg._remote_images_row.set_active(True)
        self._assert_persisted(dlg, "preview_allow_remote_images", True)


class TestReadBack(_DialogTest):
    """Persisted values are reflected in the widgets on open."""

    def test_autosave_value_read_from_config(self):
        dlg = self._dialog(autosave_interval=45)
        self.assertEqual(dlg._autosave_row.get_adjustment().get_value(), 45)

    def test_default_when_missing(self):
        dlg = self._dialog()               # empty settings
        self.assertEqual(dlg._autosave_row.get_adjustment().get_value(), 30)

    def test_editor_font_read_from_config(self):
        dlg = self._dialog(editor_font_size=20)
        self.assertEqual(dlg._font_row.get_adjustment().get_value(), 20)

    def test_preview_zoom_read_from_config(self):
        dlg = self._dialog(preview_zoom=1.25)
        self.assertEqual(dlg._zoom_row.get_adjustment().get_value(), 1.25)

    def test_word_wrap_read_from_config(self):
        dlg = self._dialog(editor_wrap_text=True)
        self.assertTrue(dlg._wrap_row.get_active())


class TestBackendTolerance(_DialogTest):
    """An unknown persisted backend must not crash the dialog (R22.10 defence)."""

    def test_known_backends(self):
        dlg = self._dialog(semantic_backend="ollama")
        self.assertEqual(dlg._sem_backend_index(), 1)

    def test_unknown_persisted_backend_still_opens(self):
        dlg = self._dialog(semantic_backend="a-backend-from-the-future")
        self.assertIsNotNone(dlg)          # __init__ must not raise
        self.assertEqual(dlg._sem_backend_index(), 0)


class TestOpenPage(_DialogTest):
    """open_page navigates to ANY named page by argument (not an if/elif of
    hardcoded targets), and optionally pushes one of its subpages."""

    def test_every_top_level_page_is_addressable_by_name(self):
        dlg = self._dialog()
        for name in ("general", "editor", "preview", "web", "search",
                     "keyboard", "debug"):
            dlg.open_page(name)
            self.assertEqual(dlg.get_visible_page().get_name(), name)

    def test_open_page_pushes_the_named_subpage(self):
        dlg = self._dialog()
        with patch.object(dlg, "push_subpage") as push:
            dlg.open_page("search", subpage="ask")
        self.assertEqual(dlg.get_visible_page().get_name(), "search")
        push.assert_called_once_with(dlg._subpages["ask"])

    def test_unknown_page_name_is_a_no_op(self):
        dlg = self._dialog()
        dlg.open_page("no-such-page")          # must not raise
        dlg.open_page("search", subpage="nope")  # unknown subpage ignored


class TestOpenAIEmbeddingBackend(_DialogTest):
    """The OpenAI-compatible embedding backend rows in the Embedding subpage."""

    def test_openai_is_the_third_backend(self):
        dlg = self._dialog(semantic_backend="openai")
        self.assertEqual(dlg._sem_backend_index(), 2)

    def test_selecting_openai_greys_the_other_backends(self):
        dlg = self._dialog(semantic_backend="openai")
        dlg._update_sem_backend_sensitivity()
        self.assertTrue(dlg._sem_openai_widgets[0].get_sensitive())
        self.assertFalse(dlg._sem_onnx_widgets[0].get_sensitive())
        self.assertFalse(dlg._sem_ollama_widgets[0].get_sensitive())

    def test_model_selection_updates_the_setting(self):
        # The chosen model goes into settings (a debounced save flushes it later,
        # the same shared path the Ask picker uses).
        dlg = self._dialog(semantic_backend="openai")
        dlg._sem_oai_model_list.splice(0, 0, ["bge-m3", "e5-large"])
        dlg._sem_oai_model_combo.set_selected(1)   # fires notify::selected
        self.assertEqual(config.get_setting(dlg._settings, "semantic.openai.model"),
                         "e5-large")

    def test_d6_unusable_shown_when_no_model_and_the_server_lists(self):
        dlg = self._dialog(semantic_backend="openai", semantic_openai_model="")
        dlg._sem_oai_no_list = False
        dlg._refresh_sem_openai_state()
        self.assertTrue(dlg._sem_oai_unusable_row.get_visible())

    def test_d6_unusable_hidden_for_a_no_list_server(self):
        # llama.cpp serves its one model regardless of the (empty) name → usable.
        dlg = self._dialog(semantic_backend="openai", semantic_openai_model="")
        dlg._sem_oai_no_list = True
        dlg._refresh_sem_openai_state()
        self.assertFalse(dlg._sem_oai_unusable_row.get_visible())

    def test_d6_unusable_hidden_when_a_model_is_set(self):
        dlg = self._dialog(semantic_backend="openai", semantic_openai_model="bge-m3")
        dlg._sem_oai_no_list = False
        dlg._refresh_sem_openai_state()
        self.assertFalse(dlg._sem_oai_unusable_row.get_visible())

    def test_key_uses_the_endpoint_scoped_embedding_name(self):
        # D2: the embedding key name carries the endpoint and is NOT the Ask name.
        dlg = self._dialog(semantic_backend="openai",
                           semantic_openai_url="http://h:8080/v1")
        self.assertEqual(dlg._sem_openai_secret_name(),
                         "semantic_api_key:openai|http://h:8080")

    def test_model_refresh_probes_without_writing_the_shared_status(self):
        # ZE1: the embedding model refresh must probe with record=False, or a
        # failure would write the shared verdict and mute Ask on the same server.
        dlg = self._dialog(semantic_backend="openai",
                           semantic_openai_url="http://h:8080")
        with patch(
                "markdown_vault.ui.preferences.embedding_subpage.threading.Thread") as T, \
             patch("markdown_vault.ui.preferences.embedding_subpage.GLib.idle_add"), \
             patch("markdown_vault.search.ask_models.probe") as probe:
            dlg._refresh_sem_openai_models()
            T.call_args.kwargs["target"]()          # run the worker the thread had
        self.assertFalse(probe.call_args.kwargs["record"])


class TestSearchAndAskHandlers(_DialogTest):
    """The Search page and its four subpages — the area ticket B will carve up, so
    its setting handlers are pinned before the split."""

    def test_semantic_min_score(self):
        dlg = self._dialog(semantic_min_score=0.35)
        dlg._sem_score_row.get_adjustment().set_value(0.6)
        self._assert_persisted(dlg, "semantic_min_score", 0.6)

    def test_gpu_layers(self):
        dlg = self._dialog(ask_n_gpu_layers=0)
        dlg._ask_gpu_row.get_adjustment().set_value(20)
        self._assert_persisted(dlg, "ask_n_gpu_layers", 20)

    def test_download_target_follows_the_configured_models_folder(self):
        # The dropdown lists ask_models_dir; downloading into models_dir() would
        # drop the file where nothing looks for it — the button would appear to
        # work and change nothing (the NN1 case).
        dlg = self._dialog(ask_models_dir="/models/custom")
        with patch("markdown_vault.ui.preferences.ask_subpage.threading.Thread") as T:
            dlg._on_download_gguf(dlg._ask_gguf_dl_btn)
        target = T.call_args.kwargs["args"][2]   # (button, url, target, name, bar)
        self.assertTrue(str(target).startswith("/models/custom"))

    def test_threads(self):
        dlg = self._dialog(ask_n_threads=4)
        dlg._ask_threads_row.get_adjustment().set_value(8)
        self._assert_persisted(dlg, "ask_n_threads", 8)

    def test_max_tokens(self):
        dlg = self._dialog(ask_max_tokens=512)
        dlg._ask_maxtok_row.get_adjustment().set_value(2048)
        self._assert_persisted(dlg, "ask_max_tokens", 2048)

    def test_top_k(self):
        dlg = self._dialog(ask_top_k=5)
        dlg._ask_topk_row.get_adjustment().set_value(15)   # range is [3, 20]
        self._assert_persisted(dlg, "ask_top_k", 15)

    def test_num_ctx(self):
        dlg = self._dialog(ask_num_ctx=2048)
        dlg._ask_ctx_row.get_adjustment().set_value(4096)
        self._assert_persisted(dlg, "ask_num_ctx", 4096)

    def test_flash_attention_toggle(self):
        dlg = self._dialog(ask_flash_attn=False)
        dlg._ask_flash_row.set_active(True)
        self._assert_persisted(dlg, "ask_flash_attn", True)

    def test_reasoning_toggle(self):
        dlg = self._dialog(ask_reasoning=False)
        dlg._ask_reasoning_row.set_active(True)
        self._assert_persisted(dlg, "ask_reasoning", True)

    def test_hybrid_toggle(self):
        dlg = self._dialog(ask_hybrid=False)
        dlg._ask_hybrid_row.set_active(True)
        self._assert_persisted(dlg, "ask_hybrid", True)

    def test_mmap_toggle(self):
        dlg = self._dialog(ask_use_mmap=True)
        dlg._ask_mmap_row.set_active(False)
        self._assert_persisted(dlg, "ask_use_mmap", False)

    def test_system_prompt_custom_persists_debounced(self):
        dlg = self._dialog()
        dlg._ask_prompt_view.get_buffer().set_text("Answer in haiku.")
        self.assertEqual(config.get_setting(dlg._settings, "ask.system_prompt"),
                         "Answer in haiku.")
        dlg._flush_persist()               # debounced write forced on close
        self.assertEqual(config.get_setting(self.saved[-1], "ask.system_prompt"),
                         "Answer in haiku.")

    def test_system_prompt_equal_to_default_stored_empty(self):
        # Matching the built-in default is stored as "" so it keeps tracking
        # future default improvements instead of pinning this snapshot.
        dlg = self._dialog()
        dlg._ask_prompt_view.get_buffer().set_text("You are a helpful assistant.")
        self.assertEqual(config.get_setting(dlg._settings, "ask.system_prompt"), "")


class TestSemanticDisabledGreysOutItsSettings(_DialogTest):
    """With semantic search off, nothing below the master switch has any effect —
    the rows must be greyed out rather than accept changes that do nothing (the
    rebuild button used to only object *after* it was pressed)."""

    def _gated(self, dlg):
        return [dlg._sem_backend_row, dlg._sem_score_row, dlg._sem_rebuild_row,
                dlg._emb_nav_row, dlg._ask_nav_row]

    def test_disabled_greys_out_the_rows_and_both_subpages(self):
        dlg = self._dialog(semantic_search_enabled=False)
        for row in self._gated(dlg):
            self.assertFalse(row.get_sensitive(), row.get_title())
        self.assertTrue(dlg._sem_enabled_row.get_sensitive())   # the way back on

    def test_enabled_keeps_them_usable(self):
        dlg = self._dialog(semantic_search_enabled=True)
        for row in self._gated(dlg):
            self.assertTrue(row.get_sensitive(), row.get_title())

    def test_toggling_takes_effect_immediately(self):
        dlg = self._dialog(semantic_search_enabled=True)
        dlg._sem_enabled_row.set_active(False)
        self.assertFalse(dlg._sem_backend_row.get_sensitive())
        dlg._sem_enabled_row.set_active(True)
        self.assertTrue(dlg._sem_backend_row.get_sensitive())


class TestApiKeyInKeyring(_DialogTest):
    """The API key lives in the keyring (secret_store), never in settings — and
    under a name that carries the endpoint, so it is only ever sent to the server
    it was entered for."""

    _SERVER = dict(ask_engine="manual", ask_backend="openai",
                   ask_ollama_url="https://llm.example.com")

    def _name(self, backend="openai", url="https://llm.example.com"):
        from markdown_vault.search import ask_models
        return ask_models.secret_name(backend, url)

    def test_read_from_keyring_on_open(self):
        self.secrets[self._name()] = "sk-stored"
        dlg = self._dialog(**self._SERVER)
        self.assertEqual(dlg._ask_key_entry.get_text(), "sk-stored")

    def test_change_writes_to_keyring_not_settings(self):
        dlg = self._dialog(**self._SERVER)
        dlg._ask_key_entry.set_text("sk-new")
        dlg._flush_secret()                       # force the debounced keyring write
        self.assertEqual(self.secrets.get(self._name()), "sk-new")
        self.assertNotIn("ask_api_key", dlg._settings)   # never in settings.yaml

    def test_clearing_a_key_that_was_shown_deletes_it(self):
        dlg = self._dialog(**self._SERVER)
        self.secrets[self._name()] = "sk-a"
        dlg._ask_key_entry.set_text("sk-a")
        dlg._flush_secret()
        dlg._ask_key_entry.set_text("")          # the user clears it
        dlg._flush_secret()
        self.assertNotIn(self._name(), self.secrets)

    def test_an_empty_write_cannot_delete_a_key_the_field_never_showed(self):
        # The keyring can answer "available" and still return nothing (locked
        # between probe and read). The field then looks empty although a key is
        # stored — and touching it would delete a credential the user never saw.
        dlg = self._dialog(**self._SERVER)        # opened with an empty field
        self.secrets[self._name()] = "sk-stored"  # …but a key does exist
        dlg._ask_key_entry.set_text("x")
        dlg._ask_key_entry.set_text("")
        dlg._flush_secret()
        self.assertEqual(self.secrets.get(self._name()), "sk-stored")

    def test_a_key_entered_and_cleared_in_the_same_session_is_removed(self):
        # The guard must not be "the field was empty at open" alone: once a key
        # has been stored from here, clearing it has to delete it again.
        dlg = self._dialog(**self._SERVER)
        dlg._ask_key_entry.set_text("sk-new")
        dlg._flush_secret()
        self.assertEqual(self.secrets.get(self._name()), "sk-new")
        dlg._ask_key_entry.set_text("")
        dlg._flush_secret()
        self.assertNotIn(self._name(), self.secrets)

    def test_key_of_another_server_is_not_shown_or_reused(self):
        self.secrets[self._name("ollama", "http://localhost:11434")] = "sk-ollama"
        dlg = self._dialog(**self._SERVER)
        self.assertEqual(dlg._ask_key_entry.get_text(), "")

    def test_app_wide_key_is_adopted_for_the_configured_server(self):
        # Pre-per-endpoint installs have one "ask_api_key"; it was already being
        # sent to exactly this server, so it is moved there and nowhere else.
        self.secrets["ask_api_key"] = "sk-old"
        dlg = self._dialog(**self._SERVER)
        self.assertEqual(dlg._ask_key_entry.get_text(), "sk-old")
        self.assertEqual(self.secrets.get(self._name()), "sk-old")
        self.assertFalse(self.secrets.get("ask_api_key"))

    def test_no_keyring_disables_the_field(self):
        with patch("markdown_vault.core.secret_store.available", return_value=False):
            dlg = self._dialog()
        self.assertFalse(dlg._ask_key_entry.get_sensitive())

    def test_failed_write_surfaces_an_error(self):
        dlg = self._dialog()
        with patch("markdown_vault.core.secret_store.set_secret", return_value=False), \
                patch("markdown_vault.ui.preferences.dialog.dialogs.show_error") as err:
            dlg._ask_key_entry.set_text("sk-nope")
            dlg._flush_secret()
        err.assert_called_once()


class TestAskModelPerEndpoint(_DialogTest):
    """The model belongs to the server, not to the app: switching provider must
    not leave the previous provider's model selected — it would be sent to a
    server that does not have it."""

    def _switch_backend(self, dlg, backend):
        dlg._ask_backend_row.set_selected(dlg._ask_backends.index(backend))

    def _listed(self, models):
        """The status a server that listed *models* would produce."""
        from markdown_vault.search import ask_models
        return ask_models.EndpointStatus(ask_models.OK, "https://llm.example.com",
                                         models=models)

    def test_switching_backend_drops_the_other_servers_model(self):
        dlg = self._dialog(ask_engine="manual", ask_backend="ollama",
                           ask_ollama_url="http://localhost:11434",
                           ask_model="llama3.2")
        dlg._remember_ask_model("llama3.2")
        self._switch_backend(dlg, "openai")
        self.assertEqual(config.get_setting(dlg._settings, "ask.server.model"), "")

    def test_switching_back_restores_the_earlier_choice(self):
        dlg = self._dialog(ask_engine="manual", ask_backend="ollama",
                           ask_ollama_url="http://localhost:11434",
                           ask_model="llama3.2")
        dlg._remember_ask_model("llama3.2")
        self._switch_backend(dlg, "openai")
        dlg._remember_ask_model("Qwen3.5-122B")
        self._switch_backend(dlg, "ollama")
        self.assertEqual(config.get_setting(dlg._settings, "ask.server.model"), "llama3.2")
        self._switch_backend(dlg, "openai")
        self.assertEqual(config.get_setting(dlg._settings, "ask.server.model"),
                         "Qwen3.5-122B")

    def test_a_hand_typed_url_is_not_carried_to_the_other_backend(self):
        # Previously only the known default ports were swapped, so a custom URL
        # travelled along and Ollama ended up talking to the OpenAI host.
        dlg = self._dialog(ask_engine="manual", ask_backend="openai",
                           ask_ollama_url="https://llm.example.com")
        self._switch_backend(dlg, "ollama")
        self.assertEqual(config.get_setting(dlg._settings, "ask.server.url"),
                         "http://localhost:11434")
        self._switch_backend(dlg, "openai")
        self.assertEqual(config.get_setting(dlg._settings, "ask.server.url"),
                         "https://llm.example.com")
        self.assertEqual(dlg._ask_url_entry.get_text(), "https://llm.example.com")

    def test_key_field_follows_the_backend(self):
        from markdown_vault.search import ask_models
        self.secrets[ask_models.secret_name("openai", "https://llm.example.com")] = "sk-a"
        self.secrets[ask_models.secret_name("ollama", "http://localhost:11434")] = "sk-b"
        dlg = self._dialog(ask_engine="manual", ask_backend="openai",
                           ask_ollama_url="https://llm.example.com")
        self.assertEqual(dlg._ask_key_entry.get_text(), "sk-a")
        self._switch_backend(dlg, "ollama")
        self.assertEqual(dlg._ask_key_entry.get_text(), "sk-b")

    def test_list_never_offers_a_model_the_server_lacks(self):
        dlg = self._dialog(ask_engine="manual", ask_backend="openai",
                           ask_ollama_url="https://llm.example.com",
                           ask_model="llama3.2")     # left over from Ollama
        dlg._populate_ask_models(self._listed(["Qwen3.5-122B", "gpt-oss"]))
        names = [dlg._ask_model_list.get_string(i)
                 for i in range(dlg._ask_model_list.get_n_items())]
        self.assertEqual(names, ["Qwen3.5-122B", "gpt-oss"])
        # …and the active value is a real one, not the stale name.
        self.assertEqual(config.get_setting(dlg._settings, "ask.server.model"),
                         "Qwen3.5-122B")

    def test_list_keeps_the_choice_the_server_has(self):
        dlg = self._dialog(ask_engine="manual", ask_backend="ollama",
                           ask_ollama_url="http://localhost:11434",
                           ask_model="llama3.2")
        dlg._populate_ask_models(self._listed(["qwen3", "llama3.2"]))
        self.assertEqual(dlg._ask_model_combo.get_selected(), 1)
        self.assertEqual(config.get_setting(dlg._settings, "ask.server.model"), "llama3.2")


class TestExternalWarning(_DialogTest):
    """The 'notes leave the device' warning tracks the URL, not the backend name:
    it shows for any server backend with a non-local URL, and hides for localhost."""

    def test_is_local_url(self):
        for u in ("http://localhost:8080", "http://127.0.0.1:11434",
                  "http://[::1]:8080", ""):
            self.assertTrue(PreferencesDialog._is_local_url(u), u)
        for u in ("https://llm.example.com", "http://192.168.1.5:8080",
                  "https://api.openai.com"):
            self.assertFalse(PreferencesDialog._is_local_url(u), u)

    def test_local_llama_cpp_no_warning(self):
        dlg = self._dialog(ask_backend="openai", ask_ollama_url="http://localhost:8080")
        self.assertFalse(dlg._ask_external_row.get_visible())

    def test_remote_openai_shows_warning(self):
        dlg = self._dialog(ask_backend="openai",
                           ask_ollama_url="https://llm.example.com")
        self.assertTrue(dlg._ask_external_row.get_visible())

    def test_remote_ollama_shows_warning(self):
        dlg = self._dialog(ask_backend="ollama",
                           ask_ollama_url="http://192.168.1.5:11434")
        self.assertTrue(dlg._ask_external_row.get_visible())

    def test_local_backend_no_warning(self):
        dlg = self._dialog(ask_backend="local")
        self.assertFalse(dlg._ask_external_row.get_visible())


if __name__ == "__main__":
    unittest.main()
