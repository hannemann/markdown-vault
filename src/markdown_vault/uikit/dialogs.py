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

from markdown_vault.core.i18n import _, ngettext

logger = logging.getLogger(__name__)


# ── File-dialog result helpers ──────────────────────────────────────


def dialog_cancelled(error: GLib.Error) -> bool:
    """Whether a file/folder-dialog ``*_finish`` ``GLib.Error`` is a user
    cancel/dismiss rather than a real failure.

    ``Gtk.FileDialog`` finishers raise the same error type for a user cancel and
    for a genuine portal/backend failure, so a caller must split the two: stay
    silent on a cancel, but log and surface a real failure instead of dropping
    the user's action without a trace.
    """
    quark = Gtk.DialogError.quark()
    return (error.matches(quark, Gtk.DialogError.DISMISSED)
            or error.matches(quark, Gtk.DialogError.CANCELLED))


# ── Simple informational dialogs ────────────────────────────────────


def show_error(parent: Gtk.Widget, heading: str, body: str) -> None:
    """Show a modal error dialog with a single OK button."""
    dialog = Adw.AlertDialog(heading=heading, body=body)
    dialog.set_prefer_wide_layout(True)
    dialog.add_response("ok", _("OK"))
    dialog.present(parent)


def show_broken_wikilinks(parent: Gtk.Widget, names: list[str]) -> None:
    """Inform (non-blocking) that a saved file still has broken wikilinks.

    The file has already been saved; this is a notice, not a gatekeeper.
    *names* are user-facing link labels (``stem`` or ``stem|alias``).
    """
    listed = "\n".join(f"– {n}" for n in names)
    dialog = Adw.AlertDialog(
        heading=_("Broken wikilinks"),
        body=_("The file was saved, but these links could not be resolved:\n"
               "{items}").format(items=listed),
    )
    dialog.set_prefer_wide_layout(True)
    dialog.add_response("ok", _("OK"))
    dialog.present(parent)


def show_link_not_found(parent: Gtk.Widget, path_str: str) -> None:
    """Show a dialog when a ``[[wikilink]]`` cannot be resolved."""
    dialog = Adw.AlertDialog(
        heading=_("Link not found"),
        body=_("Could not find a file matching \u201c{path}\u201d.").format(path=path_str),
    )
    dialog.set_prefer_wide_layout(True)
    dialog.add_response("close", _("Close"))
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
    dialog.add_response("cancel", _("Cancel"))
    dialog.add_response("create", _("Create"))
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


def prompt_rename(parent: Gtk.Widget, current_name: str, on_response) -> None:
    """Show a dialog with a text entry to rename a file or folder.

    *on_response* is called with the entered name (stripped), or ``None``
    when the dialog is cancelled or the input is empty/unchanged.
    """
    dialog = Adw.AlertDialog(
        heading=_("Rename"), body=_("Rename “{name}”").format(name=current_name))
    dialog.set_prefer_wide_layout(True)
    dialog.add_response("cancel", _("Cancel"))
    dialog.add_response("rename", _("Rename"))
    dialog.set_response_appearance("rename", Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("rename")
    dialog.set_close_response("cancel")

    entry = Gtk.Entry(text=current_name)
    entry.set_activates_default(True)
    dialog.set_extra_child(entry)

    def _on_response(_dlg, response):
        if response == "rename":
            name = entry.get_text().strip()
            on_response(name if name and name != current_name else None)
        else:
            on_response(None)

    dialog.connect("response", _on_response)
    dialog.present(parent)

    def _focus():
        entry.grab_focus()
        # Preselect the basename (before the extension) for quick editing.
        dot = current_name.rfind(".")
        entry.select_region(0, dot if dot > 0 else len(current_name))
        return False
    GLib.idle_add(_focus)


_VAULT_ICON_PALETTE = [
    "🗄️", "🗂️", "🗃️", "📁", "📚", "📓", "📔", "🧠",
    "🔖", "🏛️", "💎", "🔒", "⭐", "🌐", "🔬", "💼",
    "🎯", "📦", "🗒️", "🧩", "🚀", "🏰", "📜", "🏷️",
    "🧪", "🪴", "🗺️", "🔮",
]


def show_vault_icon_dialog(
    parent: Gtk.Widget, current_icon: str, current_mono: bool, on_choose,
) -> None:
    """Pick a vault icon from a palette or free text, plus a monochrome toggle.

    *on_choose* is called with ``(icon, mono)`` — *icon* is the chosen
    emoji/character (stripped) or ``None`` to reset to the default, *mono* is a
    bool.  Not called on cancel.
    """
    dialog = Adw.AlertDialog(
        heading=_("Vault Icon"), body=_("Pick a symbol or type your own."))
    dialog.add_response("cancel", _("Cancel"))
    dialog.add_response("apply", _("Apply"))
    dialog.set_response_appearance("apply", Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("apply")
    dialog.set_close_response("cancel")

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
    entry = Gtk.Entry(text=current_icon or "")
    entry.set_activates_default(True)
    entry.set_placeholder_text(_("e.g. 🗄️ (empty = default)"))

    flow = Gtk.FlowBox()
    flow.set_max_children_per_line(8)
    flow.set_selection_mode(Gtk.SelectionMode.NONE)
    flow.set_row_spacing(2)
    flow.set_column_spacing(2)
    for emoji in _VAULT_ICON_PALETTE:
        btn = Gtk.Button(label=emoji)
        btn.add_css_class("flat")
        btn.add_css_class("vault-icon-choice")
        btn.connect("clicked", lambda _b, e=emoji: entry.set_text(e))
        flow.append(btn)

    mono_check = Gtk.CheckButton(label=_("Monochrome"))
    mono_check.set_active(bool(current_mono))

    box.append(flow)
    box.append(entry)
    box.append(mono_check)
    dialog.set_extra_child(box)

    def _on_response(_dlg, response):
        if response == "apply":
            on_choose(entry.get_text().strip() or None, mono_check.get_active())

    dialog.connect("response", _on_response)
    dialog.present(parent)

    def _focus():
        entry.grab_focus_without_selecting()
        return False
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
        body = _("Delete folder \"{name}\" and all its contents? "
                 "This cannot be undone.").format(name=name)
    else:
        body = _("Delete \"{name}\"?").format(name=name)

    dialog = Adw.AlertDialog(heading=_("Delete?"), body=body)
    dialog.set_prefer_wide_layout(True)
    dialog.add_response("cancel", _("Cancel"))
    dialog.add_response("delete", _("Delete"))
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
    body = _("The file \"{name}\" already exists. Open it?").format(name=name)

    dialog = Adw.AlertDialog(heading=_("File Already Exists"), body=body)
    dialog.set_prefer_wide_layout(True)
    dialog.add_response("cancel", _("Cancel"))
    dialog.add_response("open", _("Open"))
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
        heading=_("Unsaved Changes"),
        body=ngettext(
            "{n} tab has unsaved changes:\n\n{items}",
            "{n} tabs have unsaved changes:\n\n{items}",
            n,
        ).format(n=n, items=body_lines),
    )
    dialog.set_prefer_wide_layout(True)
    dialog.add_response("discard", _("Discard"))
    dialog.add_response("cancel", _("Cancel"))
    dialog.add_response("save", _("Save"))
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
    from markdown_vault.core import config
    from markdown_vault.core import validation

    base_body = _("Enter a new name for the vault.") + "\n" + validation.INVALID_VAULT_NAME_HINT
    dialog = Adw.AlertDialog(heading=_("Rename Vault"), body=base_body)
    dialog.set_prefer_wide_layout(True)
    dialog.add_response("cancel", _("Cancel"))
    dialog.add_response("rename", _("Rename"))
    dialog.set_response_appearance("rename", Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("rename")
    dialog.set_close_response("cancel")

    entry = Gtk.Entry(placeholder_text=_("Enter new vault name"))
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
                    err = _("A vault with this name already exists.")
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
        _('Remove Vault "{name}"?').format(name=vault_name),
        _("The files on disk are left untouched."),
    )
    msg.add_response("cancel", _("Cancel"))
    msg.add_response("remove", _("Remove"))
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
    from markdown_vault.core import config
    from markdown_vault.core import validation

    base_body = _("Enter a unique vault name.") + "\n" + validation.INVALID_VAULT_NAME_HINT
    dialog = Adw.AlertDialog(
        heading=_("Vault Name Collision"),
        body=base_body,
    )
    dialog.set_prefer_wide_layout(True)
    dialog.add_response("cancel", _("Cancel"))
    dialog.add_response("add", _("Add"))
    dialog.set_response_appearance("add", Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("add")
    dialog.set_close_response("cancel")

    entry = Gtk.Entry(placeholder_text=_("Enter a unique vault name"))
    entry.set_text(default_name)
    entry.set_activates_default(True)
    dialog.set_extra_child(entry)

    def _check_name():
        # Show the reason (invalid chars / collision) in the body (R19.4).
        new_name = entry.get_text().strip()
        err = validation.validate_vault_name(new_name)
        if err is None and any(v["name"] == new_name for v in config.load_vaults()):
            err = _("A vault with this name already exists.")
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
