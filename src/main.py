"""Markdown Vault — application entry point.

Creates the ``Adw.Application`` instance and launches the main window.
Run this module directly with ``python -m src.main``.
"""

import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio

from .app_window import MainWindow


class MarkdownVaultApp(Adw.Application):
    """Top-level application object."""

    def __init__(self) -> None:
        super().__init__(
            application_id="de.hannemann.markdown-vault",
            flags=Gio.ApplicationFlags.FLAGS_NONE,
        )
        self.connect("activate", self._on_activate)

    def _on_activate(self, app: "MarkdownVaultApp") -> None:
        """Present the main window when the application is activated."""
        win = MainWindow(app)
        win.present()


def main() -> int:
    """Application entry point."""
    app = MarkdownVaultApp()
    return app.run(sys.argv)
