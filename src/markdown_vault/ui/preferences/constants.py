"""Preferences — values shared by the dialog shell and its page modules.

Kept in one module so a page does not have to import a sibling page (the package
edge is ``ui → ui.preferences``; a module here importing back up would close a
cycle and turn the layering guard red).
"""

import logging

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")

from gi.repository import Gtk, Gdk

from markdown_vault.core.i18n import _

_VIEW_MODES = {"edit": _("Edit"), "render": _("Render"), "split": _("Split")}
_LOGLEVELS = {"debug": _("Debug"), "info": _("Info"), "warning": _("Warning"),
              "error": _("Error")}
_GLIB_LOGLEVELS = {
    "all": _("All (debug+)"),
    "warning": _("Warning and up"),
    "critical": _("Critical and up"),
    "error": _("Error only"),
}
_LOGLEVEL_MAP = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}

_RELEVANT_MODS = (
    Gdk.ModifierType.SHIFT_MASK
    | Gdk.ModifierType.CONTROL_MASK
    | Gdk.ModifierType.ALT_MASK
    | Gdk.ModifierType.SUPER_MASK
)


def _accel_to_label(accel: str) -> str:
    """Convert a GTK accelerator string to a human-readable label."""
    if not accel:
        return "None"
    ok, keyval, mods = Gtk.accelerator_parse(accel)
    if not ok or keyval == 0:
        return accel
    parts = []
    if mods & Gdk.ModifierType.SHIFT_MASK:
        parts.append("Shift")
    if mods & Gdk.ModifierType.CONTROL_MASK:
        parts.append("Ctrl")
    if mods & Gdk.ModifierType.ALT_MASK:
        parts.append("Alt")
    if mods & Gdk.ModifierType.SUPER_MASK:
        parts.append("Super")
    key_name = Gdk.keyval_name(keyval)
    if key_name:
        parts.append(key_name.capitalize())
    return "+".join(parts)
