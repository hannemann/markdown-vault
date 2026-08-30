"""Import dialog — right-click a vault/folder → Import… to bring content into it.

A two-step :class:`Adw.Dialog`: pick a source (Web Page / File), then a
source-specific form. The web branch takes a URL, an optional name and a
download-images switch; the file branch picks a local document (PDF, Word,
PowerPoint, Excel, audio) and an optional name. Either way the convert/save runs on
a worker thread so the UI never blocks and the result is marshalled back with
:func:`GLib.idle_add`. On success it emits ``note-imported`` with the new file's
path; the window opens it and reveals it in the tree.

The two forms are independent (only the chooser shell and the success/close plumbing
are shared), so web and document import never depend on each other.
"""

import logging
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw, Gio, GObject, GLib

from markdown_vault.importers import document_import, web_import
from markdown_vault.core import path_utils
from markdown_vault.core import vault_fs
from markdown_vault.core.i18n import _
from markdown_vault.uikit import dialogs

logger = logging.getLogger(__name__)


class ImportDialog(Adw.Dialog):
    """Import content into *target_dir* — currently a web page as a note.

    Signals:
        note-imported(str): path of the newly written note.
        import-failed(str): error message, when the import fails after the dialog
            was already dismissed (there is no banner left to show it in).
    """

    __gsignals__ = {
        "note-imported": (GObject.SignalFlags.RUN_LAST, None, (str,)),
        "import-failed": (GObject.SignalFlags.RUN_LAST, None, (str,)),
    }

    def __init__(self, target_dir: str, last_dir: str | None = None,
                 save_last_dir=None):
        super().__init__()
        self._target_dir = target_dir
        self._last_dir = last_dir            # folder to reopen the chooser in
        self._save_last_dir = save_last_dir  # callback to persist the last folder
        self._busy = False
        self._closed = False
        self.set_title(_("Import"))
        self.set_content_width(480)
        self.connect("closed", self._on_closed)

        self._file_path: str | None = None
        self._file_block_reason: str | None = None   # why Import is disabled, if any
        self._stack = Gtk.Stack()
        self._stack.add_named(self._build_chooser(), "chooser")
        self._stack.add_named(self._build_web_form(), "web")
        self._stack.add_named(self._build_file_form(), "file")

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        toolbar.set_content(self._stack)
        self.set_child(toolbar)

    # ── Step 1: source chooser ─────────────────────────────────────────
    def _build_chooser(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      margin_top=18, margin_bottom=18, margin_start=18, margin_end=18)
        group = Adw.PreferencesGroup(title=_("What do you want to import?"))

        web_row = Adw.ActionRow(title=_("Web Page"),
                                subtitle=_("Fetch a URL as a Markdown note"),
                                activatable=True)
        web_row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        web_row.connect("activated", self._on_choose_web)
        group.add(web_row)

        file_row = Adw.ActionRow(title=_("File"),
                                 subtitle=_("Import a document (PDF, Word, PowerPoint, "
                                            "Excel, audio)"),
                                 activatable=True)
        file_row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        file_row.connect("activated", self._on_choose_file)
        group.add(file_row)

        box.append(group)
        return box

    def _on_choose_web(self, _row) -> None:
        self._stack.set_visible_child_name("web")
        self._url_row.grab_focus()

    # ── Step 2: web form ───────────────────────────────────────────────
    def _build_web_form(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      margin_top=18, margin_bottom=18, margin_start=18, margin_end=18)

        self._error = Adw.Banner()
        self._error.set_revealed(False)
        box.append(self._error)

        group = Adw.PreferencesGroup()
        self._url_row = Adw.EntryRow(title=_("URL"))
        self._url_row.connect("changed", lambda *_: self._validate())
        self._url_row.connect("entry-activated", lambda *_: self._on_import())
        group.add(self._url_row)

        self._name_row = Adw.EntryRow(title=_("Name (optional)"))
        group.add(self._name_row)

        self._dl_row = Adw.SwitchRow(title=_("Download images"),
                                     subtitle=_("Save images into the vault's attachments folder"))
        group.add(self._dl_row)
        box.append(group)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8,
                          halign=Gtk.Align.END)
        self._spinner = Gtk.Spinner()
        actions.append(self._spinner)
        self._import_btn = Gtk.Button(label=_("Import"))
        self._import_btn.add_css_class("suggested-action")
        self._import_btn.set_sensitive(False)
        self._import_btn.connect("clicked", lambda *_: self._on_import())
        actions.append(self._import_btn)
        box.append(actions)
        return box

    def _validate(self) -> None:
        if self._busy:
            return
        try:
            web_import.validate_url(self._url_row.get_text())
            valid = True
        except ValueError:
            # invalid URL just leaves Import disabled — the raise/catch is the control flow
            valid = False
        self._import_btn.set_sensitive(valid)

    def _show_error(self, message: str) -> None:
        self._error.set_title(message)
        self._error.set_revealed(True)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        for widget in (self._url_row, self._name_row, self._dl_row, self._import_btn):
            widget.set_sensitive(not busy)
        if busy:
            self._spinner.start()
        else:
            self._spinner.stop()
            self._validate()

    def _on_import(self) -> None:
        if self._busy:
            return
        try:
            url = web_import.validate_url(self._url_row.get_text())
        except ValueError:
            # user input error, not a fault → the banner suffices; a log would be noise
            self._show_error(_("Enter a valid http(s) URL."))
            return
        hint = web_import.availability()
        if hint:
            self._show_error(hint)
            return
        self._error.set_revealed(False)
        self._set_busy(True)
        name = self._name_row.get_text().strip()
        download = self._dl_row.get_active()
        threading.Thread(target=self._worker, args=(url, name, download),
                         daemon=True).start()

    # ── Worker thread ──────────────────────────────────────────────────
    def _worker(self, url: str, name: str, download: bool) -> None:
        try:
            result = web_import.import_url(url)
            if not result.markdown.strip():
                raise web_import.WebImportError(_("Nothing extractable on that page."))
            # Attachments go under the vault root (…/attachments/<note-path>/),
            # even when importing into a subfolder.
            vault_root = path_utils.find_vault_for_dir(self._target_dir)
            path = web_import.save_to_vault(result, self._target_dir,
                                            download_images=download,
                                            name=name or None, vault_root=vault_root)
        except Exception as exc:  # surface any failure to the user, never crash
            logger.warning("Web import failed for %s: %s", url, exc, exc_info=True)
            GLib.idle_add(self._on_error, web_import.describe_error(exc))
            return
        GLib.idle_add(self._on_success, str(path))

    # ── Step 2: file form ──────────────────────────────────────────────
    def _build_file_form(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      margin_top=18, margin_bottom=18, margin_start=18, margin_end=18)

        self._file_error = Adw.Banner()
        self._file_error.set_revealed(False)
        box.append(self._file_error)

        group = Adw.PreferencesGroup()
        self._file_row = Adw.ActionRow(title=_("File"), subtitle=_("No file selected"))
        browse = Gtk.Button(label=_("Browse…"), valign=Gtk.Align.CENTER)
        browse.connect("clicked", lambda *_: self._browse_file())
        self._file_row.add_suffix(browse)
        self._file_row.set_activatable_widget(browse)
        group.add(self._file_row)

        self._file_name_row = Adw.EntryRow(title=_("Name (optional)"))
        group.add(self._file_name_row)
        box.append(group)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8,
                          halign=Gtk.Align.END)
        self._file_spinner = Gtk.Spinner()
        actions.append(self._file_spinner)
        self._file_import_btn = Gtk.Button(label=_("Import"))
        self._file_import_btn.add_css_class("suggested-action")
        self._file_import_btn.set_sensitive(False)
        self._file_import_btn.connect("clicked", lambda *_: self._on_file_import())
        actions.append(self._file_import_btn)
        box.append(actions)
        return box

    def _on_choose_file(self, _row) -> None:
        self._stack.set_visible_child_name("file")
        self._recheck_file()                # heads-up before a file is even picked

    def _browse_file(self) -> None:
        dialog = Gtk.FileDialog(title=_("Choose a document"))
        filt = Gtk.FileFilter()
        filt.set_name(_("Supported documents"))
        for suffix in document_import.SUPPORTED_SUFFIXES:
            filt.add_suffix(suffix.lstrip("."))
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(filt)
        dialog.set_filters(filters)
        dialog.set_default_filter(filt)
        # Reopen where the last import came from — importing several files in a row
        # shouldn't drop you back at $HOME each time.
        if self._last_dir and Path(self._last_dir).is_dir():
            dialog.set_initial_folder(Gio.File.new_for_path(self._last_dir))
        dialog.open(self.get_root(), None, self._on_file_chosen)

    def _on_file_chosen(self, dialog, result) -> None:
        try:
            gfile = dialog.open_finish(result)
        except GLib.Error as exc:
            # A cancel and a real portal/backend failure raise the same type; stay
            # silent on cancel, surface a genuine failure instead of dropping it.
            if not dialogs.dialog_cancelled(exc):
                logger.warning("import file chooser failed", exc_info=True)
                self._show_file_error(_("Could not open the file chooser."))
            return
        if gfile is None or not gfile.get_path():
            return
        self._file_path = gfile.get_path()
        self._file_row.set_subtitle(gfile.get_basename())
        if not self._file_name_row.get_text().strip():
            self._file_name_row.set_text(Path(self._file_path).stem)
        self._remember_dir(Path(self._file_path).parent)
        self._recheck_file()

    def _remember_dir(self, folder) -> None:
        """Persist the folder of the last picked file (through the window's shared
        settings, via the injected setter) so the chooser reopens there next time."""
        self._last_dir = str(folder)
        if self._save_last_dir is not None:
            self._save_last_dir(str(folder))

    def _recheck_file(self) -> None:
        """Set the banner + Import-button state. Runs on opening the File tab and on
        every file pick, both cheap (a find_spec and a file test). With no file yet it
        shows a *heads-up* (audio needs its model) without blocking; once a file is
        picked it blocks Import only for that file's actual needs — a format whose
        backend is missing, or audio whose *selected* Whisper model isn't downloaded."""
        stack_hint = document_import.is_available()            # whole feature present?
        model_missing = (not stack_hint
                         and not document_import.whisper_model_ready())
        reason = None                                         # blocks Import
        notice = None                                         # informational only
        if not path_utils.find_vault_for_dir(self._target_dir):
            # VaultFS refuses a write outside every vault, so the import cannot succeed.
            # Checked here rather than only at the write: the target is known when the
            # dialog opens, and failing after the whole conversion tells the user far too
            # late. Lexical, so it is an EARLY filter, not the authority — the guard still
            # decides (it resolves symlinks, which this does not).
            reason = document_import.describe_error(vault_fs.OutsideVault(self._target_dir))
        elif self._file_path:
            suffix = Path(self._file_path).suffix
            reason = document_import.is_available(suffix)      # this format's backend?
            if (not reason and document_import.needs_transcription_model(suffix)
                    and model_missing):
                reason = _("The '{model}' transcription model isn't downloaded — "
                           "get it in Preferences → Search before importing "
                           "audio.").format(model=document_import.whisper_model_name())
        elif stack_hint:
            notice = stack_hint                               # AI stack not installed
        elif model_missing:
            notice = _("Audio import needs the '{model}' model, which isn't "
                       "downloaded yet (Preferences → Search).").format(
                           model=document_import.whisper_model_name())
        self._file_block_reason = reason
        message = reason or notice
        if message:
            self._show_file_error(message)
        else:
            self._file_error.set_revealed(False)
        self._validate_file()

    def _validate_file(self) -> None:
        self._file_import_btn.set_sensitive(
            bool(self._file_path) and not self._busy and not self._file_block_reason)

    def _show_file_error(self, message: str) -> None:
        self._file_error.set_title(message)
        self._file_error.set_revealed(True)

    def _set_file_busy(self, busy: bool) -> None:
        self._busy = busy
        for widget in (self._file_name_row, self._file_import_btn):
            widget.set_sensitive(not busy)
        if busy:
            self._file_spinner.start()
        else:
            self._file_spinner.stop()
            self._validate_file()

    def _on_file_import(self) -> None:
        if self._busy or not self._file_path:
            return
        hint = document_import.is_available(Path(self._file_path).suffix)
        if hint:
            self._show_file_error(hint)
            return
        self._file_error.set_revealed(False)
        self._set_file_busy(True)
        name = self._file_name_row.get_text().strip()
        threading.Thread(target=self._file_worker, args=(self._file_path, name),
                         daemon=True).start()

    def _file_worker(self, file_path: str, name: str) -> None:
        try:
            result = document_import.convert(file_path)
            if not result.markdown.strip():
                raise document_import.DocumentImportError(
                    _("No text could be extracted from this file."))
            # Attachments go under the vault root (…/attachments/<note-path>/),
            # even when importing into a subfolder — same as the web import.
            vault_root = path_utils.find_vault_for_dir(self._target_dir)
            path = document_import.save_to_vault(result, self._target_dir,
                                                 name=name or None, vault_root=vault_root)
        except Exception as exc:            # surface any failure, never crash
            logger.warning("Document import failed for %s: %s", file_path, exc,
                           exc_info=True)
            # describe_error, not str(exc): a containment refusal reads as
            # "…/report.md is outside every vault" — English and developer-facing.
            GLib.idle_add(self._on_file_error, document_import.describe_error(exc))
            return
        GLib.idle_add(self._on_success, str(path))

    def _on_file_error(self, message: str) -> bool:
        if self._closed:
            self.emit("import-failed", message)
        else:
            self._set_file_busy(False)
            self._show_file_error(message)
        return False

    def _on_success(self, path: str) -> bool:
        # Dismissing the dialog only backgrounds the import — the note is still
        # opened and revealed when it finishes, whether or not the dialog is open.
        self.emit("note-imported", path)
        if not self._closed:
            self.close()
        return False

    def _on_error(self, message: str) -> bool:
        if self._closed:
            self.emit("import-failed", message)   # no banner left → toast it
        else:
            self._set_busy(False)
            self._show_error(message)
        return False

    def _on_closed(self, _dialog) -> None:
        # The dialog was dismissed; the worker can't be interrupted, so it runs to
        # completion in the background and its result is delivered via the signals.
        self._closed = True
