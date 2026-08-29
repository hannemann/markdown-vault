"""Tests for markdown_vault.importers.dialog_import — the ImportDialog logic.

ImportDialog is an Adw.Dialog, but its behaviour lives in plain methods: URL/file
validation drives the Import button, `_recheck_file` decides banner vs. block, and
the success/error handlers emit signals or update banners depending on whether the
dialog was already dismissed. The engines (web_import, document_import) and the
worker thread are mocked; the real Adw widgets are constructed headless so button
sensitivity and banner state can be asserted directly.
"""

import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GObject, GLib, Gtk  # type: ignore[attr-defined]

Adw.init()

from markdown_vault.importers.dialog_import import ImportDialog
from markdown_vault.importers import web_import as wi


class TestFileChooserFailure(unittest.TestCase):
    """Seam guard (stub dialog, internal name): the file-chooser callback splits a
    user cancel from a real dialog failure — silent on cancel, log + banner on a
    real failure. It previously swallowed both."""

    def _dialog(self, code):
        err = GLib.Error.new_literal(Gtk.DialogError.quark(), "x", code)
        d = MagicMock()
        d.open_finish.side_effect = err
        return d

    def test_cancel_is_silent(self):
        win = MagicMock()
        with self.assertNoLogs("markdown_vault.importers.dialog_import", level="WARNING"):
            ImportDialog._on_file_chosen(win, self._dialog(Gtk.DialogError.CANCELLED), None)
        win._show_file_error.assert_not_called()

    def test_real_failure_logs_and_shows_banner(self):
        win = MagicMock()
        with self.assertLogs("markdown_vault.importers.dialog_import", level="WARNING"):
            ImportDialog._on_file_chosen(win, self._dialog(Gtk.DialogError.FAILED), None)
        win._show_file_error.assert_called_once()


def _make_dialog(**kwargs):
    return ImportDialog("/vault/notes", **kwargs)


class TestWorkerErrorMapping(unittest.TestCase):
    """The web worker forwards a TRANSLATED, mapped message to _on_error — never
    the raw str(exc) with its untranslatable 'HTTP Error NNN:' prefix. Called
    unbound with a mock self, like the file-chooser seam test. C locale in the test
    env, so _() returns the English msgid."""

    @patch("markdown_vault.importers.dialog_import.GLib")
    @patch("markdown_vault.importers.dialog_import.web_import")
    def test_worker_maps_blocked_redirect_not_str(self, mock_web, mock_glib):
        mock_web.import_url.side_effect = wi.BlockedRedirectError(
            "http://x/", 302, "r", None, None)
        mock_web.describe_error = wi.describe_error   # keep the real mapper
        win = MagicMock()
        ImportDialog._worker(win, "http://x/", "", False)
        mock_glib.idle_add.assert_called_once()
        fn, msg = mock_glib.idle_add.call_args.args
        self.assertIs(fn, win._on_error)
        self.assertEqual(msg, "The page redirected to a blocked or non-public target.")
        self.assertNotIn("HTTP Error", msg)

    @patch("markdown_vault.importers.dialog_import.GLib")
    @patch("markdown_vault.importers.dialog_import.path_utils")
    @patch("markdown_vault.importers.dialog_import.document_import")
    def test_file_worker_maps_a_containment_refusal_not_str(
            self, mock_doc, _mock_paths, mock_glib):
        # The file worker's mirror of the test above. Without it, reverting the mapping to
        # str(exc) leaves the suite green and the user reads
        # "…/report.md is outside every vault" again — the caller side of BG1.
        from markdown_vault.core import vault_fs
        from markdown_vault.importers import document_import as di
        mock_doc.convert.return_value = MagicMock(markdown="text")
        mock_doc.save_to_vault.side_effect = vault_fs.OutsideVault(
            "/home/x/Downloads/report.md is outside every vault")
        mock_doc.describe_error = di.describe_error       # keep the real mapper
        win = MagicMock()
        ImportDialog._file_worker(win, "/tmp/report.pdf", "")
        mock_glib.idle_add.assert_called_once()
        fn, msg = mock_glib.idle_add.call_args.args
        self.assertIs(fn, win._on_file_error)
        self.assertNotIn("/home/x", msg)
        self.assertNotIn("outside every vault", msg)


class TestModuleStructure(unittest.TestCase):
    """The dialog exposes the two documented signals."""

    def test_signals_defined(self):
        # __gsignals__ is consumed at class registration, so look the signals up
        # on the registered GType instead.
        self.assertTrue(GObject.signal_lookup("note-imported", ImportDialog))
        self.assertTrue(GObject.signal_lookup("import-failed", ImportDialog))


class TestChooserNavigation(unittest.TestCase):
    """Step 1 rows switch the stack to the matching form."""

    def setUp(self):
        self.dlg = _make_dialog()

    def test_choose_web_shows_web_form(self):
        self.dlg._on_choose_web(None)
        self.assertEqual(self.dlg._stack.get_visible_child_name(), "web")

    @patch("markdown_vault.importers.dialog_import.document_import")
    def test_choose_file_shows_file_form_and_rechecks(self, mock_doc):
        mock_doc.is_available.return_value = None
        mock_doc.whisper_model_ready.return_value = True
        self.dlg._on_choose_file(None)
        self.assertEqual(self.dlg._stack.get_visible_child_name(), "file")


class TestUrlValidation(unittest.TestCase):
    """`_validate` toggles the web Import button off the URL validator."""

    def setUp(self):
        self.dlg = _make_dialog()

    @patch("markdown_vault.importers.dialog_import.web_import")
    def test_valid_url_enables_button(self, mock_web):
        mock_web.validate_url.return_value = "https://example.com"
        self.dlg._url_row.set_text("https://example.com")
        self.dlg._validate()
        self.assertTrue(self.dlg._import_btn.get_sensitive())

    @patch("markdown_vault.importers.dialog_import.web_import")
    def test_invalid_url_disables_button(self, mock_web):
        mock_web.validate_url.side_effect = ValueError("bad")
        self.dlg._url_row.set_text("not a url")
        self.dlg._validate()
        self.assertFalse(self.dlg._import_btn.get_sensitive())

    @patch("markdown_vault.importers.dialog_import.web_import")
    def test_busy_skips_validation(self, mock_web):
        self.dlg._busy = True
        self.dlg._validate()
        mock_web.validate_url.assert_not_called()


class TestRecheckFile(unittest.TestCase):
    """`_recheck_file` is the branchy part: block reason vs. informational notice."""

    def setUp(self):
        self.dlg = _make_dialog()

    def _doc(self, mock, *, stack=None, fmt=None, model_ready=True,
             needs_model=False, model_name="base"):
        # is_available() (no arg) reports the whole stack; is_available(suffix)
        # reports one format's backend.
        mock.is_available.side_effect = lambda *a: (fmt if a else stack)
        mock.whisper_model_ready.return_value = model_ready
        mock.needs_transcription_model.return_value = needs_model
        mock.whisper_model_name.return_value = model_name

    @patch("markdown_vault.importers.dialog_import.document_import")
    def test_no_file_stack_ready_no_banner(self, mock_doc):
        self._doc(mock_doc)  # stack present, model ready
        self.dlg._file_path = None
        self.dlg._recheck_file()
        self.assertIsNone(self.dlg._file_block_reason)
        self.assertFalse(self.dlg._file_error.get_revealed())
        self.assertFalse(self.dlg._file_import_btn.get_sensitive())

    @patch("markdown_vault.importers.dialog_import.document_import")
    def test_no_file_stack_missing_shows_notice_not_block(self, mock_doc):
        self._doc(mock_doc, stack="Install the AI stack for file import.")
        self.dlg._file_path = None
        self.dlg._recheck_file()
        self.assertIsNone(self.dlg._file_block_reason)  # informational only
        self.assertTrue(self.dlg._file_error.get_revealed())

    @patch("markdown_vault.importers.dialog_import.document_import")
    def test_file_backend_present_enables_import(self, mock_doc):
        self._doc(mock_doc)
        self.dlg._file_path = "/docs/report.pdf"
        self.dlg._recheck_file()
        self.assertIsNone(self.dlg._file_block_reason)
        self.assertFalse(self.dlg._file_error.get_revealed())
        self.assertTrue(self.dlg._file_import_btn.get_sensitive())

    @patch("markdown_vault.importers.dialog_import.document_import")
    def test_file_backend_missing_blocks(self, mock_doc):
        self._doc(mock_doc, fmt="PDF backend not installed.")
        self.dlg._file_path = "/docs/report.pdf"
        self.dlg._recheck_file()
        self.assertEqual(self.dlg._file_block_reason, "PDF backend not installed.")
        self.assertTrue(self.dlg._file_error.get_revealed())
        self.assertFalse(self.dlg._file_import_btn.get_sensitive())

    @patch("markdown_vault.importers.dialog_import.document_import")
    def test_audio_without_model_blocks(self, mock_doc):
        self._doc(mock_doc, model_ready=False, needs_model=True, model_name="small")
        self.dlg._file_path = "/clips/voice.mp3"
        self.dlg._recheck_file()
        self.assertIsNotNone(self.dlg._file_block_reason)
        self.assertIn("small", self.dlg._file_block_reason)
        self.assertFalse(self.dlg._file_import_btn.get_sensitive())


class TestValidateFile(unittest.TestCase):
    """`_validate_file` gates the button on file + not-busy + no block reason."""

    def setUp(self):
        self.dlg = _make_dialog()

    def test_enabled_when_ready(self):
        self.dlg._file_path = "/docs/a.pdf"
        self.dlg._busy = False
        self.dlg._file_block_reason = None
        self.dlg._validate_file()
        self.assertTrue(self.dlg._file_import_btn.get_sensitive())

    def test_disabled_without_file(self):
        self.dlg._file_path = None
        self.dlg._validate_file()
        self.assertFalse(self.dlg._file_import_btn.get_sensitive())

    def test_disabled_when_blocked(self):
        self.dlg._file_path = "/docs/a.pdf"
        self.dlg._file_block_reason = "no backend"
        self.dlg._validate_file()
        self.assertFalse(self.dlg._file_import_btn.get_sensitive())


class TestOnImportGuards(unittest.TestCase):
    """`_on_import` validates before spawning the worker thread."""

    def setUp(self):
        self.dlg = _make_dialog()

    @patch("markdown_vault.importers.dialog_import.threading.Thread")
    @patch("markdown_vault.importers.dialog_import.web_import")
    def test_valid_spawns_worker(self, mock_web, mock_thread):
        mock_web.validate_url.return_value = "https://example.com"
        mock_web.availability.return_value = None
        self.dlg._url_row.set_text("https://example.com")
        self.dlg._name_row.set_text(" note ")
        self.dlg._on_import()
        mock_thread.assert_called_once()
        self.assertEqual(mock_thread.call_args.kwargs["args"],
                         ("https://example.com", "note", False))
        self.assertTrue(self.dlg._busy)

    @patch("markdown_vault.importers.dialog_import.threading.Thread")
    @patch("markdown_vault.importers.dialog_import.web_import")
    def test_invalid_url_shows_error_no_thread(self, mock_web, mock_thread):
        mock_web.validate_url.side_effect = ValueError("bad")
        self.dlg._on_import()
        self.assertTrue(self.dlg._error.get_revealed())
        mock_thread.assert_not_called()

    @patch("markdown_vault.importers.dialog_import.threading.Thread")
    @patch("markdown_vault.importers.dialog_import.web_import")
    def test_availability_hint_blocks(self, mock_web, mock_thread):
        mock_web.validate_url.return_value = "https://example.com"
        mock_web.availability.return_value = "trafilatura not installed"
        self.dlg._on_import()
        self.assertTrue(self.dlg._error.get_revealed())
        mock_thread.assert_not_called()


class TestFileChosen(unittest.TestCase):
    """`_on_file_chosen` records the path, subtitle and default name."""

    def setUp(self):
        self.dlg = _make_dialog()

    @patch("markdown_vault.importers.dialog_import.document_import")
    def test_records_path_and_defaults_name(self, mock_doc):
        mock_doc.is_available.return_value = None
        mock_doc.whisper_model_ready.return_value = True
        gfile = MagicMock()
        gfile.get_path.return_value = "/docs/Quarterly Report.pdf"
        gfile.get_basename.return_value = "Quarterly Report.pdf"
        chooser = MagicMock()
        chooser.open_finish.return_value = gfile
        saved = []
        self.dlg._save_last_dir = saved.append
        self.dlg._on_file_chosen(chooser, MagicMock())
        self.assertEqual(self.dlg._file_path, "/docs/Quarterly Report.pdf")
        self.assertEqual(self.dlg._file_name_row.get_text(), "Quarterly Report")
        self.assertEqual(saved, ["/docs"])

    def test_cancelled_leaves_state(self):
        from gi.repository import GLib  # type: ignore[attr-defined]
        chooser = MagicMock()
        chooser.open_finish.side_effect = GLib.Error("cancelled")
        self.dlg._on_file_chosen(chooser, MagicMock())
        self.assertIsNone(self.dlg._file_path)


class TestRememberDir(unittest.TestCase):
    """`_remember_dir` persists through the injected setter."""

    def test_calls_setter_and_stores(self):
        saved = []
        dlg = _make_dialog(save_last_dir=saved.append)
        dlg._remember_dir(Path("/docs/sub"))
        self.assertEqual(dlg._last_dir, "/docs/sub")
        self.assertEqual(saved, ["/docs/sub"])

    def test_no_setter_is_safe(self):
        dlg = _make_dialog()
        dlg._remember_dir(Path("/docs/sub"))
        self.assertEqual(dlg._last_dir, "/docs/sub")


class TestSignalEmission(unittest.TestCase):
    """Success/error handlers emit or update banners based on `_closed`."""

    def setUp(self):
        self.dlg = _make_dialog()

    def test_success_emits_note_imported(self):
        got = []
        self.dlg.connect("note-imported", lambda _d, p: got.append(p))
        self.dlg._closed = True  # don't drive the real close()
        self.dlg._on_success("/vault/notes/new.md")
        self.assertEqual(got, ["/vault/notes/new.md"])

    def test_error_while_open_shows_banner(self):
        self.dlg._closed = False
        self.dlg._on_error("boom")
        self.assertTrue(self.dlg._error.get_revealed())
        self.assertFalse(self.dlg._busy)

    def test_error_after_close_emits_failed(self):
        got = []
        self.dlg.connect("import-failed", lambda _d, m: got.append(m))
        self.dlg._closed = True
        self.dlg._on_error("boom")
        self.assertEqual(got, ["boom"])

    def test_file_error_after_close_emits_failed(self):
        got = []
        self.dlg.connect("import-failed", lambda _d, m: got.append(m))
        self.dlg._closed = True
        self.dlg._on_file_error("nope")
        self.assertEqual(got, ["nope"])


class TestWebWorker(unittest.TestCase):
    """`_worker` marshals the web import result back via idle_add."""

    def setUp(self):
        self.dlg = _make_dialog()

    @patch("markdown_vault.importers.dialog_import.GLib.idle_add")
    @patch("markdown_vault.importers.dialog_import.path_utils")
    @patch("markdown_vault.importers.dialog_import.web_import")
    def test_success_schedules_on_success(self, mock_web, mock_paths, mock_idle):
        mock_web.import_url.return_value = MagicMock(markdown="# hi")
        mock_paths.find_vault_for_dir.return_value = "/vault"
        mock_web.save_to_vault.return_value = "/vault/notes/x.md"
        self.dlg._worker("https://e.com", "", False)
        mock_idle.assert_called_once_with(self.dlg._on_success, "/vault/notes/x.md")

    @patch("markdown_vault.importers.dialog_import.GLib.idle_add")
    @patch("markdown_vault.importers.dialog_import.web_import")
    def test_empty_markdown_schedules_error(self, mock_web, mock_idle):
        mock_web.import_url.return_value = MagicMock(markdown="   ")
        self.dlg._worker("https://e.com", "", False)
        self.assertEqual(mock_idle.call_args.args[0], self.dlg._on_error)

    @patch("markdown_vault.importers.dialog_import.GLib.idle_add")
    @patch("markdown_vault.importers.dialog_import.web_import")
    def test_exception_schedules_error(self, mock_web, mock_idle):
        mock_web.import_url.side_effect = RuntimeError("network down")
        mock_web.describe_error = wi.describe_error   # keep the real mapper
        self.dlg._worker("https://e.com", "", False)
        self.assertEqual(mock_idle.call_args.args[0], self.dlg._on_error)
        # a foreign exception maps to the translated default — never the raw
        # str(exc), which would leak untranslated technical text into the banner
        self.assertEqual(mock_idle.call_args.args[1],
                         "The import failed. See the log for details.")
        self.assertNotIn("network down", mock_idle.call_args.args[1])


class TestDownloadFlag(unittest.TestCase):
    """The 'Download images' switch must reach the worker and the save call —
    the opt-in the finding was written about (R119.1)."""

    def setUp(self):
        self.dlg = _make_dialog()

    @patch("markdown_vault.importers.dialog_import.threading.Thread")
    @patch("markdown_vault.importers.dialog_import.web_import")
    def test_download_switch_reaches_the_worker(self, mock_web, mock_thread):
        mock_web.validate_url.return_value = "https://example.com"
        mock_web.availability.return_value = None
        self.dlg._url_row.set_text("https://example.com")
        self.dlg._dl_row.set_active(True)          # opt in — not the default
        self.dlg._on_import()
        self.assertEqual(mock_thread.call_args.kwargs["args"],
                         ("https://example.com", "", True))

    @patch("markdown_vault.importers.dialog_import.GLib.idle_add")
    @patch("markdown_vault.importers.dialog_import.path_utils")
    @patch("markdown_vault.importers.dialog_import.web_import")
    def test_worker_passes_download_flag_to_save(self, mock_web, mock_paths, mock_idle):
        mock_web.import_url.return_value = MagicMock(markdown="# x")
        mock_paths.find_vault_for_dir.return_value = "/vault"
        self.dlg._worker("https://e.com", "", True)
        self.assertIs(mock_web.save_to_vault.call_args.kwargs["download_images"], True)


class TestBrowseSurfacing(unittest.TestCase):
    """`_browse_file` surfaces every SUPPORTED_SUFFIXES entry as a filter — the
    seam between a registered engine handler and the user being able to pick it."""

    def setUp(self):
        self.dlg = _make_dialog()

    @patch("markdown_vault.importers.dialog_import.Gio.ListStore")
    @patch("markdown_vault.importers.dialog_import.Gtk.FileFilter")
    @patch("markdown_vault.importers.dialog_import.Gtk.FileDialog")
    @patch("markdown_vault.importers.dialog_import.document_import")
    def test_supported_suffixes_become_filters(self, mock_doc, MockDialog,
                                               MockFilter, _MockStore):
        mock_doc.SUPPORTED_SUFFIXES = [".pdf", ".docx", ".mp3"]
        filt = MockFilter.return_value
        self.dlg._browse_file()
        added = [c.args[0] for c in filt.add_suffix.call_args_list]
        self.assertEqual(added, ["pdf", "docx", "mp3"])
        MockDialog.return_value.set_default_filter.assert_called_once_with(filt)

    @patch("markdown_vault.importers.dialog_import.Gio.ListStore")
    @patch("markdown_vault.importers.dialog_import.Gtk.FileFilter")
    @patch("markdown_vault.importers.dialog_import.Gtk.FileDialog")
    @patch("markdown_vault.importers.dialog_import.document_import")
    def test_reopens_at_last_dir(self, mock_doc, MockDialog, _MockFilter, _MockStore):
        import tempfile
        mock_doc.SUPPORTED_SUFFIXES = [".pdf"]
        with tempfile.TemporaryDirectory() as last:
            self.dlg._last_dir = last
            self.dlg._browse_file()
            MockDialog.return_value.set_initial_folder.assert_called_once()


class TestFileWorker(unittest.TestCase):
    """`_file_worker` mirrors the web worker for document import."""

    def setUp(self):
        self.dlg = _make_dialog()

    @patch("markdown_vault.importers.dialog_import.GLib.idle_add")
    @patch("markdown_vault.importers.dialog_import.path_utils")
    @patch("markdown_vault.importers.dialog_import.document_import")
    def test_success_schedules_on_success(self, mock_doc, mock_paths, mock_idle):
        mock_doc.convert.return_value = MagicMock(markdown="# doc")
        mock_paths.find_vault_for_dir.return_value = "/vault"
        mock_doc.save_to_vault.return_value = "/vault/notes/d.md"
        self.dlg._file_worker("/docs/a.pdf", "")
        mock_idle.assert_called_once_with(self.dlg._on_success, "/vault/notes/d.md")

    @patch("markdown_vault.importers.dialog_import.GLib.idle_add")
    @patch("markdown_vault.importers.dialog_import.document_import")
    def test_empty_extract_schedules_error(self, mock_doc, mock_idle):
        mock_doc.convert.return_value = MagicMock(markdown="")
        self.dlg._file_worker("/docs/a.pdf", "")
        self.assertEqual(mock_idle.call_args.args[0], self.dlg._on_file_error)


class TestOnFileImportGuards(unittest.TestCase):
    """`_on_file_import` validates the format before spawning the worker."""

    def setUp(self):
        self.dlg = _make_dialog()

    @patch("markdown_vault.importers.dialog_import.threading.Thread")
    @patch("markdown_vault.importers.dialog_import.document_import")
    def test_valid_spawns_worker(self, mock_doc, mock_thread):
        mock_doc.is_available.return_value = None
        self.dlg._file_path = "/docs/a.pdf"
        self.dlg._file_name_row.set_text(" my doc ")
        self.dlg._on_file_import()
        mock_thread.assert_called_once()
        self.assertEqual(mock_thread.call_args.kwargs["args"], ("/docs/a.pdf", "my doc"))
        self.assertTrue(self.dlg._busy)

    @patch("markdown_vault.importers.dialog_import.threading.Thread")
    @patch("markdown_vault.importers.dialog_import.document_import")
    def test_backend_missing_shows_error_no_thread(self, mock_doc, mock_thread):
        mock_doc.is_available.return_value = "no PDF backend"
        self.dlg._file_path = "/docs/a.pdf"
        self.dlg._on_file_import()
        self.assertTrue(self.dlg._file_error.get_revealed())
        mock_thread.assert_not_called()

    @patch("markdown_vault.importers.dialog_import.threading.Thread")
    def test_no_file_is_noop(self, mock_thread):
        self.dlg._file_path = None
        self.dlg._on_file_import()
        mock_thread.assert_not_called()


class TestSmallGuards(unittest.TestCase):
    """The remaining early-return / notice branches."""

    def setUp(self):
        self.dlg = _make_dialog()

    @patch("markdown_vault.importers.dialog_import.threading.Thread")
    @patch("markdown_vault.importers.dialog_import.web_import")
    def test_on_import_while_busy_is_noop(self, mock_web, mock_thread):
        self.dlg._busy = True
        self.dlg._on_import()
        mock_web.validate_url.assert_not_called()
        mock_thread.assert_not_called()

    def test_file_chosen_none_gfile_leaves_state(self):
        chooser = MagicMock()
        chooser.open_finish.return_value = None
        self.dlg._on_file_chosen(chooser, MagicMock())
        self.assertIsNone(self.dlg._file_path)

    @patch("markdown_vault.importers.dialog_import.document_import")
    def test_no_file_audio_model_missing_shows_notice(self, mock_doc):
        mock_doc.is_available.return_value = None       # stack present
        mock_doc.whisper_model_ready.return_value = False  # but no model
        mock_doc.whisper_model_name.return_value = "base"
        self.dlg._file_path = None
        self.dlg._recheck_file()
        self.assertIsNone(self.dlg._file_block_reason)   # notice, not a block
        self.assertTrue(self.dlg._file_error.get_revealed())


class TestCloseAndSuccessClose(unittest.TestCase):
    """`_on_closed` flips the flag; `_on_success` closes an open dialog."""

    def setUp(self):
        self.dlg = _make_dialog()

    def test_on_closed_sets_flag(self):
        self.dlg._on_closed(None)
        self.assertTrue(self.dlg._closed)

    def test_success_closes_when_open(self):
        with patch.object(self.dlg, "close") as mock_close:
            self.dlg._closed = False
            self.dlg._on_success("/vault/notes/x.md")
            mock_close.assert_called_once()

    def test_file_error_while_open_shows_banner(self):
        self.dlg._closed = False
        self.dlg._on_file_error("nope")
        self.assertTrue(self.dlg._file_error.get_revealed())
        self.assertFalse(self.dlg._busy)


if __name__ == "__main__":
    unittest.main()
