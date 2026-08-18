"""Markdown Vault — configuration management.

Handles reading and writing of vault configuration stored in
``~/.config/de.hannemann.markdown-vault/vaults.yaml``.  All paths are resolved to
absolute form on load and save to avoid duplicates that differ only
by relative path notation.
"""

import logging
import os
import tempfile
import traceback
from pathlib import Path

import yaml

from markdown_vault.core import paths, validation

logger = logging.getLogger(__name__)

# In-memory cache for vaults loaded from vaults.yaml.
_vaults_cache: list[dict[str, str]] | None = None

# The base directories live in core.paths (one definition for the whole app, shared
# with logging_setup); re-exported here so existing call sites keep working.
CONFIG_DIR = paths.CONFIG_DIR      # honours MDV_CONFIG_DIR, else XDG_CONFIG_HOME
CONFIG_FILE = CONFIG_DIR / "vaults.yaml"

STATE_DIR = paths.STATE_DIR        # logs, debug dumps, session.json
CACHE_DIR = paths.CACHE_DIR        # regenerable: the semantic index
DATA_DIR = paths.DATA_DIR          # downloaded models (ONNX, GGUF)
LOG_FILE = STATE_DIR / "markdown-vault.log"


def _ensure_config_dir() -> None:
    """Create the configuration directory if it does not exist."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def _ensure_state_dir() -> None:
    """Create the state directory if it does not exist (session.json, dumps)."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)


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
    # OKF lifecycle: hide deprecated notes from the vault tree AND the search
    # surfaces (with a visible "N hidden" notice). One shared, persisted toggle.
    "hide_deprecated": False,
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
    # Folder holding the ONNX files (model.onnx + tokenizer.json); it is both the
    # download target and the load source. Empty → the app data dir default.
    "semantic_onnx_dir": "",
    "semantic_onnx_model_url":
        "https://huggingface.co/Xenova/paraphrase-multilingual-MiniLM-L12-v2/"
        "resolve/main/onnx/model.onnx",
    "semantic_onnx_tokenizer_url":
        "https://huggingface.co/Xenova/paraphrase-multilingual-MiniLM-L12-v2/"
        "resolve/main/tokenizer.json",
    "semantic_min_score": 0.35,
    # Answer engine — the top-level Ask control.
    #   "auto"   — the app configures everything: in-process backend, GPU offload
    #              when the build supports it, a safe thread count (recommended)
    #   "manual" — honour the advanced ask_backend / thread / GPU settings below
    #   "off"    — no answers are generated
    "ask_engine": "auto",
    # Ask/answer (RAG): the manual-mode chat backend.
    #   "local"  — in-process GGUF via llama-cpp-python (no server)
    #   "ollama" — a running Ollama server (/api/chat)
    #   "openai" — an OpenAI-compatible server, e.g. llama.cpp (/v1)
    "ask_backend": "local",
    "ask_ollama_url": "http://localhost:11434",
    "ask_model": "llama3.2",
    # The model choice belongs to the server, not to the app: "<backend>|<url>" →
    # model name, so switching provider restores that provider's model instead of
    # sending the previous one to a server that does not have it.
    "ask_model_by_endpoint": {},
    # Same for the server URL: one per backend, so switching does not point the
    # new backend at the previous one's host. ask_ollama_url is the active value.
    "ask_url_by_backend": {},
    # Local (in-process) GGUF model. The .gguf file is both the download target
    # and the load source; empty → the app state dir default. The URL pre-fills
    # the download button with a small llama3.2-3B instruct build.
    "ask_gguf_path": "",
    "ask_gguf_url":
        "https://huggingface.co/bartowski/Llama-3.2-3B-Instruct-GGUF/"
        "resolve/main/Llama-3.2-3B-Instruct-Q4_K_M.gguf",
    # llama.cpp runtime knobs. 0 GPU layers = pure CPU (safe default, works on a
    # laptop); raise to offload layers to a GPU. 0 threads = half the physical
    # cores (resolved at runtime).
    "ask_n_gpu_layers": 0,
    "ask_n_threads": 0,
    # Prompt batch sizes. n_batch is the logical batch (max tokens per decode);
    # n_ubatch the physical micro-batch actually processed at once — the real
    # prefill-speed lever on the GPU. 0 = llama.cpp default (2048 / 512). Larger
    # n_ubatch speeds up the prompt-reading phase; keep n_ubatch <= n_batch.
    "ask_n_batch": 0,
    "ask_n_ubatch": 0,
    # KV-cache precision, chosen separately for the K and V caches: "f16"
    # (default) or quantized "q8_0"/"q4_0". Quantizing K is free; quantizing V
    # (type_v below f16) requires flash attention.
    "ask_kv_type_k": "f16",
    "ask_kv_type_v": "f16",
    "ask_flash_attn": False,
    # Memory-map the model file (default). Off loads it fully into RAM — slower
    # first load, but no page-faults during the answer; needs enough free RAM.
    "ask_use_mmap": True,
    # Hard cap on generated tokens, so a model that degenerates into a repetition
    # loop still stops (it would otherwise run until the context is full).
    "ask_max_tokens": 1024,
    "ask_system_prompt": "",  # empty → the built-in default (ask.DEFAULT_SYSTEM_PROMPT)
    # Reasoning models (Qwen3, …) think before answering: accurate but slow. For
    # grounded note Q&A, disabling it is faster and better calibrated. Only sent
    # to the backend when False, so non-reasoning models are unaffected.
    "ask_reasoning": True,
    # Context window (tokens) requested from Ollama. Its own default (2048)
    # truncates multi-note contexts; note-level retrieval needs more. Larger =
    # fits more/longer notes, but costs memory. Only used by the Ollama backend
    # (llama.cpp sizes its context server-side).
    "ask_num_ctx": 8192,
    # How many notes are retrieved as context for an answer. On CPU the model
    # spends almost all its time *reading* this context, so fewer notes = much
    # faster (roughly linear); 10 suits a GPU, ~5 a slow CPU.
    "ask_top_k": 10,
    # Hybrid retrieval: fuse a BM25 (keyword) ranking into the semantic one so
    # exact tokens (names, config keys, shortcuts) that embeddings blur still
    # surface, and relevant notes rank higher. On by default (measured +10/100
    # on a gold set, never worse); the Preferences switch can disable it if the
    # extra BM25 index is unwanted on a very large vault.
    "ask_hybrid": True,
    # Document import — Whisper model size for audio transcription (tiny · base ·
    # small · medium · large-v3; bigger = more accurate, slower, larger download).
    # Downloaded explicitly in Preferences, never during an import.
    "document_whisper_model": "base",
    # Folder the document-import file chooser reopens in — the directory of the last
    # picked file, so importing several files in a row doesn't drop back to $HOME.
    "document_import_last_dir": "",
}


def default(key):
    """The built-in default value for a setting *key* (``""`` if unknown).

    Use ``settings.get(key) or config.default(key)`` at read sites so a field
    the user has cleared to an empty string falls back to the default instead
    of an empty value.
    """
    return _DEFAULT_SETTINGS.get(key, "")


def models_dir() -> Path:
    """Folder holding the downloaded GGUF models (one file per model)."""
    return DATA_DIR / "models"


def default_gguf_path() -> str:
    """Legacy single-file location, kept as a fallback target."""
    return str(models_dir() / "model.gguf")


def is_gguf(path) -> bool:
    """Whether *path* is really a GGUF model — it starts with the ``GGUF`` magic.
    Guards against a saved HTML page (e.g. a HuggingFace *blob* URL fetched by
    mistake) being offered as a model."""
    try:
        with open(path, "rb") as fh:
            return fh.read(4) == b"GGUF"
    except OSError:
        return False


def list_models() -> list:
    """The **valid** GGUF files in :func:`models_dir`, newest first — files that
    only carry a ``.gguf`` name but aren't GGUF are skipped."""
    d = models_dir()
    if not d.exists():
        return []
    files = [p for p in d.glob("*.gguf") if p.is_file() and is_gguf(p)]
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def resolve_model_path(settings: dict) -> str:
    """The GGUF the local backend should load: the explicitly selected
    ``ask_gguf_path`` if it still exists and is a real GGUF, else the most-recent
    valid model in the folder, else ``""`` (nothing usable yet)."""
    explicit = settings.get("ask_gguf_path")
    if explicit and os.path.exists(explicit) and is_gguf(explicit):
        return explicit
    models = list_models()
    if models:
        return str(models[0])
    return explicit or ""


def model_filename_from_url(url: str) -> str:
    """A GGUF filename derived from a download *url*, so several models keep
    distinct names in the folder instead of overwriting one ``model.gguf``."""
    from urllib.parse import urlparse
    name = os.path.basename(urlparse(url).path)
    return name if name.endswith(".gguf") else "model.gguf"


def normalize_gguf_url(url: str) -> str:
    """Fix the common HuggingFace mistake of pasting the file's *page* URL: a
    ``…/blob/…`` link serves an HTML page, ``…/resolve/…`` serves the raw file."""
    if "huggingface.co" in url and "/blob/" in url:
        return url.replace("/blob/", "/resolve/", 1)
    return url


def gguf_n_layers(path) -> int | None:
    """The transformer layer count (``*.block_count``) read straight from the
    GGUF header — a few KB, no model load — so a GPU-layers recommendation can be
    derived. ``None`` if the file isn't a readable GGUF or lacks the key."""
    import struct
    _SCALAR = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}
    try:
        with open(path, "rb") as f:
            if f.read(4) != b"GGUF":
                return None
            f.read(4)                                   # version
            f.read(8)                                   # tensor count
            (kv_count,) = struct.unpack("<Q", f.read(8))

            def read_str():
                (n,) = struct.unpack("<Q", f.read(8))
                return f.read(n)

            def skip(vtype):
                if vtype in _SCALAR:
                    f.read(_SCALAR[vtype])
                elif vtype == 8:                        # string
                    read_str()
                elif vtype == 9:                        # array
                    (et,) = struct.unpack("<I", f.read(4))
                    (cnt,) = struct.unpack("<Q", f.read(8))
                    for _ in range(cnt):
                        skip(et)
                else:
                    raise ValueError(f"unknown gguf type {vtype}")

            for _ in range(kv_count):
                key = read_str()
                (vtype,) = struct.unpack("<I", f.read(4))
                if key.endswith(b".block_count"):
                    if vtype in (4, 5):
                        return struct.unpack("<I", f.read(4))[0]
                    if vtype in (10, 11):
                        return struct.unpack("<Q", f.read(8))[0]
                    return None
                skip(vtype)
    except (OSError, ValueError, struct.error):
        return None
    return None


# Setting key → environment variable consumed by WebKitGTK at startup.
_WEBKIT_ENV_KEYS = {
    "webkit_disable_dmabuf": "WEBKIT_DISABLE_DMABUF_RENDERER",
    "webkit_disable_compositing": "WEBKIT_DISABLE_COMPOSITING_MODE",
}


# Last settings dict logged by load_settings(), so we dump the full dict only
# when it changes — startup calls load_settings() ~7 times and logging the whole
# config each time is pure noise.
# Settings keys whose value is a secret and must never be logged verbatim. The
# value lives in the OS keyring (secret_store), not here; this only guards a legacy
# plaintext copy that a hand-edited vaults.yaml might still hold.
_SECRET_KEYS = {"ask_api_key"}

_last_logged_settings = None


def _migrate_settings(settings: dict) -> None:
    """In-place migration of renamed/removed settings for old configs."""
    # semantic_onnx_model / semantic_onnx_tokenizer were replaced by the folder
    # setting semantic_onnx_dir; carry a custom old location over so semantic
    # search keeps finding the model instead of silently reverting to default.
    # Drop both old keys unconditionally so the migration is one-shot once the
    # config is next saved — otherwise they linger and a later "reset to default"
    # (dir → "") gets silently undone by re-deriving from them on every load.
    model = settings.pop("semantic_onnx_model", None)
    tokenizer = settings.pop("semantic_onnx_tokenizer", None)
    old = model or tokenizer
    if old and not settings.get("semantic_onnx_dir"):
        settings["semantic_onnx_dir"] = os.path.dirname(old)


def load_settings() -> dict:
    """Load app settings from vaults.yaml, with safe defaults."""
    try:
        if not CONFIG_FILE.exists():
            # On a genuine first run there is no config dir either — that is normal
            # and stays quiet. But a *missing file next to an existing dir* means it
            # vanished, and every setting silently becomes a default: the likeliest
            # route to an unexplained reset. Warn, because a reset restores
            # loglevel: info and would hide a DEBUG line (it once did).
            if CONFIG_DIR.exists():
                logger.warning(
                    "config file %s is missing though %s exists — falling back to "
                    "default settings; a write from here would persist those defaults",
                    CONFIG_FILE, CONFIG_DIR)
            else:
                logger.debug("No config file, using default settings")
            return dict(_DEFAULT_SETTINGS)
        with open(CONFIG_FILE, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except (yaml.YAMLError, OSError) as exc:
        logger.warning("Failed to parse config for settings: %s", exc)
        return dict(_DEFAULT_SETTINGS)
    settings = dict(_DEFAULT_SETTINGS)
    settings.update(data.get("settings") or {})
    _migrate_settings(settings)
    global _last_logged_settings
    # Secrets belong in the keyring, not settings — but mask them in the debug
    # dump anyway (defence in depth: a legacy vaults.yaml may still carry one, and
    # loglevel: debug is what the manual-testing loop sets).
    loggable = {k: ("***" if k in _SECRET_KEYS and v else v)
                for k, v in settings.items() if k != "loglevel"}
    if loggable != _last_logged_settings:
        logger.debug("Loaded settings: %s", loggable)
        _last_logged_settings = loggable
    return settings


def _log_settings_write(before: dict, after: dict) -> None:
    """Report what a write changes, at a level a settings reset cannot hide.

    ``save_settings`` replaces the whole block, so a caller holding a stale or partial
    snapshot silently erases everything not in it — that is how a leaked timer once
    wiped 50 settings. Dropped keys are therefore a WARNING, ordinary changes an INFO;
    both survive the ``loglevel: info`` a reset restores.
    """
    # Which of the five writers this is. The logger name is core.config for all of
    # them and _FORMAT carries no filename, so without this a DROPS warning says what
    # was lost but not who lost it — the exact question the reset investigation could
    # not answer. (logging's stacklevel= cannot help: the format has no %(filename)s.)
    stack = traceback.extract_stack(limit=3)
    frame = stack[0] if len(stack) >= 3 else None
    where = f"{os.path.basename(frame.filename)}:{frame.lineno}" if frame else "?"

    dropped = sorted(set(before) - set(after))
    changed = sorted(k for k in after if k in before and before[k] != after[k])
    added = sorted(set(after) - set(before))

    def _v(key, value):
        return "***" if key in _SECRET_KEYS and value else value

    if dropped:
        logger.warning(
            "settings write from %s DROPS %d key(s) — the caller's snapshot is partial "
            "or stale: %s", where, len(dropped), ", ".join(dropped))
    if changed or added:
        parts = [f"{k}: {_v(k, before[k])!r} -> {_v(k, after[k])!r}" for k in changed]
        parts += [f"{k}: (new) {_v(k, after[k])!r}" for k in added]
        logger.info("settings write from %s: %s", where, "; ".join(parts))


_settings_singleton: dict | None = None


def settings() -> dict:
    """**The** settings of this process — one object, owned here.

    Every consumer gets the same dict, so a change made anywhere is visible
    everywhere and there is no second copy that could overwrite the first. That is
    the whole point: the components used to load their own snapshots, and
    :func:`save_settings` writes the block back as a whole, so whoever saved last
    reset every key the others had changed meanwhile.

    Mutate it in place (``settings()["key"] = value``) and call
    :func:`save_settings` to persist.
    """
    global _settings_singleton
    if _settings_singleton is None:
        _settings_singleton = load_settings()
    return _settings_singleton


def reload_settings() -> dict:
    """Re-read the file into the **existing** object (contents change, identity
    does not) — handing out a new dict would strand every holder with a stale one.
    For tests and for picking up a change made outside the app."""
    global _settings_singleton
    fresh = load_settings()
    if _settings_singleton is None:
        _settings_singleton = fresh
    else:
        _settings_singleton.clear()
        _settings_singleton.update(fresh)
    return _settings_singleton


def save_settings(settings_to_save: dict | None = None) -> None:
    """Persist settings into vaults.yaml (merged with existing vaults).

    Without an argument the owned state (:func:`settings`) is written — the normal
    case. Passing a dict stays possible for tests and one-off writes.
    """
    values = settings_to_save if settings_to_save is not None else settings()
    _invalidate_cache()
    _ensure_config_dir()
    existing: dict = {}
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as fh:
                existing = yaml.safe_load(fh) or {}
        except (yaml.YAMLError, OSError):
            existing = {}
    _log_settings_write(existing.get("settings") or {}, values)
    existing["settings"] = dict(values)     # a copy: the file gets a snapshot
    yaml_str = yaml.dump(existing, default_flow_style=False, sort_keys=False)
    try:
        _atomic_write(CONFIG_FILE, yaml_str)
    except OSError:
        logger.error("Failed to save settings to %s", CONFIG_FILE, exc_info=True)
        raise


def apply_webkit_env(values: dict | None = None) -> None:
    """Apply WebKit environment variables from *values* (default: the owned state).

    Must run before any WebKit module is imported (i.e. before the app
    window is created), otherwise the renderer/compositor is already
    initialised with the defaults.  Environment variables are only set,
    never unset — the app starts in a fresh process each time.
    """
    if values is None:
        values = settings()
    for setting_key, env_key in _WEBKIT_ENV_KEYS.items():
        if values.get(setting_key):
            os.environ[env_key] = "1"
