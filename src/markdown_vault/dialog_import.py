"""Import dialog — right-click a vault/folder → Import… to bring content into it.

A two-step :class:`Adw.Dialog`: pick a source (Web Page / File — File is a later,
document-import feature), then a source-specific form. The web branch takes a URL,
an optional name and a download-images switch; the fetch/extract/save runs on a
worker thread so the UI never blocks and the result is marshalled back with
:func:`GLib.idle_add`. On success it emits ``note-imported`` with the new file's
path; the window opens it and reveals it in the tree.

This dialog is deliberately not web-specific: when document import lands, its form
moves into ``dialog_import_document`` (and the web form into ``dialog_import_web``)
while this file keeps the chooser shell.
"""

import logging
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw, GObject, GLib

from . import web_import

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

    def __init__(self, target_dir: str):
        super().__init__()
        self._target_dir = target_dir
        self._busy = False
        self._closed = False
        self.set_title("Import")
        self.set_content_width(480)
        self.connect("closed", self._on_closed)

        self._stack = Gtk.Stack()
        self._stack.add_named(self._build_chooser(), "chooser")
        self._stack.add_named(self._build_web_form(), "web")

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        toolbar.set_content(self._stack)
        self.set_child(toolbar)

    # ── Step 1: source chooser ─────────────────────────────────────────
    def _build_chooser(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      margin_top=18, margin_bottom=18, margin_start=18, margin_end=18)
        group = Adw.PreferencesGroup(title="What do you want to import?")

        web_row = Adw.ActionRow(title="Web Page",
                                subtitle="Fetch a URL as a Markdown note",
                                activatable=True)
        web_row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        web_row.connect("activated", self._on_choose_web)
        group.add(web_row)

        file_row = Adw.ActionRow(title="File",
                                 subtitle="Import a document (coming soon)",
                                 sensitive=False)
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
        self._url_row = Adw.EntryRow(title="URL")
        self._url_row.connect("changed", lambda *_: self._validate())
        self._url_row.connect("entry-activated", lambda *_: self._on_import())
        group.add(self._url_row)

        self._name_row = Adw.EntryRow(title="Name (optional)")
        group.add(self._name_row)

        self._dl_row = Adw.SwitchRow(title="Download images",
                                     subtitle="Save images into attachments/<note>/")
        group.add(self._dl_row)
        box.append(group)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8,
                          halign=Gtk.Align.END)
        self._spinner = Gtk.Spinner()
        actions.append(self._spinner)
        self._import_btn = Gtk.Button(label="Import")
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
            self._show_error("Enter a valid http(s) URL.")
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
                raise ValueError("Nothing extractable on that page.")
            path = web_import.save_to_vault(result, self._target_dir,
                                            download_images=download,
                                            name=name or None)
        except Exception as exc:  # surface any failure to the user, never crash
            logger.warning("Web import failed for %s: %s", url, exc, exc_info=True)
            GLib.idle_add(self._on_error, str(exc))
            return
        GLib.idle_add(self._on_success, str(path))

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
