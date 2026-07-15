"""Markdown Vault — application entry point.

Creates the ``Adw.Application`` instance and launches the main window.
Run this module directly with ``python3 -m src.main``.
"""

import faulthandler
import logging
import os
import sys

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("markdown-vault")

faulthandler.enable(sys.stderr)

if os.environ.get("MARKDOWN_VAULT_DEBUG"):
    import signal

    def _sigusr1(_sig, _frame):
        faulthandler.dump_traceback()

    signal.signal(signal.SIGUSR1, _sigusr1)

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
        self.connect("shutdown", lambda *_: logger.info("shutdown signal received"))
        self._setup_accels()

    def _setup_accels(self) -> None:
        """Register global keyboard shortcuts."""
        self.set_accels_for_action("win.toggle-sidebar", ["<Control>b"])
        self.set_accels_for_action("win.toggle-search", ["<Control>f"])
        self.set_accels_for_action("win.save", ["<Control>s"])
        self.set_accels_for_action("win.close-tab", ["<Control>w"])
        self.set_accels_for_action("win.new-file", ["<Control>n"])
        self.set_accels_for_action("win.preferences", ["<Control>comma"])
        self.set_accels_for_action("win.zoom-in", ["<Control>plus", "<Control>equal"])
        self.set_accels_for_action("win.zoom-out", ["<Control>minus"])
        self.set_accels_for_action("win.zoom-reset", ["<Control>0"])
        self.set_accels_for_action("win.toggle-help", ["<Control>space"])

    def _on_activate(self, app: "MarkdownVaultApp") -> None:
        """Present the main window when the application is activated."""
        logger.info("activate signal received")
        win = MainWindow(app)
        win.present()
        logger.info("main window presented")


def main() -> int:
    """Application entry point."""
    logger.info("app starting (pid=%d)", os.getpid())
    app = MarkdownVaultApp()
    try:
        ret = app.run(sys.argv)
        logger.info("app.run() returned %s", ret)
        return ret
    except KeyboardInterrupt:
        logger.info("interrupted by user")
        app.quit()
        return 0
    except SystemExit as e:
        logger.info("exit requested: %s", e.code)
        return e.code if isinstance(e.code, int) else 0


if __name__ == "__main__":
    raise SystemExit(main())
