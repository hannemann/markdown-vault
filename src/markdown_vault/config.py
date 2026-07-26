"""Markdown Vault — configuration management.

Handles reading and writing of vault configuration stored in
``~/.config/markdown-vault/vaults.yaml``.  All paths are resolved to
absolute form on load and save to avoid duplicates that differ only
by relative path notation.
"""

import logging
import os
import tempfile
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

CONFIG_DIR = Path.home() / ".config" / "markdown-vault"
CONFIG_FILE = CONFIG_DIR / "vaults.yaml"

STATE_DIR = Path(
    os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state"))
) / "markdown-vault"
LOG_FILE = STATE_DIR / "markdown-vault.log"


def _ensure_config_dir() -> None:
    """Create the configuration directory if it does not exist."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def check_config_access() -> None:
    """Raise OSError if the config file/directory is not accessible.

    Called before opening Preferences to fail fast with a user-visible
    error dialog instead of silently falling back to defaults.
    """
    try:
        if not CONFIG_FILE.exists():
            # No config yet (first run) — directory must be writable.
            if not os.access(str(CONFIG_DIR), os.W_OK | os.X_OK):
                raise OSError(f"Cannot write to {CONFIG_DIR}")
            return
        # File exists — must be readable AND writable.
        with open(CONFIG_FILE, "r") as fh:
            fh.read()
        if not os.access(str(CONFIG_FILE), os.W_OK):
            raise OSError(f"Cannot write to {CONFIG_FILE}")
    except OSError:
        raise OSError(
            f"Configuration is not accessible.\n"
            f"Check permissions on {CONFIG_DIR}"
        )


def _atomic_write(path: Path, content: str) -> None:
    """Atomically write *content* to *path* via temp file + replace."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_vaults() -> list[dict[str, str]]:
    """Return the list of configured vaults.

    Each entry is ``{"name": str, "path": str}`` where *path* is always
    absolute.  Duplicate paths are silently discarded (first wins).
    """
    try:
        if not CONFIG_FILE.exists():
            logger.debug("No config file found, returning empty vault list")
            return []
        with open(CONFIG_FILE, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except (yaml.YAMLError, OSError) as exc:
        logger.warning("Failed to parse config file %s: %s", CONFIG_FILE, exc)
        return []
    vaults = data.get("vaults") or []
    seen: set[str] = set()
    unique: list[dict[str, str]] = []
    for entry in vaults:
        raw_path = entry.get("path", "")
        if not raw_path:
            continue
        abs_path = os.path.abspath(raw_path)
        if abs_path in seen:
            logger.debug("Skipping duplicate vault path: %s", abs_path)
            continue
        seen.add(abs_path)
        name = entry.get("name") or Path(abs_path).name
        unique.append({"name": name, "path": abs_path})
    logger.debug("Loaded %d vault(s) from config", len(unique))
    return unique


def save_vaults(vaults: list[dict[str, str]]) -> None:
    """Persist *vaults* to disk, deduplicating by absolute path."""
    _ensure_config_dir()
    seen: set[str] = set()
    unique: list[dict[str, str]] = []
    for entry in vaults:
        abs_path = os.path.abspath(entry["path"])
        if abs_path in seen:
            continue
        seen.add(abs_path)
        name = entry.get("name") or Path(abs_path).name
        unique.append({"name": name, "path": abs_path})
    existing: dict = {}
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as fh:
                existing = yaml.safe_load(fh) or {}
        except (yaml.YAMLError, OSError):
            existing = {}
    existing["vaults"] = unique
    yaml_str = yaml.dump(existing, default_flow_style=False, sort_keys=False)
    try:
        _atomic_write(CONFIG_FILE, yaml_str)
    except OSError:
        logger.error("Failed to save vaults to %s", CONFIG_FILE, exc_info=True)
        raise
    logger.debug("Saved %d vault(s) to config", len(unique))


def add_vault(name: str, path: str) -> list[dict[str, str]]:
    """Add a vault and return the updated list."""
    vaults = load_vaults()
    vaults.append({"name": name, "path": os.path.abspath(path)})
    save_vaults(vaults)
    logger.info("Vault added: %s (%s)", name, path)
    return load_vaults()


def remove_vault(path: str) -> list[dict[str, str]]:
    """Remove the vault at *path* and return the updated list."""
    abs_path = os.path.abspath(path)
    vaults = [v for v in load_vaults() if v["path"] != abs_path]
    save_vaults(vaults)
    logger.info("Vault removed: %s", path)
    return load_vaults()


# ── App settings ────────────────────────────────────────────────────

_DEFAULT_SETTINGS = {
    "autosave_interval": 30,
    "default_view_mode": "edit",
    "editor_font_size": 14,
    "editor_tab_width": 4,
    "editor_wrap_text": True,
    "preview_zoom": 1.0,
    "keybinding_next_tab": "<Control>Tab",
    "keybinding_prev_tab": "<Shift><Control>Tab",
    "tab_switch_mode": "mru",
    "tab_min_width": 150,
    "tab_wrap": False,
    "loglevel": "info",
    "third_party_loglevel": "warning",
}


def load_settings() -> dict:
    """Load app settings from vaults.yaml, with safe defaults."""
    try:
        if not CONFIG_FILE.exists():
            logger.debug("No config file, using default settings")
            return dict(_DEFAULT_SETTINGS)
        with open(CONFIG_FILE, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except (yaml.YAMLError, OSError) as exc:
        logger.warning("Failed to parse config for settings: %s", exc)
        return dict(_DEFAULT_SETTINGS)
    settings = dict(_DEFAULT_SETTINGS)
    settings.update(data.get("settings") or {})
    logger.debug("Loaded settings: %s", {k: v for k, v in settings.items()
                                          if k != "loglevel"})
    return settings


def save_settings(settings: dict) -> None:
    """Persist settings into vaults.yaml (merged with existing vaults)."""
    _ensure_config_dir()
    existing: dict = {}
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as fh:
                existing = yaml.safe_load(fh) or {}
        except (yaml.YAMLError, OSError):
            existing = {}
    existing["settings"] = settings
    yaml_str = yaml.dump(existing, default_flow_style=False, sort_keys=False)
    try:
        _atomic_write(CONFIG_FILE, yaml_str)
    except OSError:
        logger.error("Failed to save settings to %s", CONFIG_FILE, exc_info=True)
        raise
