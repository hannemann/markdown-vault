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

    def tearDown(self):
        for m in reversed(self._patchers):
            m.stop()

    def _dialog(self, **settings):
        self.load.return_value = dict(settings)
        return PreferencesDialog()

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


if __name__ == "__main__":
    unittest.main()
