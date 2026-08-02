"""Central logging setup for Markdown Vault.

Sets up:
- Python file logging (RotatingFileHandler, 1 MB, 3 backups)
- GLib log handler (Gtk/Gdk/JavascriptCore/WebKit/GtkSource/Adwaita)
"""

import logging
import logging.handlers
import os
import sys
import threading
import traceback

from gi.repository import GLib

_STATE_DIR = os.path.join(os.path.expanduser("~"), ".local/state/markdown-vault")
_LOG_FILE = os.path.join(_STATE_DIR, "markdown-vault.log")


# Global state for GLib logging.
_glib_log_level = GLib.LogLevelFlags.LEVEL_ERROR
_glib_handler_id = []  # Handler IDs for each domain


def _glib_log_handler(log_domain, log_level, message, user_data=None):
    """GLib log handler — routes through Python logger.
    
    This ensures GLib/JavascriptCore/Gtk messages end up in the
    log file via RotatingFileHandler.
    """
    import logging as _logging
    
    timestamp = GLib.DateTime.new_now_local().format("%Y-%m-%d %H:%M:%S")
    msg = f"[{timestamp}] [{log_domain}] {log_level}: {message}"
    
    # Map GLib level to Python logging level.
    if log_level & GLib.LogLevelFlags.LEVEL_CRITICAL:
        log_level_py = _logging.CRITICAL
    elif log_level & GLib.LogLevelFlags.LEVEL_WARNING:
        log_level_py = _logging.WARNING
    elif log_level & GLib.LogLevelFlags.LEVEL_ERROR:
        log_level_py = _logging.ERROR
    elif log_level & GLib.LogLevelFlags.LEVEL_MESSAGE:
        log_level_py = _logging.INFO
    elif log_level & GLib.LogLevelFlags.LEVEL_INFO:
        log_level_py = _logging.INFO
    else:
        log_level_py = _logging.DEBUG
    
    # Route through Python logger (already writes to log file via RotatingFileHandler).
    logger = _logging.getLogger("markdown-vault")
    logger.log(log_level_py, msg)
    
    # Append stack trace for critical/warning messages.
    if log_level & (GLib.LogLevelFlags.LEVEL_CRITICAL | GLib.LogLevelFlags.LEVEL_WARNING):
        # Try C backtrace via libc.
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
            
            libc.backtrace_symbols.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_int]
            libc.backtrace_symbols.restype = ctypes.POINTER(ctypes.c_char_p)
            symbols = libc.backtrace_symbols(backtrace, n_frames)
            
            trace_lines = []
            for i in range(n_frames):
                symbol = symbols[i]
                if symbol:
                    trace_lines.append(f"  {i}: {symbol.decode('utf-8', errors='replace')}")
            
            if trace_lines:
                logger.log(log_level_py, "C backtrace:\n%s", "\n".join(trace_lines))
            else:
                raise ValueError("No symbols")
        except Exception:
            logger.log(log_level_py, "C backtrace failed (no traceback)", exc_info=False)
        
        # Always append Python thread stacks for best analysis.
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


def _setup_file_logging():
    """Set up Python file logging with RotatingFileHandler."""
    root = logging.getLogger()
    has_file_handler = any(
        isinstance(h, logging.handlers.RotatingFileHandler)
        for h in root.handlers
    )
    if not has_file_handler:
        try:
            os.makedirs(_STATE_DIR, exist_ok=True)
            file_handler = logging.handlers.RotatingFileHandler(
                _LOG_FILE,
                maxBytes=1_000_000,
                backupCount=3,
                encoding="utf-8",
            )
            file_handler.setFormatter(
                logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
            )
            root.addHandler(file_handler)
            logging.getLogger("markdown-vault").info("Log file: %s", _LOG_FILE)
        except OSError as exc:
            logging.getLogger("markdown-vault").warning(
                "Could not set up file logging: %s", exc
            )


def _setup_glib_logging(level_str="critical"):
    """Register the GLib log handler.
    
    Args:
        level_str: Log level string (error, warning, critical, all).
    """
    global _glib_log_level, _glib_handler_id
    
    # Map string level to GLib LogLevelFlags.
    level_map = {
        "error": GLib.LogLevelFlags.LEVEL_ERROR,
        "warning": GLib.LogLevelFlags.LEVEL_WARNING,
        "critical": GLib.LogLevelFlags.LEVEL_CRITICAL,
        "all": GLib.LogLevelFlags.LEVEL_MASK,
    }
    new_level = level_map.get(level_str, GLib.LogLevelFlags.LEVEL_ERROR)
    
    # Only (re)register if the level actually changed.
    if new_level == _glib_log_level and _glib_handler_id:
        return
    
    # Remove old handlers.
    if _glib_handler_id:
        for hid in _glib_handler_id:
            try:
                GLib.log_remove_default_handler(hid)
            except Exception:
                pass
            try:
                for domain in ("Gtk", "Gdk", "JavascriptCore", "WebKit", "GtkSource", "Adwaita"):
                    GLib.log_remove_handler(domain, hid)
            except Exception:
                pass
        _glib_handler_id = []
    
    # Register new handler for the selected level (and above).
    _glib_log_level = new_level
    for domain in ("Gtk", "Gdk", "JavascriptCore", "WebKit", "GtkSource", "Adwaita"):
        try:
            handler_id = GLib.log_set_handler(
                domain,
                _glib_log_level,
                _glib_log_handler,
                None,
            )
            _glib_handler_id.append(handler_id)
        except Exception:
            pass
    # Also set default handler to catch all other domains.
    try:
        default_handler_id = GLib.log_set_default_handler(
            _glib_log_handler,
            None,
        )
        _glib_handler_id.append(default_handler_id)
    except Exception:
        pass


def init(settings):
    """Initialize logging.
    
    Sets up Python file logging and GLib log handler.
    
    Args:
        settings: Application settings object.
    """
    try:
        log_level = getattr(settings, "loglevel", "warning")
        log_level = log_level.lower() if log_level else "warning"
        
        # Set Python logging level for markdown-vault logger.
        logger = logging.getLogger("markdown-vault")
        logger.setLevel(getattr(logging, log_level.upper(), logging.WARNING))
        
        _setup_file_logging()
        
        # GLib log level from settings (default: critical).
        glib_level = getattr(settings, "glib_loglevel", "critical")
        glib_level = glib_level.lower() if glib_level else "critical"
        _setup_glib_logging(glib_level)
        
    except Exception:
        pass  # Logging initialization is best-effort


def update_glib_loglevel(level_str):
    """Update GLib log level from Preferences dialog.
    
    Args:
        level_str: Log level string (error, warning, critical, all).
    """
    _setup_glib_logging(level_str)
