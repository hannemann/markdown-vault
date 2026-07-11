"""Markdown Vault — configuration management.

Handles reading and writing of vault configuration stored in
``~/.config/markdown-vault/vaults.yaml``.  All paths are resolved to
absolute form on load and save to avoid duplicates that differ only
by relative path notation.
"""

import os
from pathlib import Path

import yaml

CONFIG_DIR = Path.home() / ".config" / "markdown-vault"
CONFIG_FILE = CONFIG_DIR / "vaults.yaml"


def _ensure_config_dir() -> None:
    """Create the configuration directory if it does not exist."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def load_vaults() -> list[dict[str, str]]:
    """Return the list of configured vaults.

    Each entry is ``{"name": str, "path": str}`` where *path* is always
    absolute.  Duplicate paths are silently discarded (first wins).
    """
    if not CONFIG_FILE.exists():
        return []
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except (yaml.YAMLError, OSError):
        return []
    vaults = data.get("vaults", [])
    seen: set[str] = set()
    unique: list[dict[str, str]] = []
    for entry in vaults:
        raw_path = entry.get("path", "")
        if not raw_path:
            continue
        abs_path = os.path.abspath(raw_path)
        if abs_path in seen:
            continue
        seen.add(abs_path)
        name = entry.get("name") or Path(abs_path).name
        unique.append({"name": name, "path": abs_path})
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
    data = {"vaults": unique}
    with open(CONFIG_FILE, "w", encoding="utf-8") as fh:
        yaml.dump(data, fh, default_flow_style=False, sort_keys=False)


def add_vault(name: str, path: str) -> list[dict[str, str]]:
    """Add a vault and return the updated list."""
    vaults = load_vaults()
    vaults.append({"name": name, "path": os.path.abspath(path)})
    save_vaults(vaults)
    return load_vaults()


def remove_vault(path: str) -> list[dict[str, str]]:
    """Remove the vault at *path* and return the updated list."""
    abs_path = os.path.abspath(path)
    vaults = [v for v in load_vaults() if v["path"] != abs_path]
    save_vaults(vaults)
    return load_vaults()


# ── App settings ────────────────────────────────────────────────────

_DEFAULT_SETTINGS = {
    "autosave_interval": 30,
    "default_view_mode": "edit",
    "editor_font_size": 14,
    "editor_tab_width": 4,
    "editor_wrap_text": True,
    "preview_zoom": 1.0,
}


def load_settings() -> dict:
    """Load app settings from vaults.yaml, with safe defaults."""
    if not CONFIG_FILE.exists():
        return dict(_DEFAULT_SETTINGS)
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except (yaml.YAMLError, OSError):
        return dict(_DEFAULT_SETTINGS)
    settings = dict(_DEFAULT_SETTINGS)
    settings.update(data.get("settings", {}))
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
    with open(CONFIG_FILE, "w", encoding="utf-8") as fh:
        yaml.dump(existing, fh, default_flow_style=False, sort_keys=False)
