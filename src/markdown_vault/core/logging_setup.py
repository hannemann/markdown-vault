"""Central logging setup for Markdown Vault.

Sets up:
- Two rotated log files in the XDG state dir (``core.paths.STATE_DIR``, by default
  ~/.local/state/de.hannemann.markdown-vault/; inside a Flatpak the sandbox's own
  state dir):
  - markdown-vault.log         (level <= INFO)
  - markdown-vault.stderr.log  (level >= WARNING)
- Console logging (stdout <= INFO, stderr >= WARNING) when the app is run
  from a terminal.
- An fd redirect that keeps fd 1 / fd 2 pointing at the log files when there
  is no terminal (desktop/gtk-launch/app.sh launches), so native and
  child-process output (WebKit subprocesses inherit fd 2) is captured.
- faulthandler, so crash dumps land in the stderr log.
- A GLib log handler (Gtk/Gdk/JavascriptCore/WebKit/GtkSource/Adwaita).
"""

import faulthandler
import logging
import logging.handlers
import os
import sys
import threading
import traceback

from gi.repository import GLib

from markdown_vault.core import paths   # stdlib-only, safe this early

_STATE_DIR = str(paths.STATE_DIR)
_LOG_FILE = os.path.join(_STATE_DIR, "markdown-vault.log")
_STDERR_LOG_FILE = os.path.join(_STATE_DIR, "markdown-vault.stderr.log")
_LLAMA_LOG_FILE = os.path.join(_STATE_DIR, "markdown-vault.llama.log")

_FORMAT = "%(asctime)s %(name)s %(levelname)s %(message)s"

THIRD_PARTY_LOGGERS = ("markdown", "pymdownx", "urllib3", "pygments", "xml")

_LOGLEVEL_MAP = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}

# Global state.
# GLib log levels are bitflags, not an ordinal scale, so rank them by severity
# to implement a real "this level and above" threshold.
_GLIB_LEVEL_RANK = {
    GLib.LogLevelFlags.LEVEL_DEBUG: 10,
    GLib.LogLevelFlags.LEVEL_INFO: 20,
    GLib.LogLevelFlags.LEVEL_MESSAGE: 30,
    GLib.LogLevelFlags.LEVEL_WARNING: 40,
    GLib.LogLevelFlags.LEVEL_CRITICAL: 50,
    GLib.LogLevelFlags.LEVEL_ERROR: 60,
}
# Config string -> minimum severity rank that still gets logged.
_GLIB_THRESHOLD = {
    "all": 0,
    "debug": 10,
    "info": 20,
    "message": 30,
    "warning": 40,
    "critical": 50,
    "error": 60,
}
_DEFAULT_GLIB_RANK = _GLIB_THRESHOLD["warning"]

_glib_min_rank = _DEFAULT_GLIB_RANK  # messages below this rank are dropped
_glib_handler_id = []  # Handler IDs for each domain
_installed_handlers = []
_file_setup_done = False
_console_setup_done = False


def _glib_severity_rank(log_level) -> int:
    """Return the severity rank of a GLib log level (higher = more severe)."""
    lvl = int(log_level) & int(GLib.LogLevelFlags.LEVEL_MASK)
    best = 0
    for flag, rank in _GLIB_LEVEL_RANK.items():
        if lvl & int(flag):
            best = max(best, rank)
    # A message with no known level bit is treated as top severity so nothing
    # is ever silently dropped.
    return best or _GLIB_LEVEL_RANK[GLib.LogLevelFlags.LEVEL_ERROR]


class _MaxLevelFilter(logging.Filter):
    """Pass records at or below ``level``."""

    def __init__(self, level):
        self._level = level

    def filter(self, record):
        return record.levelno <= self._level


class _MinLevelFilter(logging.Filter):
    """Pass records at or above ``level``."""

    def __init__(self, level):
        self._level = level

    def filter(self, record):
        return record.levelno >= self._level


class _FdRedirectRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """RotatingFileHandler that keeps a target fd pointing at the log file.

    Native/child processes that inherit the fd write into the log file, and
    the redirect is re-established after every rollover.
    """

    def __init__(self, filename, fd, mode="a", maxBytes=0, backupCount=0,
                 encoding=None, delay=False):
        self._target_fd = fd
        self._redirect_fd = False
        super().__init__(
            filename, mode, maxBytes, backupCount, encoding, delay
        )

    def _apply_redirect(self):
        if self.stream is None:
            self.stream = self._open()
        self.stream.flush()
        os.dup2(self.stream.fileno(), self._target_fd)

    def doRollover(self):
        super().doRollover()
        if self._redirect_fd:
            self._apply_redirect()

    def close(self):
        self._redirect_fd = False
        super().close()


class _StderrRotatingFileHandler(_FdRedirectRotatingFileHandler):
    """Rotating stderr log handler that may also redirect fd 2 to the file."""

    def __init__(self, filename, mode="a", maxBytes=0, backupCount=0,
                 encoding=None, delay=False, redirect_fd=True):
        super().__init__(
            filename, 2, mode, maxBytes, backupCount, encoding, delay
        )
        self._redirect_fd = redirect_fd
        if redirect_fd:
            self._apply_redirect()


class _StdoutRotatingFileHandler(_FdRedirectRotatingFileHandler):
    """Rotating stdout log handler that may also redirect fd 1 to the file."""

    def __init__(self, filename, mode="a", maxBytes=0, backupCount=0,
                 encoding=None, delay=False, redirect_fd=True):
        super().__init__(
            filename, 1, mode, maxBytes, backupCount, encoding, delay
        )
        self._redirect_fd = redirect_fd
        if redirect_fd:
            self._apply_redirect()


def _glib_log_handler(log_domain, log_level, message, user_data=None):
    """GLib log handler — routes through the Python logger."""
    # Real "level and above" threshold: drop anything below the configured
    # minimum severity (handlers themselves catch every level).
    if _glib_severity_rank(log_level) < _glib_min_rank:
        return
    timestamp = GLib.DateTime.new_now_local().format("%Y-%m-%d %H:%M:%S")
    msg = f"[{timestamp}] [{log_domain}] {log_level}: {message}"

    # Map GLib level to Python logging level.
    if log_level & GLib.LogLevelFlags.LEVEL_CRITICAL:
        log_level_py = logging.CRITICAL
    elif log_level & GLib.LogLevelFlags.LEVEL_WARNING:
        log_level_py = logging.WARNING
    elif log_level & GLib.LogLevelFlags.LEVEL_ERROR:
        log_level_py = logging.ERROR
    elif log_level & GLib.LogLevelFlags.LEVEL_MESSAGE:
        log_level_py = logging.INFO
    elif log_level & GLib.LogLevelFlags.LEVEL_INFO:
        log_level_py = logging.INFO
    else:
        log_level_py = logging.DEBUG

    logger = logging.getLogger("markdown-vault")
    logger.log(log_level_py, msg)

    # Only critical messages get a C backtrace plus Python thread stacks.
    if log_level & GLib.LogLevelFlags.LEVEL_CRITICAL:
        try:
            import ctypes
            import ctypes.util

            libc_path = ctypes.util.find_library("c")
            if not libc_path:
                raise ValueError("libc not found")
            libc = ctypes.CDLL(libc_path, use_errno=True)

            max_frames = 64
            backtrace = (ctypes.c_void_p * max_frames)()
            libc.backtrace.restype = ctypes.c_int
            n_frames = libc.backtrace(backtrace, max_frames)

            libc.backtrace_symbols.argtypes = [
                ctypes.POINTER(ctypes.c_void_p), ctypes.c_int
            ]
            libc.backtrace_symbols.restype = ctypes.POINTER(ctypes.c_char_p)
            symbols = libc.backtrace_symbols(backtrace, n_frames)

            trace_lines = []
            for i in range(n_frames):
                symbol = symbols[i]
                if symbol:
                    trace_lines.append(
                        f"  {i}: {symbol.decode('utf-8', errors='replace')}"
                    )

            if trace_lines:
                logger.log(log_level_py, "C backtrace:\n%s", "\n".join(trace_lines))
            else:
                raise ValueError("No symbols")
        except Exception:
            logger.log(
                log_level_py, "C backtrace failed (no traceback)", exc_info=False
            )

        thread_stacks = []
        for thread_id, frame in sys._current_frames().items():
            thread_name = None
            for t in threading.enumerate():
                if t.ident == thread_id:
                    thread_name = t.name
                    break
            thread_name = thread_name or f"Thread-{thread_id}"

            stack_lines = traceback.format_stack(frame)
            thread_stacks.append(f"\n=== {thread_name} (id={thread_id}) ===")
            thread_stacks.extend(stack_lines)

        stack_text = "\n".join(thread_stacks)
        logger.log(log_level_py, "Python stacks:\n%s", stack_text)


def _setup_file_logging(redirect_stdout=True, redirect_stderr=True):
    """Set up the two rotated log files."""
    global _file_setup_done
    if _file_setup_done:
        return
    _file_setup_done = True
    try:
        os.makedirs(_STATE_DIR, exist_ok=True)
        fmt = logging.Formatter(_FORMAT)

        stdout_path = os.path.join(_STATE_DIR, "markdown-vault.log")
        stdout_handler = _StdoutRotatingFileHandler(
            stdout_path,
            maxBytes=1_000_000,
            backupCount=3,
            encoding="utf-8",
            redirect_fd=redirect_stdout,
        )
        stdout_handler.setFormatter(fmt)
        stdout_handler.addFilter(_MaxLevelFilter(logging.INFO))

        stderr_path = os.path.join(_STATE_DIR, "markdown-vault.stderr.log")
        stderr_handler = _StderrRotatingFileHandler(
            stderr_path,
            maxBytes=1_000_000,
            backupCount=3,
            encoding="utf-8",
            redirect_fd=redirect_stderr,
        )
        stderr_handler.setFormatter(fmt)
        stderr_handler.addFilter(_MinLevelFilter(logging.WARNING))

        root = logging.getLogger()
        root.addHandler(stdout_handler)
        root.addHandler(stderr_handler)
        _installed_handlers.extend([stdout_handler, stderr_handler])
        logging.getLogger("markdown-vault").info(
            "Log files: %s / %s", stdout_path, stderr_path
        )
    except OSError as exc:
        logging.getLogger("markdown-vault").warning(
            "Could not set up file logging: %s", exc, exc_info=True
        )


def _setup_console_logging(redirect_stdout=True, redirect_stderr=True):
    """Set up console logging (stdout <= INFO, stderr >= WARNING).

    Console handlers are only added for fds that are not redirected to the
    log files; otherwise the output would be duplicated in the files.
    """
    global _console_setup_done
    if _console_setup_done:
        return
    _console_setup_done = True
    fmt = logging.Formatter(_FORMAT)
    handlers = []
    if not redirect_stdout:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(logging.DEBUG)
        handler.addFilter(_MaxLevelFilter(logging.INFO))
        handler.setFormatter(fmt)
        handlers.append(handler)
    if not redirect_stderr:
        handler = logging.StreamHandler(sys.stderr)
        handler.setLevel(logging.WARNING)
        handler.addFilter(_MinLevelFilter(logging.WARNING))
        handler.setFormatter(fmt)
        handlers.append(handler)
    if handlers:
        root = logging.getLogger()
        for handler in handlers:
            root.addHandler(handler)
        _installed_handlers.extend(handlers)


def _setup_glib_logging(level_str="warning"):
    """Register the GLib log handler and set the severity threshold.

    *level_str* is a real "this level and above" threshold — one of
    ``all``/``debug``/``info``/``message``/``warning``/``critical``/``error``.
    Messages below it are dropped (see :func:`_glib_severity_rank`).

    The handlers catch *every* level and are installed once; re-running this
    (e.g. from Preferences) only updates the threshold.
    """
    global _glib_min_rank, _glib_handler_id

    _glib_min_rank = _GLIB_THRESHOLD.get(
        str(level_str).lower(), _DEFAULT_GLIB_RANK
    )

    if _glib_handler_id:
        return  # already installed; threshold updated above

    all_levels = (
        GLib.LogLevelFlags.LEVEL_MASK
        | GLib.LogLevelFlags.FLAG_FATAL
        | GLib.LogLevelFlags.FLAG_RECURSION
    )
    for domain in ("Gtk", "Gdk", "JavascriptCore", "WebKit", "GtkSource", "Adwaita"):
        try:
            handler_id = GLib.log_set_handler(
                domain, all_levels, _glib_log_handler, None,
            )
            _glib_handler_id.append((domain, handler_id))
        except Exception:
            logging.getLogger("markdown-vault").debug(
                "Could not install GLib log handler for %s", domain, exc_info=True)
    # Default handler catches every other domain.
    try:
        default_handler_id = GLib.log_set_default_handler(
            _glib_log_handler, None,
        )
        _glib_handler_id.append((None, default_handler_id))
    except Exception:
        logging.getLogger("markdown-vault").debug(
            "Could not install the default GLib log handler", exc_info=True)


_llama_logger_ready = False


def get_llama_logger():
    """The ``markdown_vault.llama`` logger, writing to its own rotated file
    (``markdown-vault.llama.log``) and kept out of the main logs — llama.cpp's
    load output is verbose, so it gets a dedicated stream instead of drowning the
    app log. Set up once on first use."""
    global _llama_logger_ready
    logger = logging.getLogger("markdown_vault.llama")
    if not _llama_logger_ready:
        _llama_logger_ready = True
        logger.setLevel(logging.INFO)
        logger.propagate = False           # dedicated file only, not the app log
        try:
            os.makedirs(_STATE_DIR, exist_ok=True)
            handler = logging.handlers.RotatingFileHandler(
                _LLAMA_LOG_FILE, maxBytes=1_000_000, backupCount=3,
                encoding="utf-8")
            handler.setFormatter(logging.Formatter(_FORMAT))
            logger.addHandler(handler)
        except OSError as exc:
            logging.getLogger("markdown-vault").warning(
                "Could not set up the llama log file: %s", exc)
    return logger


def set_third_party_loglevel(level_str: str) -> None:
    """Set log level for all third-party loggers (markdown, pymdownx, …)."""
    level = _LOGLEVEL_MAP.get(str(level_str).lower(), logging.WARNING)
    for prefix in THIRD_PARTY_LOGGERS:
        logging.getLogger(prefix).setLevel(level)
        logging.getLogger(prefix.upper()).setLevel(level)


def _is_tty(stream) -> bool:
    """Return True if ``stream`` is attached to an interactive terminal."""
    try:
        return bool(stream.isatty())
    except Exception:
        # any stream without a working isatty() is treated as non-interactive
        return False


def init(settings, redirect_stdout=None, redirect_stderr=None, setup_glib=True):
    """Initialize logging.

    Sets up the two rotated log files, console logging and the GLib log
    handler.

    Args:
        settings: Application settings (dict or object with attributes).
        redirect_stdout: Redirect fd 1 to the stdout log file. ``None``
            (default) redirects only when stdout is not a terminal.
        redirect_stderr: Redirect fd 2 to the stderr log file. ``None``
            (default) redirects only when stderr is not a terminal.
        setup_glib: Install the GLib log handler.
    """
    # Local import so logging_setup stays import-light at module load (it is the
    # very first thing main.py runs, before config is otherwise imported).
    from markdown_vault.core import config

    settings = settings or {}

    if redirect_stdout is None:
        redirect_stdout = not _is_tty(sys.stdout)
    if redirect_stderr is None:
        redirect_stderr = not _is_tty(sys.stderr)

    if isinstance(settings, dict):
        log_level_str = str(
            config.get_setting(settings, "log.level", "warning")).lower() or "warning"
    else:
        log_level_str = str(getattr(settings, "loglevel", "warning")).lower() or "warning"
    log_level = _LOGLEVEL_MAP.get(log_level_str, logging.WARNING)

    root = logging.getLogger()
    root.setLevel(log_level)
    logging.getLogger("markdown-vault").setLevel(log_level)

    _setup_file_logging(
        redirect_stdout=redirect_stdout, redirect_stderr=redirect_stderr
    )
    _setup_console_logging(
        redirect_stdout=redirect_stdout, redirect_stderr=redirect_stderr
    )

    # Dumps go to fd 2, which the stderr handler points at the stderr log on
    # headless launches.
    faulthandler.enable(sys.stderr)

    if setup_glib:
        if isinstance(settings, dict):
            glib_level = str(
                config.get_setting(settings, "log.glib", "critical")).lower() or "critical"
        else:
            glib_level = str(getattr(settings, "glib_loglevel", "critical")).lower() or "critical"
        _setup_glib_logging(glib_level)


def update_glib_loglevel(level_str):
    """Update the GLib severity threshold from the Preferences dialog.

    Args:
        level_str: threshold — all/debug/info/message/warning/critical/error.
    """
    _setup_glib_logging(level_str)
