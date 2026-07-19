"""Markdown Vault — application entry point.

Creates the ``Adw.Application`` instance and launches the main window.
Run this module directly with ``python3 -m src.main``.
"""

import faulthandler
import logging
import logging.handlers
import os
import signal
import sys

_root = logging.getLogger()
_root.setLevel(logging.INFO)
_fmt = logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")

_stderr_handler = logging.StreamHandler(sys.stderr)
_stderr_handler.setFormatter(_fmt)
_root.addHandler(_stderr_handler)

logger = logging.getLogger("markdown-vault")

THIRD_PARTY_LOGGERS = ("markdown", "pymdownx", "urllib3", "pygments", "xml")

faulthandler.enable(sys.stderr)

if os.environ.get("MARKDOWN_VAULT_DEBUG"):

    def _sigusr1(_sig, _frame):
        faulthandler.dump_traceback()

    signal.signal(signal.SIGUSR1, _sigusr1)

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio

from .app_window import MainWindow
from . import config

_LOGLEVEL_MAP = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}


def set_third_party_loglevel(level_str: str) -> None:
    """Set log level for all third-party loggers (markdown, pymdownx, …)."""
    level = _LOGLEVEL_MAP.get(level_str.lower(), logging.WARNING)
    for prefix in THIRD_PARTY_LOGGERS:
        logging.getLogger(prefix).setLevel(level)
        logging.getLogger(prefix.upper()).setLevel(level)


class MarkdownVaultApp(Adw.Application):
    """Top-level application object."""

    def __init__(self) -> None:
        super().__init__(
            application_id="de.hannemann.markdown-vault",
            flags=Gio.ApplicationFlags.FLAGS_NONE,
        )
        self._window: MainWindow | None = None
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
        self.set_accels_for_action("win.view-edit", ["<Control>1"])
        self.set_accels_for_action("win.view-split", ["<Control>2"])
        self.set_accels_for_action("win.view-render", ["<Control>3"])

    def _on_activate(self, app: "MarkdownVaultApp") -> None:
        """Present the main window when the application is activated."""
        logger.info("activate signal received")

        # Set up file logging (RotatingFileHandler, 1 MB, 3 backups)
        root = logging.getLogger()
        has_file_handler = any(
            isinstance(h, logging.handlers.RotatingFileHandler)
            for h in root.handlers
        )
        if not has_file_handler:
            try:
                config.STATE_DIR.mkdir(parents=True, exist_ok=True)
                file_handler = logging.handlers.RotatingFileHandler(
                    str(config.LOG_FILE),
                    maxBytes=1_000_000,
                    backupCount=3,
                    encoding="utf-8",
                )
                file_handler.setFormatter(
                    logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
                )
                root.addHandler(file_handler)
                logger.info("Log file: %s", config.LOG_FILE)
            except OSError as exc:
                logger.warning("Could not set up file logging: %s", exc)

        # Apply loglevel from config
        settings = config.load_settings()
        loglevel_str = settings.get("loglevel", "info").lower()
        loglevel = _LOGLEVEL_MAP.get(loglevel_str, logging.INFO)
        logging.getLogger().setLevel(loglevel)
        if loglevel == logging.DEBUG:
            logger.debug("Settings loaded: %s", settings)

        # Third-party log level (separate from app level)
        tp_level_str = settings.get("third_party_loglevel", "warning").lower()
        set_third_party_loglevel(tp_level_str)

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
