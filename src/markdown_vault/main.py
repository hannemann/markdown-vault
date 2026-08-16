"""Markdown Vault — application entry point.

Creates the ``Adw.Application`` instance and launches the main window.
Run this module directly with ``python3 -m src.main``.
"""

import faulthandler
import logging
import os
import signal
import sys

from markdown_vault.core import logging_setup

# Establish logging as the very first action so log files, console routing
# and the fd redirect are in place before any other module is imported —
# regardless of how the app was launched (terminal, gtk-launch, app.sh).
logging_setup.init(None)

from markdown_vault.core import config

_settings = config.load_settings()
config.apply_webkit_env(_settings)

# Apply the configured log levels now that settings are available.
logging_setup.init(_settings)
logging_setup.set_third_party_loglevel(
    _settings.get("third_party_loglevel", "warning")
)

logger = logging.getLogger("markdown-vault")

if os.environ.get("MARKDOWN_VAULT_DEBUG"):

    def _sigusr1(_sig, _frame):
        faulthandler.dump_traceback()

    signal.signal(signal.SIGUSR1, _sigusr1)

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio

from markdown_vault.app.app_window import MainWindow


class MarkdownVaultApp(Adw.Application):
    """Top-level application object."""

    def __init__(self) -> None:
        super().__init__(
            application_id="de.hannemann.markdown-vault",
            flags=Gio.ApplicationFlags.FLAGS_NONE,
        )
        self._window: MainWindow | None = None
        self._debug_control = None
        self.connect("activate", self._on_activate)
        self.connect("shutdown", lambda *_: logger.info("shutdown signal received"))
        self._setup_accels()
        self._setup_signals()

    def _setup_signals(self) -> None:
        """Install Unix signal handlers for clean shutdown."""

        def _sigterm(_sig, _frame):
            logger.info("SIGTERM received, closing window")
            if self._window is not None:
                self._window.close()  # triggers close-request → saves session

        signal.signal(signal.SIGTERM, _sigterm)
        signal.signal(signal.SIGINT, _sigterm)  # Ctrl+C behaves same

    def _setup_accels(self) -> None:
        """Register global keyboard shortcuts."""
        self.set_accels_for_action("win.toggle-zen", ["<Control>b"])
        self.set_accels_for_action("win.toggle-zen-total", ["<Shift><Control>b"])
        self.set_accels_for_action("win.toggle-search", ["<Shift><Control>f"])
        self.set_accels_for_action("win.quick-open", ["<Control>space"])
        self.set_accels_for_action("win.find-in-view", ["<Control>f"])
        self.set_accels_for_action("win.replace-in-view", ["<Control>r"])
        self.set_accels_for_action("win.save", ["<Control>s"])
        self.set_accels_for_action("win.close-tab", ["<Control>w"])
        self.set_accels_for_action("win.new-file", ["<Control>n"])
        self.set_accels_for_action("win.preferences", ["<Control>comma"])
        self.set_accels_for_action("win.zoom-in", ["<Control>plus", "<Control>equal"])
        self.set_accels_for_action("win.zoom-out", ["<Control>minus"])
        self.set_accels_for_action("win.zoom-reset", ["<Control>0"])
        self.set_accels_for_action("win.toggle-help", ["F1"])
        self.set_accels_for_action("win.view-edit", ["<Control>1"])
        self.set_accels_for_action("win.view-split", ["<Control>2"])
        self.set_accels_for_action("win.view-render", ["<Control>3"])
        self.set_accels_for_action("win.view-graph", ["<Control>4"])

    def do_dbus_register(self, connection, object_path: str) -> bool:
        """Chain up (GTK exports actions/menus here), then register the dev-only
        debug interface when MDV_DEBUG_CONTROL is set."""
        ok = Adw.Application.do_dbus_register(self, connection, object_path)
        try:
            from markdown_vault.core import debug_control
            self._debug_control = debug_control.maybe_register(
                self, connection, object_path)
        except Exception:
            logger.warning("failed to register debug control interface", exc_info=True)
        return ok

    def do_dbus_unregister(self, connection, object_path: str) -> None:
        if self._debug_control is not None:
            self._debug_control.unregister()
            self._debug_control = None
        Adw.Application.do_dbus_unregister(self, connection, object_path)

    def _on_activate(self, app: "MarkdownVaultApp") -> None:
        """Present the main window when the application is activated."""
        logger.info("activate signal received")
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("Settings loaded: %s", _settings)

        win = MainWindow(app)
        self._window = win
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
