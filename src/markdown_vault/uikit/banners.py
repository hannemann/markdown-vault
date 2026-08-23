"""Markdown Vault — reusable banner widgets.

Provides a ``BannerBox`` widget that can display an icon, text, and
arbitrary action buttons.  Two instances per tab (warning + error) are
wrapped in ``Gtk.Revealer`` for slide-down animation.

Banner types determine the default icon and CSS class:

- ``"warning"``: ``dialog-warning-symbolic``, ``.banner-warning``
- ``"error"``:   ``dialog-error-symbolic``,   ``.banner-error``
- ``"info"``:    ``dialog-information-symbolic``, ``.banner-info``
- ``"success"``: ``object-select-symbolic``,   ``.banner-success``
"""

import logging

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, GObject

from markdown_vault.core.i18n import _

logger = logging.getLogger(__name__)

_BANNER_TYPES: dict[str, tuple[str, str]] = {
    "warning": ("dialog-warning-symbolic", "banner-warning"),
    "error":   ("dialog-error-symbolic",   "banner-error"),
    "info":    ("dialog-information-symbolic", "banner-info"),
    "success": ("object-select-symbolic",   "banner-success"),
}


class BannerBox(Gtk.Box):
    """A horizontal banner with icon, label, and dynamic buttons.

    The *banner_type* determines the default icon and CSS class.
    Pass *icon_name* to override the default icon.

    Buttons are added via :meth:`add_button` and trigger arbitrary
    callbacks.  Call :meth:`clear_buttons` to remove all action buttons.

    Signals:
        dismissed: Emitted when the dismiss button is clicked.
    """

    __gsignals__ = {
        "dismissed": (GObject.SignalFlags.RUN_LAST, None, ()),
    }

    def __init__(
        self,
        banner_type: str = "warning",
        icon_name: str | None = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.set_margin_top(2)
        self.set_margin_bottom(2)
        self.set_margin_start(6)
        self.set_margin_end(6)

        default_icon, css_class = _BANNER_TYPES.get(
            banner_type, _BANNER_TYPES["warning"]
        )
        self._banner_type = banner_type
        self.add_css_class("banner")
        self.add_css_class(css_class)

        self._icon = Gtk.Image.new_from_icon_name(icon_name or default_icon)
        self._icon.set_margin_end(6)
        self.append(self._icon)

        self._label = Gtk.Label()
        self._label.set_xalign(0)
        self._label.set_hexpand(True)
        self._label.set_margin_end(6)
        self.append(self._label)

        self._button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.append(self._button_box)

        self._dismiss_btn = Gtk.Button(label=_("Dismiss"))
        self._dismiss_btn.add_css_class("flat")
        self._dismiss_btn.connect("clicked", lambda _: self.emit("dismissed"))
        self._button_box.append(self._dismiss_btn)

    def set_text(self, text: str) -> None:
        """Update the banner text."""
        self._label.set_text(text)

    def set_icon(self, icon_name: str) -> None:
        """Update the banner icon."""
        self._icon.set_from_icon_name(icon_name)

    def add_button(self, label: str, callback, css_class: str = "flat") -> Gtk.Button:
        """Add an action button before the dismiss button.

        *callback* is called with no arguments when clicked.
        Returns the created button for further configuration.
        """
        btn = Gtk.Button(label=label)
        btn.add_css_class(css_class)
        btn.connect("clicked", lambda _: callback())
        self._button_box.append(btn)
        return btn

    def clear_buttons(self) -> None:
        """Remove all buttons (including dismiss)."""
        while child := self._button_box.get_first_child():
            self._button_box.remove(child)

    def reset(
        self,
        banner_type: str | None = None,
        icon_name: str | None = None,
    ) -> None:
        """Reset banner to default state: icon, empty text, no extra buttons.

        If *banner_type* is given, switch to that type's defaults.
        *icon_name* overrides the default icon.
        """
        if banner_type is not None:
            self._banner_type = banner_type

        default_icon, css_class = _BANNER_TYPES.get(
            self._banner_type, _BANNER_TYPES["warning"]
        )

        self._icon.set_from_icon_name(icon_name or default_icon)
        self._label.set_text("")
        self.clear_buttons()
        self._dismiss_btn = Gtk.Button(label=_("Dismiss"))
        self._dismiss_btn.add_css_class("flat")
        self._dismiss_btn.connect("clicked", lambda _: self.emit("dismissed"))
        self._button_box.append(self._dismiss_btn)
        # Update CSS classes.
        for cls in list(self.get_css_classes()):
            if cls.startswith("banner-"):
                self.remove_css_class(cls)
        self.add_css_class(css_class)


def create_banner(
    banner_type: str = "warning",
    icon_name: str | None = None,
) -> tuple[Gtk.Revealer, BannerBox]:
    """Create a Revealer wrapping a BannerBox.

    *banner_type* sets the default icon and CSS class.
    *icon_name* optionally overrides the default icon.

    Returns ``(revealer, banner_box)``.  The caller connects
    ``banner_box.connect("dismissed", ...)`` to hide the revealer.
    """
    banner = BannerBox(banner_type=banner_type, icon_name=icon_name)

    revealer = Gtk.Revealer()
    revealer.set_child(banner)
    revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)

    return revealer, banner
