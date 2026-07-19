"""Markdown Vault — session state persistence.

Saves and restores the full application state (window geometry, open tabs,
view modes, split positions, sidebar visibility) to a JSON file so that
restoring the app recreates the exact previous session.

Session file: ``~/.config/markdown-vault/session.json``

Per-vault sessions store tabs, active tab, and MRU state separately so
that switching vaults can save and restore tab groups.
"""

import json
import logging
from pathlib import Path

from . import config

logger = logging.getLogger(__name__)

SESSION_FILE = config.CONFIG_DIR / "session.json"


def save_session(
    width: int,
    height: int,
    sidebar_visible: bool,
    active_vault: str | None,
    vault_sessions: dict[str, dict],
    expanded_vaults: list[str] | None = None,
    search_visible: bool = False,
    search_paned_position: int = 0,
    sidebar_paned_position: int = 0,
    main_paned_position: int = 0,
) -> None:
    """Write the current session state to disk.

    *active_vault* is the vault root path currently shown.
    *vault_sessions* maps vault paths to per-vault state dicts with keys:
        ``tabs``, ``active_tab``, ``mru``.
    *expanded_vaults* lists the vault directory paths that were expanded.
    *search_visible* whether the search bar is open.
    *search_paned_position* height of the search results area.
    *sidebar_paned_position* width of the sidebar.
    *main_paned_position* width of the vault tree panel.
    """
    config._ensure_config_dir()
    data = {
        "window": {"width": width, "height": height},
        "sidebar_visible": sidebar_visible,
        "active_vault": active_vault,
        "expanded_vaults": expanded_vaults or [],
        "vault_sessions": vault_sessions,
        "search_visible": search_visible,
        "search_paned_position": search_paned_position,
        "sidebar_paned_position": sidebar_paned_position,
        "main_paned_position": main_paned_position,
    }
    try:
        SESSION_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        logger.debug("Session saved to %s", SESSION_FILE)
    except OSError as exc:
        logger.warning("Failed to save session: %s", exc)


def load_session() -> dict:
    """Read the persisted session state, or return sensible defaults."""
    if not SESSION_FILE.exists():
        return _defaults()
    try:
        data = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Corrupt session file, using defaults: %s", exc)
        return _defaults()
    data.setdefault("window", {"width": 1200, "height": 800})
    data.setdefault("sidebar_visible", False)
    data.setdefault("active_vault", None)
    data.setdefault("expanded_vaults", [])
    data.setdefault("vault_sessions", {})
    data.setdefault("search_visible", False)
    data.setdefault("search_paned_position", 0)
    data.setdefault("sidebar_paned_position", 0)
    data.setdefault("main_paned_position", 0)
    # Migration: old sessions had top-level "tabs" + "active_tab".
    _migrate_legacy_session(data)
    return data


def prune_vault_session(vault_session: dict) -> dict:
    """Remove tabs whose files no longer exist on disk.

    Returns a new dict with only existing files.  *active_tab* is cleared
    if the referenced file is missing.
    """
    tabs = [
        t for t in vault_session.get("tabs", [])
        if t.get("path") and Path(t["path"]).exists()
    ]
    active_tab = vault_session.get("active_tab")
    if active_tab and Path(active_tab).exists():
        pass  # keep it
    else:
        active_tab = None
    # Preserve MRU entries that still point to existing files.
    mru = [fp for fp in vault_session.get("mru", []) if fp and Path(fp).exists()]
    return {"tabs": tabs, "active_tab": active_tab, "mru": mru}


def _migrate_legacy_session(data: dict) -> None:
    """Migrate old top-level tabs into vault_sessions (one-time)."""
    legacy_tabs = data.get("tabs")
    if not legacy_tabs:
        return
    # Determine the vault from the first tab's path.
    vaults = config.load_vaults()
    vault_paths = [v["path"] for v in vaults]
    legacy_active = data.get("active_tab")
    vault = None
    if legacy_active:
        parent = str(Path(legacy_active).parent)
        for vp in vault_paths:
            if parent == vp or parent.startswith(vp + "/"):
                vault = vp
                break
    if not vault and legacy_tabs:
        fp = legacy_tabs[0].get("path", "")
        if fp:
            parent = str(Path(fp).parent)
            for vp in vault_paths:
                if parent == vp or parent.startswith(vp + "/"):
                    vault = vp
                    break
    if not vault and vault_paths:
        vault = vault_paths[0]
    if vault:
        # Build MRU list from tab order (last = most recent).
        mru = [t["path"] for t in reversed(legacy_tabs) if "path" in t]
        data["vault_sessions"][vault] = {
            "tabs": legacy_tabs,
            "active_tab": legacy_active,
            "mru": mru,
        }
        data["active_vault"] = vault
    # Remove legacy keys.
    data.pop("tabs", None)
    data.pop("active_tab", None)


def _defaults() -> dict:
    return {
        "window": {"width": 1200, "height": 800},
        "sidebar_visible": False,
        "active_vault": None,
        "expanded_vaults": [],
        "vault_sessions": {},
        "search_visible": False,
        "search_paned_position": 0,
        "sidebar_paned_position": 0,
        "main_paned_position": 0,
    }
