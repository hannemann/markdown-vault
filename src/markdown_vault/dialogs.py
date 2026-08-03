"""Reusable dialog functions for Markdown Vault.

Each function creates an Adw.AlertDialog, presents it to *parent*, and
calls back with the user's response.  No application state is stored —
all response handling lives in the caller (``app_window``).
"""

import logging
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw, GLib

logger = logging.getLogger(__name__)


# ── Simple informational dialogs ────────────────────────────────────


def show_error(parent: Gtk.Widget, heading: str, body: str) -> None:
    """Show a modal error dialog with a single OK button."""
    dialog = Adw.AlertDialog(heading=heading, body=body)
    dialog.set_prefer_wide_layout(True)
    dialog.add_response("ok", "OK")
    dialog.present(parent)


def show_broken_wikilinks(parent: Gtk.Widget, names: list[str]) -> None:
    """Inform (non-blocking) that a saved file still has broken wikilinks.

    The file has already been saved; this is a notice, not a gatekeeper.
    *names* are user-facing link labels (``stem`` or ``stem|alias``).
    """
    listed = "\n".join(f"– {n}" for n in names)
    dialog = Adw.AlertDialog(
        heading="Broken wikilinks",
        body=f"The file was saved, but these links could not be resolved:\n{listed}",
    )
    dialog.set_prefer_wide_layout(True)
    dialog.add_response("ok", "OK")
    dialog.present(parent)


def show_link_not_found(parent: Gtk.Widget, path_str: str) -> None:
    """Show a dialog when a ``[[wikilink]]`` cannot be resolved."""
    dialog = Adw.AlertDialog(
        heading="Link not found",
        body=f"Could not find a file matching \u201c{path_str}\u201d.",
    )
    dialog.set_prefer_wide_layout(True)
    dialog.add_response("close", "Close")
    dialog.set_response_appearance("close", Adw.ResponseAppearance.SUGGESTED)
    dialog.present(parent)


# ── Text-entry prompt (new file / new folder) ──────────────────────


def prompt_new_item(
    parent: Gtk.Widget,
    heading: str,
    body: str,
    placeholder: str,
    on_response,
) -> None:
    """Show a dialog with a text entry for creating a new file or folder.

    *on_response* is called with the entered name (stripped), or
    ``None`` when the dialog is cancelled or the input is empty.
    """
    dialog = Adw.AlertDialog(heading=heading, body=body)
    dialog.set_prefer_wide_layout(True)
    dialog.add_response("cancel", "Cancel")
    dialog.add_response("create", "Create")
    dialog.set_response_appearance("create", Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("create")
    dialog.set_close_response("cancel")

    entry = Gtk.Entry(placeholder_text=placeholder)
    entry.set_activates_default(True)
    dialog.set_extra_child(entry)

    def _on_response(_dlg, response):
        if response == "create":
            name = entry.get_text().strip()
            on_response(name if name else None)
        else:
            on_response(None)

    dialog.connect("response", _on_response)
    dialog.present(parent)

    def _focus():
        entry.grab_focus_without_selecting()
        return False  # do not repeat
    GLib.idle_add(_focus)


# ── Delete confirmation ────────────────────────────────────────────


def confirm_delete(parent: Gtk.Widget, path: str, on_response) -> None:
    """Show a delete confirmation dialog.

    *on_response* is called with ``True`` (confirmed) or ``False``
    (cancelled).  The body adapts to files vs. directories and shows
    an item count for non-empty directories.
    """
    name = Path(path).name
    is_dir = Path(path).is_dir()

    if is_dir:
        body = f"Delete folder \"{name}\" and all its contents? This cannot be undone."
    else:
        body = f"Delete \"{name}\"?"

    dialog = Adw.AlertDialog(heading="Delete?", body=body)
    dialog.set_prefer_wide_layout(True)
    dialog.add_response("cancel", "Cancel")
    dialog.add_response("delete", "Delete")
    dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
    dialog.set_default_response("cancel")
    dialog.set_close_response("cancel")

    def _on_response(_dlg, response):
        on_response(response == "delete")

    dialog.connect("response", _on_response)
    dialog.present(parent)


# ── File exists confirmation ───────────────────────────────────────


def confirm_file_exists(parent: Gtk.Widget, path: str, on_response) -> None:
    """Show a dialog when a file already exists.

    *on_response* is called with ``True`` (open file) or ``False``
    (cancelled).
    """
    name = Path(path).name
    body = f"The file \"{name}\" already exists. Open it?"

    dialog = Adw.AlertDialog(heading="File Already Exists", body=body)
    dialog.set_prefer_wide_layout(True)
    dialog.add_response("cancel", "Cancel")
    dialog.add_response("open", "Open")
    dialog.set_response_appearance("open", Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("open")
    dialog.set_close_response("cancel")

    def _on_response(_dlg, response):
        on_response(response == "open")

    dialog.connect("response", _on_response)
    dialog.present(parent)


# ── Save / discard / cancel ────────────────────────────────────────


def confirm_discard_unsaved(
    parent: Gtk.Widget,
    dirty_paths: list[str],
    on_response,
) -> None:
    """Show an aggregated save/discard/cancel dialog for dirty tabs.

    *on_response* is called with one of ``"save"``, ``"discard"``,
    or ``"cancel"``.  The ``"close"`` response from the dialog's close
    button is normalised to ``"cancel"``.
    """
    n = len(dirty_paths)
    body_lines = "\n".join(f"\u2013 {Path(p).name}" for p in dirty_paths)

    dialog = Adw.AlertDialog(
        heading="Unsaved Changes",
        body=(
            f"{n} tab{'s' if n > 1 else ''} have unsaved changes:\n\n"
            f"{body_lines}"
        ),
    )
    dialog.set_prefer_wide_layout(True)
    dialog.add_response("discard", "Discard")
    dialog.add_response("cancel", "Cancel")
    dialog.add_response("save", "Save")
    dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
    dialog.set_response_appearance("discard", Adw.ResponseAppearance.DESTRUCTIVE)
    dialog.set_default_response("save")
    dialog.set_close_response("cancel")

    def _on_response(_dlg, resp):
        if resp == "close":
            resp = "cancel"
        on_response(resp)

    dialog.connect("response", _on_response)
    dialog.present(parent)


# ── Vault CRUD dialogs ──────────────────────────────────────────────


def show_rename_vault_dialog(
    parent: Gtk.Widget,
    vault_path: str,
    vault_name: str,
    on_rename,
) -> None:
    """Show a dialog to rename a vault.

    *on_rename* is called with (vault_path, new_name, dialog) when the
    user confirms with a unique name.
    """
    from . import config
    from . import validation

    base_body = "Enter a new name for the vault.\n" + validation.INVALID_VAULT_NAME_HINT
    dialog = Adw.AlertDialog(heading="Rename Vault", body=base_body)
    dialog.set_prefer_wide_layout(True)
    dialog.add_response("cancel", "Cancel")
    dialog.add_response("rename", "Rename")
    dialog.set_response_appearance("rename", Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("rename")
    dialog.set_close_response("cancel")

    entry = Gtk.Entry(placeholder_text="Enter new vault name")
    entry.set_text(vault_name)
    entry.set_activates_default(True)
    dialog.set_extra_child(entry)

    def _check_name():
        # Surface why the button is disabled (invalid chars / collision) in the
        # dialog body so the user knows which characters aren't allowed (R19.4).
        new_name = entry.get_text().strip()
        err = validation.validate_vault_name(new_name)
        if err is None and new_name == vault_name:
            dialog.set_body(base_body)  # current name, unchanged — no error
            dialog.set_response_enabled("rename", False)
            return
        if err is None:
            for v in config.load_vaults():
                if v["path"] != vault_path and v["name"] == new_name:
                    err = "A vault with this name already exists."
                    break
        dialog.set_body(err or base_body)
        dialog.set_response_enabled("rename", err is None)

    entry.connect("changed", lambda *_: _check_name())
    _check_name()

    def _on_response(_dlg, response):
        if response == "rename":
            new_name = entry.get_text().strip()
            if new_name and new_name != vault_name:
                on_rename(vault_path, new_name, dialog)

    dialog.connect("response", _on_response)
    dialog.present(parent)

    def _focus():
        entry.grab_focus_without_selecting()
        return False
    GLib.idle_add(_focus)


def show_remove_vault_dialog(
    parent: Gtk.Widget,
    vault_path: str,
    vault_name: str,
    on_remove,
) -> None:
    """Show a confirmation dialog to remove a vault.

    *on_remove* is called with *vault_path* when the user confirms.
    """
    msg = Adw.AlertDialog.new(
        f'Remove Vault "{vault_name}"?',
        "The files on disk are left untouched.",
    )
    msg.add_response("cancel", "Cancel")
    msg.add_response("remove", "Remove")
    # Appearance must be set AFTER the response exists, else Adw emits a
    # g_critical and the destructive styling is silently dropped (R19.5).
    msg.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
    msg.set_default_response("cancel")
    msg.set_close_response("cancel")

    def _on_response(_dialog, response: str) -> None:
        if response == "remove":
            on_remove(vault_path)

    msg.connect("response", _on_response)
    msg.present(parent)


def show_add_vault_name_dialog(
    parent: Gtk.Widget,
    vault_path: str,
    default_name: str,
    on_add,
) -> None:
    """Show a dialog to resolve a vault name collision.

    *on_add* is called with (vault_path, default_name, new_name, dialog)
    when the user confirms with a unique name.
    """
    from . import config
    from . import validation

    base_body = "Enter a unique vault name.\n" + validation.INVALID_VAULT_NAME_HINT
    dialog = Adw.AlertDialog(
        heading="Vault Name Collision",
        body=base_body,
    )
    dialog.set_prefer_wide_layout(True)
    dialog.add_response("cancel", "Cancel")
    dialog.add_response("add", "Add")
    dialog.set_response_appearance("add", Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("add")
    dialog.set_close_response("cancel")

    entry = Gtk.Entry(placeholder_text="Enter a unique vault name")
    entry.set_text(default_name)
    entry.set_activates_default(True)
    dialog.set_extra_child(entry)

    def _check_name():
        # Show the reason (invalid chars / collision) in the body (R19.4).
        new_name = entry.get_text().strip()
        err = validation.validate_vault_name(new_name)
        if err is None and any(v["name"] == new_name for v in config.load_vaults()):
            err = "A vault with this name already exists."
        dialog.set_body(err or base_body)
        dialog.set_response_enabled("add", err is None)

    entry.connect("changed", lambda *_: _check_name())
    _check_name()

    def _on_response(_dlg, response):
        if response == "add":
            new_name = entry.get_text().strip()
            if new_name:
                on_add(vault_path, default_name, new_name, dialog)

    dialog.connect("response", _on_response)
    dialog.present(parent)

    def _focus():
        entry.grab_focus_without_selecting()
        return False
    GLib.idle_add(_focus)
