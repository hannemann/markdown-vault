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

from . import validation

logger = logging.getLogger(__name__)

# In-memory cache for vaults loaded from vaults.yaml.
_vaults_cache: list[dict[str, str]] | None = None

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


def _read_vaults_from_disk() -> list[dict[str, str]]:
    """Read vaults from vaults.yaml on disk (no caching)."""
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
    used_names: set[str] = set()
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
        raw_name = entry.get("name") or Path(abs_path).name
        # Names key backlink/file-index lookups (vault:{name}?path=… /
        # {name}>stem). Sanitize forbidden characters out of a hand-edited
        # config (R19.4), fall back to the directory name, then uniquify
        # colliding names (R19.3, first occurrence wins).
        name = (validation.sanitize_vault_name(raw_name)
                or validation.sanitize_vault_name(Path(abs_path).name)
                or "vault")
        name = _uniquify_vault_name(name, used_names)
        used_names.add(name)
        vault: dict = {"name": name, "path": abs_path}
        icon = entry.get("icon")
        if icon:
            vault["icon"] = str(icon)
        if entry.get("mono"):
            vault["mono"] = True
        unique.append(vault)
    logger.debug("Loaded %d vault(s) from config", len(unique))
    return unique


def _uniquify_vault_name(name: str, used: set[str]) -> str:
    """Return *name*, or ``"name(n)"`` with the lowest free *n* if taken.

    The suffix is whitespace-free on purpose: vault names must contain no
    spaces (WIKILINK_RE's vault prefix forbids whitespace — R21.7).
    """
    if name not in used:
        return name
    n = 2
    while f"{name}({n})" in used:
        n += 1
    return f"{name}({n})"


def _invalidate_cache() -> None:
    """Invalidate the in-memory vaults cache."""
    global _vaults_cache
    _vaults_cache = None


def load_vaults() -> list[dict[str, str]]:
    """Return the list of configured vaults (cached).

    Each entry is ``{"name": str, "path": str}`` where *path* is always
    absolute.  Duplicate paths are silently discarded (first wins).

    The result is cached in memory and only re-read from disk when
    :func:`_invalidate_cache` has been called (triggered by any write
    operation: ``save_vaults``, ``add_vault``, ``remove_vault``,
    ``save_settings``).  A fresh list is returned on every call so
    callers cannot alias or mutate the cached object.
    """
    global _vaults_cache
    if _vaults_cache is None:
        _vaults_cache = _read_vaults_from_disk()
    return list(_vaults_cache)


def save_vaults(vaults: list[dict[str, str]]) -> None:
    """Persist *vaults* to disk, deduplicating by absolute path."""
    _invalidate_cache()
    _ensure_config_dir()
    seen: set[str] = set()
    unique: list[dict[str, str]] = []
    for entry in vaults:
        abs_path = os.path.abspath(entry["path"])
        if abs_path in seen:
            continue
        seen.add(abs_path)
        name = entry.get("name") or Path(abs_path).name
        vault = {"name": name, "path": abs_path}
        icon = entry.get("icon")
        if icon:
            vault["icon"] = str(icon)
        if entry.get("mono"):
            vault["mono"] = True
        unique.append(vault)
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


def rename_vault(path: str, new_name: str) -> list[dict[str, str]]:
    """Rename the vault at *path* to *new_name* and return the updated list."""
    abs_path = os.path.abspath(path)
    vaults = load_vaults()
    for vault in vaults:
        if vault["path"] == abs_path:
            vault["name"] = new_name
            save_vaults(vaults)
            logger.info("Vault renamed: %s → %s", path, new_name)
            return load_vaults()
    logger.warning("Vault not found for rename: %s", path)
    return vaults


def set_vault_icon(path: str, icon: str | None, mono: bool = False) -> list[dict]:
    """Set the display icon (and monochrome flag) for the vault at *path*.

    Pass ``icon=None`` to clear the icon (fall back to the default) and
    ``mono=False`` to render it in colour.
    """
    abs_path = os.path.abspath(path)
    vaults = load_vaults()
    for vault in vaults:
        if vault["path"] == abs_path:
            if icon:
                vault["icon"] = icon
            else:
                vault.pop("icon", None)
            if mono:
                vault["mono"] = True
            else:
                vault.pop("mono", None)
            save_vaults(vaults)
            logger.info("Vault icon set: %s → %s (mono=%s)", path, icon, mono)
            return load_vaults()
    logger.warning("Vault not found for icon change: %s", path)
    return vaults


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
    "glib_loglevel": "critical",
    "webkit_disable_dmabuf": False,
    "webkit_disable_compositing": False,
    "wikilink_autofix_normalize": False,
    "wikilink_autofix_relink": False,
    "wikilink_warn_on_save": False,
    "wikilink_mark_broken": False,
    "preview_allow_remote_images": False,
    # Semantic (vector) search — opt-in, Ollama backend.
    "semantic_search_enabled": False,
    "semantic_backend": "onnx",  # "onnx" (local, recommended) or "ollama" (server)
    "semantic_ollama_url": "http://localhost:11434",
    "semantic_ollama_model": "nomic-embed-text",
    "semantic_onnx_model": "",       # path to model.onnx (empty → data dir default)
    "semantic_onnx_tokenizer": "",   # path to tokenizer.json (empty → default)
    "semantic_onnx_model_url":
        "https://huggingface.co/Xenova/paraphrase-multilingual-MiniLM-L12-v2/"
        "resolve/main/onnx/model.onnx",
    "semantic_onnx_tokenizer_url":
        "https://huggingface.co/Xenova/paraphrase-multilingual-MiniLM-L12-v2/"
        "resolve/main/tokenizer.json",
    "semantic_min_score": 0.35,
}

# Setting key → environment variable consumed by WebKitGTK at startup.
_WEBKIT_ENV_KEYS = {
    "webkit_disable_dmabuf": "WEBKIT_DISABLE_DMABUF_RENDERER",
    "webkit_disable_compositing": "WEBKIT_DISABLE_COMPOSITING_MODE",
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
    _invalidate_cache()
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


def apply_webkit_env(settings: dict | None = None) -> None:
    """Apply WebKit environment variables from *settings*.

    Must run before any WebKit module is imported (i.e. before the app
    window is created), otherwise the renderer/compositor is already
    initialised with the defaults.  Environment variables are only set,
    never unset — the app starts in a fresh process each time.
    """
    if settings is None:
        settings = load_settings()
    for setting_key, env_key in _WEBKIT_ENV_KEYS.items():
        if settings.get(setting_key):
            os.environ[env_key] = "1"
