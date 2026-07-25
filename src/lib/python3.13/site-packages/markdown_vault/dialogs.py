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
        try:
            count = sum(1 for _ in Path(path).rglob("*"))
        except PermissionError:
            count = -1
        if count > 0:
            body = (
                f"Delete \"{name}\" and all {count} contained items? "
                "This cannot be undone."
            )
        else:
            body = f"Delete empty folder \"{name}\"?"
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
