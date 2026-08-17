"""Tests for markdown_vault.ui.preferences — PreferencesDialog behaviour.

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
from markdown_vault.ui.preferences import PreferencesDialog  # noqa: E402


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

        self.load = _p("markdown_vault.ui.preferences.config.load_settings")
        self.load.return_value = {}
        _p("markdown_vault.ui.preferences.config.save_settings").side_effect = (
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
            self.secrets[k] = v
            return True                                   # write succeeded
        _p("markdown_vault.core.secret_store.set_secret").side_effect = _store

    def tearDown(self):
        # Cancel debounced writes BEFORE the patches go away. A pending
        # GLib.timeout from _persist_debounced / _on_secret_changed would
        # otherwise fire once some later test runs a main loop — by then
        # config.save_settings is the real one again, and it would persist this
        # dialog's tiny snapshot over the developer's actual vaults.yaml,
        # wiping every setting not in it (that really happened).
        from gi.repository import GLib  # type: ignore[attr-defined]
        for dlg in self._dialogs:
            for attr in ("_persist_id", "_secret_persist_id"):
                source = getattr(dlg, attr, None)
                if source is not None:
                    GLib.source_remove(source)
                    setattr(dlg, attr, None)
            dlg._pending_secret = None
        self._dialogs.clear()
        for m in reversed(self._patchers):
            m.stop()

    def _dialog(self, **settings):
        self.load.return_value = dict(settings)
        dlg = PreferencesDialog()
        self._dialogs.append(dlg)   # so tearDown can cancel its pending writes
        return dlg

    def _assert_persisted(self, dlg, key, value):
        self.assertEqual(dlg._settings[key], value)
        self.assertTrue(self.saved and self.saved[-1][key] == value)


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
        self.assertEqual(dlg._settings["autosave_interval"], 90)
        self.assertTrue(self.saved and self.saved[-1]["autosave_interval"] == 90)
        self.assertEqual(got, [True])

    def test_save_failure_shows_error_and_no_signal(self):
        dlg = self._dialog(autosave_interval=30)
        got = []
        dlg.connect("settings-changed", lambda _d: got.append(True))
        with patch("markdown_vault.ui.preferences.config.save_settings",
                   side_effect=OSError("disk full")), \
                patch("markdown_vault.ui.preferences.dialogs.show_error") as err:
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
        self.assertEqual(dlg._settings["ask_system_prompt"], "Answer in haiku.")
        dlg._flush_persist()               # debounced write forced on close
        self.assertEqual(self.saved[-1]["ask_system_prompt"], "Answer in haiku.")

    def test_system_prompt_equal_to_default_stored_empty(self):
        # Matching the built-in default is stored as "" so it keeps tracking
        # future default improvements instead of pinning this snapshot.
        dlg = self._dialog()
        dlg._ask_prompt_view.get_buffer().set_text("You are a helpful assistant.")
        self.assertEqual(dlg._settings["ask_system_prompt"], "")


class TestApiKeyInKeyring(_DialogTest):
    """The API key lives in the keyring (secret_store), never in settings."""

    def test_read_from_keyring_on_open(self):
        self.secrets["ask_api_key"] = "sk-stored"
        dlg = self._dialog()
        self.assertEqual(dlg._ask_key_entry.get_text(), "sk-stored")

    def test_change_writes_to_keyring_not_settings(self):
        dlg = self._dialog()
        dlg._ask_key_entry.set_text("sk-new")
        dlg._flush_secret()                       # force the debounced keyring write
        self.assertEqual(self.secrets.get("ask_api_key"), "sk-new")
        self.assertNotIn("ask_api_key", dlg._settings)   # never in vaults.yaml

    def test_no_keyring_disables_the_field(self):
        with patch("markdown_vault.core.secret_store.available", return_value=False):
            dlg = self._dialog()
        self.assertFalse(dlg._ask_key_entry.get_sensitive())

    def test_failed_write_surfaces_an_error(self):
        dlg = self._dialog()
        with patch("markdown_vault.core.secret_store.set_secret", return_value=False), \
                patch("markdown_vault.ui.preferences.dialogs.show_error") as err:
            dlg._ask_key_entry.set_text("sk-nope")
            dlg._flush_secret()
        err.assert_called_once()


class TestExternalWarning(_DialogTest):
    """The 'notes leave the device' warning tracks the URL, not the backend name:
    it shows for any server backend with a non-local URL, and hides for localhost."""

    def test_is_local_url(self):
        for u in ("http://localhost:8080", "http://127.0.0.1:11434",
                  "http://[::1]:8080", ""):
            self.assertTrue(PreferencesDialog._is_local_url(u), u)
        for u in ("https://llm.aihosting.mittwald.de", "http://192.168.1.5:8080",
                  "https://api.openai.com"):
            self.assertFalse(PreferencesDialog._is_local_url(u), u)

    def test_local_llama_cpp_no_warning(self):
        dlg = self._dialog(ask_backend="openai", ask_ollama_url="http://localhost:8080")
        self.assertFalse(dlg._ask_external_row.get_visible())

    def test_remote_openai_shows_warning(self):
        dlg = self._dialog(ask_backend="openai",
                           ask_ollama_url="https://llm.aihosting.mittwald.de")
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
