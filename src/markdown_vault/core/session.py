"""Markdown Vault — session state persistence.

Saves and restores the full application state (window geometry, open tabs,
view modes, split positions, sidebar visibility) to a JSON file so that
restoring the app recreates the exact previous session.

Session file: ``session.json`` in the XDG state dir (``core.paths.STATE_DIR``) —
view/layout state, not configuration.

Per-vault sessions store tabs, active tab, and MRU state separately so
that switching vaults can save and restore tab groups.
"""

import json
import logging
import os
from pathlib import Path

from markdown_vault.core import config

logger = logging.getLogger(__name__)

SESSION_FILE = config.STATE_DIR / "session.json"


def _is_canonical_note_path(p) -> bool:
    """A well-formed persisted note path: a non-empty absolute string that is
    canonical (``os.path.normpath`` is a no-op — no ``.``/``..``/duplicate
    separators), ends in ``.md``, and is not a directory. This rejects exactly
    the entries that crash session restore: a ``..`` path breaks ``relative_to``
    and a directory breaks ``with_suffix`` deep in the backlink index. Existence
    is deliberately *not* required, so a note on a momentarily-missing vault keeps
    its tab (a missing file only fails to open, harmlessly) rather than being
    dropped from the session."""
    return (isinstance(p, str) and bool(p)
            and os.path.isabs(p)
            and os.path.normpath(p) == p
            and p.endswith(".md")
            and not os.path.isdir(p))


def _nav_entry_path(h):
    """The note path of a nav-history entry, tolerating both persisted forms:
    a plain string (legacy) or a ``{"path": …, …}`` dict (position-carrying).
    Returns ``None`` for anything without a usable path, so the canonical check
    below rejects it."""
    if isinstance(h, str):
        return h
    if isinstance(h, dict):
        return h.get("path")
    return None


def _sanitize(data: dict) -> dict:
    """Drop malformed persisted state at load so a corrupt or stale session can't
    crash the restore (or resurrect broken tabs). Invalid tabs / MRU / history
    entries are removed individually; everything valid is kept. Mutates *data*."""
    dropped = 0
    vaults = data.get("vault_sessions")
    if not isinstance(vaults, dict):
        data["vault_sessions"] = {}
        vaults = {}
    for vault in list(vaults):
        sess = vaults[vault]
        if not isinstance(sess, dict):
            del vaults[vault]
            dropped += 1
            continue
        tabs = sess.get("tabs") if isinstance(sess.get("tabs"), list) else []
        good = [t for t in tabs
                if isinstance(t, dict) and _is_canonical_note_path(t.get("path"))]
        dropped += len(tabs) - len(good)
        sess["tabs"] = good
        valid = {t["path"] for t in good}
        if isinstance(sess.get("mru"), list):
            sess["mru"] = [p for p in sess["mru"] if p in valid]
        if sess.get("active_tab") not in valid:
            sess["active_tab"] = good[-1]["path"] if good else None
    nav = data.get("nav_history")
    if isinstance(nav, dict) and isinstance(nav.get("history"), list):
        hist = [h for h in nav["history"]
                if _is_canonical_note_path(_nav_entry_path(h))]
        dropped += len(nav["history"]) - len(hist)
        nav["history"] = hist
        pos = nav.get("pos", -1)
        nav["pos"] = pos if isinstance(pos, int) and -1 <= pos < len(hist) else len(hist) - 1
    if dropped:
        logger.warning("Session: dropped %d invalid entr%s on load",
                       dropped, "y" if dropped == 1 else "ies")
    return data


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
    nav_history: dict | None = None,
    ask_last_question: str = "",
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
    config._ensure_state_dir()
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
        "nav_history": nav_history or {"history": [], "pos": -1},
        "ask_last_question": ask_last_question,
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
    data.setdefault("nav_history", {"history": [], "pos": -1})
    data.setdefault("search_visible", False)
    data.setdefault("search_paned_position", 0)
    data.setdefault("sidebar_paned_position", 0)
    data.setdefault("main_paned_position", 0)
    data.setdefault("ask_last_question", "")
    # Migration: old sessions had top-level "tabs" + "active_tab".
    _migrate_legacy_session(data)
    # Drop malformed/crash-causing entries before any consumer touches them.
    _sanitize(data)
    return data


def prune_vault_session(vault_session: dict) -> dict:
    """Restore-time pruning: keep only tabs that are well-formed note paths *and*
    still exist on disk. Uses :func:`_is_canonical_note_path` (so a directory or a
    ``.``/``..`` path is rejected, not just a missing file — plain ``exists()``
    passes for a directory), then requires the file to be present. *active_tab* is
    cleared and *mru* filtered to survivors.
    """
    def usable(p):
        return _is_canonical_note_path(p) and os.path.isfile(p)

    tabs = [t for t in vault_session.get("tabs", [])
            if isinstance(t, dict) and usable(t.get("path"))]
    active_tab = vault_session.get("active_tab")
    if not usable(active_tab):
        active_tab = None
    mru = [fp for fp in vault_session.get("mru", []) if usable(fp)]
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
        "ask_last_question": "",
    }
