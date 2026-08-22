"""The app's base directories, resolved per the XDG Base Directory Specification.

**One definition per path, for the whole app.** This module exists because the two
places that need these paths cannot import each other's home: ``logging_setup`` runs
first in ``main.py`` and deliberately pulls in nothing but stdlib + GLib (logging must
work even when the config is unreadable), while ``config`` is imported by many pure
unit tests that must not require GTK. A stdlib-only third module serves both.

Which data kind goes where follows the spec rather than history:

===================== ==================== ==========================================
data                  directory            why
===================== ==================== ==========================================
logs, debug dumps     ``STATE_DIR``        state: persists, not portable, not config
``session.json``      ``STATE_DIR``        view/layout state (geometry, tabs, zoom)
semantic index        ``CACHE_DIR``        regenerates from the notes
ONNX + GGUF models    ``DATA_DIR``         deliberately downloaded, expensive; a cache
                                           cleaner must not silently eat a GB
``vaults.yaml``       ``CONFIG_DIR``       user configuration
===================== ==================== ==========================================

``MDV_CONFIG_DIR`` overrides the config directory *verbatim* (isolated runs and the E2E
harness point it at a throwaway dir) and takes precedence over ``XDG_CONFIG_HOME``.

These are the *application's* dirs. WebKit keeps its own caches elsewhere: the launcher
execs the interpreter, so ``g_get_prgname()`` is ``python3`` and WebKit writes under
``~/.local/share/python3/``, not the app ID. Under Flatpak everything (the app's dirs and
WebKit's) lands in ``~/.var/app/<app-id>/…``, so a sandboxed build must be debugged there,
not through the host's dirs.
"""
import os
from pathlib import Path

#: The application ID — the same string as ``main.py``'s ``application_id``, the
#: ``.desktop`` file and the installed data directory. One name for the app
#: everywhere; reverse-DNS keeps it unique among all programs sharing these bases.
_APP = "de.hannemann.markdown-vault"


def resolve(env_var: str, default: str) -> Path:
    """Return ``<$env_var or ~/default>/de.hannemann.markdown-vault``.

    Per the XDG spec a relative path in one of these variables is invalid and must be
    ignored, so an unset, empty or relative value falls back to *default*.
    """
    base = os.environ.get(env_var, "")
    if not base or not os.path.isabs(base):
        base = str(Path.home() / default)
    return Path(base) / _APP


def config_dir() -> Path:
    """The config directory: ``MDV_CONFIG_DIR`` verbatim, else the XDG location."""
    override = os.environ.get("MDV_CONFIG_DIR", "")
    if override:
        return Path(override)
    return resolve("XDG_CONFIG_HOME", ".config")


#: Logs, debug dumps, ``session.json``.
STATE_DIR = resolve("XDG_STATE_HOME", ".local/state")
#: ``vaults.yaml`` (honours ``MDV_CONFIG_DIR``).
CONFIG_DIR = config_dir()
#: Regenerable data — the semantic index.
CACHE_DIR = resolve("XDG_CACHE_HOME", ".cache")
#: Downloaded models (ONNX, GGUF).
DATA_DIR = resolve("XDG_DATA_HOME", ".local/share")
