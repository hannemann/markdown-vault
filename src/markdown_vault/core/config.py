"""Markdown Vault — configuration management.

Handles reading and writing of vaults and settings stored in
``~/.config/de.hannemann.markdown-vault/settings.yaml``.  All paths are resolved to
absolute form on load and save to avoid duplicates that differ only
by relative path notation.
"""

import copy
import json
import logging
import os
import tempfile
import traceback
from pathlib import Path

import yaml

from markdown_vault.core import paths, validation

logger = logging.getLogger(__name__)

# In-memory cache for vaults loaded from settings.yaml.
_vaults_cache: list[dict[str, str]] | None = None

# The base directories live in core.paths (one definition for the whole app, shared
# with logging_setup); re-exported here so existing call sites keep working.
CONFIG_DIR = paths.CONFIG_DIR      # honours MDV_CONFIG_DIR, else XDG_CONFIG_HOME
CONFIG_FILE = CONFIG_DIR / "settings.yaml"

STATE_DIR = paths.STATE_DIR        # logs, debug dumps, session.json
CACHE_DIR = paths.CACHE_DIR        # regenerable: the semantic index
DATA_DIR = paths.DATA_DIR          # downloaded models (ONNX, GGUF)
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
            # best-effort temp removal; the failed write is re-raised right below
            pass
        raise


def _read_vaults_from_disk() -> list[dict[str, str]]:
    """Read vaults from settings.yaml on disk (no caching)."""
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
            logger.warning("Could not read existing %s while saving vaults; other "
                           "sections may be dropped", CONFIG_FILE, exc_info=True)
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
    "autosave": {"interval": 30},
    "view": {"default_mode": "edit"},
    # Graph view — cursor fisheye lens (mirrors graph_view.LENS_DEFAULTS; kept here so
    # the keys are known settings and survive a load rather than being dropped).
    "graph": {
        "fisheye": True,
        "cursor_labels": True,
        "lens_radius": 140.0,
        "lens_strength": 2.6,
        "label_radius": 160.0,
        "lens_in_sidebar": True,
    },
    # OKF lifecycle: hide deprecated notes from the vault tree AND the search
    # surfaces (with a visible "N hidden" notice). One shared, persisted toggle.
    # Top-level: it spans pages (tree + every search surface).
    "hide_deprecated": False,
    "editor": {"font_size": 14, "tab_width": 4, "wrap_text": True},
    "preview": {"zoom": 1.0, "allow_remote_images": False},
    "tabs": {
        "min_width": 150,
        "wrap": False,
        "switch_mode": "mru",
        "keybinding": {"next": "<Control>Tab", "prev": "<Shift><Control>Tab"},
    },
    "log": {"level": "info", "third_party": "warning", "glib": "critical"},
    "webkit": {"disable_dmabuf": False, "disable_compositing": False},
    "wikilink": {
        "autofix_normalize": False,
        "autofix_relink": False,
        "warn_on_save": False,
        "mark_broken": False,
    },
    # Semantic (vector) search — opt-in.
    "semantic": {
        "enabled": False,
        # "onnx" (local, recommended), "ollama" (server) or "openai" (an
        # OpenAI-compatible embeddings server, e.g. llama.cpp/vLLM, POST /v1/embeddings)
        "backend": "onnx",
        "min_score": 0.35,
        "ollama": {"url": "http://localhost:11434", "model": "nomic-embed-text"},
        # OpenAI-compatible embedding backend. No general default model name exists
        # for such a server, so it starts empty (a configured-but-unusable state the
        # UI must name). The API key is not here — it lives in the keyring under
        # semantic_api_key:<backend>|<url>; the debug-log masking hides a legacy copy.
        "openai": {"url": "http://localhost:8080", "model": ""},
        # Folder holding the ONNX files (model.onnx + tokenizer.json); both the
        # download target and the load source. Empty → the app data dir default.
        "onnx": {
            "dir": "",
            "model_url":
                "https://huggingface.co/Xenova/paraphrase-multilingual-MiniLM-L12-v2/"
                "resolve/main/onnx/model.onnx",
            "tokenizer_url":
                "https://huggingface.co/Xenova/paraphrase-multilingual-MiniLM-L12-v2/"
                "resolve/main/tokenizer.json",
        },
    },
    "ask": {
        # Answer engine — the top-level Ask control.
        #   "auto"   — the app configures everything: in-process backend, GPU offload
        #              when the build supports it, a safe thread count (recommended)
        #   "manual" — honour the advanced ask.backend / ask.local settings below
        #   "off"    — no answers are generated
        "engine": "auto",
        # Ask/answer (RAG): the manual-mode chat backend.
        #   "local"  — in-process GGUF via llama-cpp-python (no server)
        #   "ollama" — a running Ollama server (/api/chat)
        #   "openai" — an OpenAI-compatible server, e.g. llama.cpp (/v1)
        "backend": "local",
        # Reasoning models (Qwen3, …) think before answering: accurate but slow. For
        # grounded note Q&A, disabling it is faster and better calibrated. Only sent
        # to the backend when False, so non-reasoning models are unaffected.
        "reasoning": True,
        # Hybrid retrieval: fuse a BM25 (keyword) ranking into the semantic one so
        # exact tokens (names, config keys, shortcuts) that embeddings blur still
        # surface. On by default (measured +10/100 on a gold set, never worse).
        "hybrid": True,
        # How many notes are retrieved as context for an answer. On CPU the model
        # spends almost all its time *reading* this context, so fewer notes = much
        # faster (roughly linear); 10 suits a GPU, ~5 a slow CPU.
        "top_k": 10,
        # Context window (tokens) requested from Ollama. Its own default (2048)
        # truncates multi-note contexts. Used by the local and Ollama backends
        # (openai sizes its context server-side).
        "num_ctx": 8192,
        # Hard cap on generated tokens, so a model that degenerates into a repetition
        # loop still stops (it would otherwise run until the context is full).
        "max_tokens": 1024,
        "system_prompt": "",  # empty → the built-in default (ask.DEFAULT_SYSTEM_PROMPT)
        # Everything read only by the server backends (ollama / openai).
        "server": {
            # The active server URL for the current server backend (ollama or openai).
            "url": "http://localhost:11434",
            # The server model. The model choice belongs to the server, not the app.
            "model": "llama3.2",
            # "<backend>|<url>" → model name, so switching provider restores that
            # provider's model instead of sending the previous one to a server that
            # does not have it. Opaque data leaf (not a schema branch).
            "model_by_endpoint": {},
            # One URL per backend, so switching does not point the new backend at the
            # previous one's host. server.url is the active value. Opaque data leaf.
            "url_by_backend": {},
        },
        # llama.cpp runtime knobs, read only in the local (in-process) branch.
        "local": {
            # 0 GPU layers = pure CPU (safe default, works on a laptop); raise to
            # offload layers to a GPU. 0 threads = half the physical cores (runtime).
            "n_gpu_layers": 0,
            "n_threads": 0,
            # Prompt batch sizes. n_batch is the logical batch; n_ubatch the physical
            # micro-batch — the real prefill-speed lever on the GPU. 0 = llama.cpp
            # default (2048 / 512). Keep n_ubatch <= n_batch.
            "n_batch": 0,
            "n_ubatch": 0,
            # KV-cache precision per cache: "f16" (default) or quantized "q8_0"/"q4_0".
            # Quantizing K is free; quantizing V below f16 requires flash attention.
            "kv_type_k": "f16",
            "kv_type_v": "f16",
            "flash_attn": False,
            # Memory-map the model file (default). Off loads it fully into RAM.
            "use_mmap": True,
        },
        # The local GGUF model — a bare filename inside gguf.dir (not a full path;
        # a hand-edited absolute path collapses to its basename there). The file is
        # both the download target and the load source; empty → newest in gguf.dir.
        "gguf": {
            "filename": "",
            "url":
                "https://huggingface.co/bartowski/Llama-3.2-3B-Instruct-GGUF/"
                "resolve/main/Llama-3.2-3B-Instruct-Q4_K_M.gguf",
            # Folder the Ask GGUF picker searches and downloads into; empty → the
            # shared models_dir(). Kept separate from the Whisper models.
            "dir": "",
        },
    },
    # Document import — Whisper model size for audio transcription (tiny · base ·
    # small · medium · large-v3; bigger = more accurate, slower, larger download).
    "document": {"whisper_model": "base", "import_last_dir": ""},
}

# Dict-valued settings that are *data*, not schema branches: ``_flatten`` stops at
# them (their contents are user endpoint keys, not settings paths).
_OPAQUE_LEAVES = frozenset({
    "ask.server.model_by_endpoint",
    "ask.server.url_by_backend",
})

# JSON Schema type name for a Python value's exact type. ``bool`` is a subclass of
# ``int``, so this is keyed by ``type(value)`` (not ``isinstance``): a bool for an
# integer leaf is a type error, not a silent pass. One source, shared by the schema
# coverage test and the load-time validator, so the two cannot drift.
_JSON_TYPE = {bool: "boolean", int: "integer", float: "number",
              str: "string", dict: "object"}


def _navigate(tree: dict, path: str):
    """Return ``(parent_dict, leaf_key, found)`` for a dotted *path* in *tree*.

    ``found`` is False when any branch along the path is missing or not a dict.
    """
    parts = path.split(".")
    node = tree
    for part in parts[:-1]:
        if not isinstance(node, dict) or part not in node:
            return None, parts[-1], False
        node = node[part]
    if not isinstance(node, dict) or parts[-1] not in node:
        return node if isinstance(node, dict) else None, parts[-1], False
    return node, parts[-1], True


def get_setting(settings: dict, path: str, default_value=None):
    """Read the leaf at dotted *path* from *settings*.

    Returns *default_value* when the path is absent. Use
    ``get_setting(settings, path, config.default(path))`` at read sites so a
    field the user cleared to empty falls back to the default — the nested
    counterpart of the old ``settings.get(key) or config.default(key)``.
    """
    parent, leaf, found = _navigate(settings, path)
    if not found:
        return default_value
    return parent[leaf]


def set_setting(settings: dict, path: str, value) -> None:
    """Write *value* to the leaf at dotted *path* in *settings*, in place.

    Missing intermediate branches are created, so a partial user file still
    accepts a write to a deep path.
    """
    parts = path.split(".")
    node = settings
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[parts[-1]] = value


def default(path):
    """The built-in default value for a setting *path* (``""`` if unknown).

    Takes a dotted path (``"ask.server.url"``) and navigates the nested
    ``_DEFAULT_SETTINGS``. Returns ``""`` for an unknown path so a read site's
    ``get_setting(s, path) or config.default(path)`` fallback degrades to empty
    rather than raising.
    """
    parent, leaf, found = _navigate(_DEFAULT_SETTINGS, path)
    if not found:
        return ""
    return parent[leaf]


def _flatten(settings: dict, _prefix: str = "") -> dict:
    """Flatten a nested settings tree to ``{dotted-path: leaf-value}``.

    Descends into schema branches but stops at ``_OPAQUE_LEAVES`` (dict-valued
    *data*, e.g. the per-endpoint model memory), which flatten as a single leaf.
    Used by the lost-write, secret-masking and coverage checks, all of which
    must compare *leaves*, not top-level branches.
    """
    flat = {}
    for key, value in settings.items():
        path = f"{_prefix}{key}"
        if isinstance(value, dict) and path not in _OPAQUE_LEAVES:
            flat.update(_flatten(value, f"{path}."))
        else:
            flat[path] = value
    return flat


def _load_schema():
    """The settings JSON Schema (docs source), or ``None`` if not installed.

    ``core.config`` is the lowest layer and must start even when the schema data
    file is absent (a forgotten ``install_data``); validation is then skipped. A
    test keeps ``settings.schema.json`` in ``core/meson.build``, so a correct build
    never reaches this — hence a ``debug`` log, not a warning a user cannot act on.
    """
    try:
        return json.loads(Path(__file__).with_name("settings.schema.json")
                          .read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.debug("settings schema not found — skipping load-time validation")
        return None


def _subschema(node: dict, segment: str):
    """The subschema for *segment* under *node*: a named property, else the open
    map ``additionalProperties`` — the JSON Schema resolution order, so every open
    map (``debug.dump``, any future one) is handled without a special case."""
    return (node.get("properties") or {}).get(segment) or node.get("additionalProperties")


def _leaf_schema(schema: dict, path: str):
    """Walk the schema to the leaf at dotted *path*, or ``None`` (unknown path)."""
    node = schema
    for segment in path.split("."):
        if not isinstance(node, dict):
            return None
        node = _subschema(node, segment)
    return node if isinstance(node, dict) else None


def _validate_settings(settings: dict) -> None:
    """Warn (never block) on a setting that does not match the schema.

    Runs at load after :func:`_migrate_settings`. Each flattened leaf is checked
    against ``settings.schema.json``: an **unknown path** is warned and left in
    place (deleting it would be data loss, so the warning recurs until the user
    edits the file — hence the message names the file); a **wrong type** or an
    **out-of-enum** value is warned and reset to the default, so a hand-edit cannot
    silently degrade a feature. The app always starts. No dependency — the schema
    is read as plain data, not with ``jsonschema``.
    """
    schema = _load_schema()
    if schema is None:
        return
    for path, value in _flatten(settings).items():
        leaf = _leaf_schema(schema, path)
        if leaf is None:
            logger.warning("unknown setting %r in %s — ignored", path, CONFIG_FILE)
            continue
        expected = leaf.get("type")
        actual = _JSON_TYPE.get(type(value))
        # An integer satisfies a "number" leaf (JSON Schema: integer ⊂ number), so
        # preview.zoom: 2 is not a false positive.
        type_ok = actual == expected or (expected == "number" and actual == "integer")
        if expected and not type_ok:
            logger.warning("setting %r in %s: expected %s, got %s — using the default",
                           path, CONFIG_FILE, expected, actual or type(value).__name__)
            set_setting(settings, path, leaf.get("default"))
            continue
        enum = leaf.get("enum")
        if enum is not None and value not in enum:
            logger.warning("setting %r in %s: %r is not one of %s — using the default",
                           path, CONFIG_FILE, value, enum)
            set_setting(settings, path, leaf.get("default"))


def models_dir() -> Path:
    """The shared model folder under the data dir. Holds the downloaded GGUF
    models (Ask) and, in per-model subfolders, the audio import's Whisper models —
    so it is a *default*, not an Ask-only setting (see :func:`ask_models_dir`)."""
    return DATA_DIR / "models"


def ask_models_dir(settings: dict) -> Path:
    """Folder the Ask GGUF picker searches and downloads into: the configured
    ``ask_models_dir`` if set, else the shared :func:`models_dir`. Kept apart from
    ``models_dir()`` itself, which also holds the audio import's Whisper models —
    those must not move when the Ask folder is changed."""
    raw = get_setting(settings or {}, "ask.gguf.dir", "")
    return Path(raw) if raw else models_dir()


def is_gguf(path) -> bool:
    """Whether *path* is really a GGUF model — it starts with the ``GGUF`` magic.
    Guards against a saved HTML page (e.g. a HuggingFace *blob* URL fetched by
    mistake) being offered as a model."""
    try:
        with open(path, "rb") as fh:
            return fh.read(4) == b"GGUF"
    except OSError:
        # an unreadable file is not a usable GGUF; the caller filters it out
        return False


def is_onnx(path) -> bool:
    """Whether *path* plausibly is an ONNX model.

    ONNX is protobuf and has no ASCII magic like GGUF's, so this checks the first byte for
    ``0x08`` — protobuf's tag for field 1 (``ir_version``), which writers emit first, and
    which every model checked here starts with. Weaker than a magic string, and enough for
    what actually turns up by mistake: an HTML error page, a captive-portal login, a JSON
    error body. Those otherwise reach a NATIVE parser, which is a memory-safety surface
    rather than a parse error.

    A false rejection is recoverable: a model placed in the folder by hand is never passed
    through here, only a downloaded one is.
    """
    try:
        with open(path, "rb") as fh:
            return fh.read(1) == b"\x08"
    except OSError:
        # unreadable is not usable; same convention as is_gguf
        return False


def is_tokenizer_json(path) -> bool:
    """Whether *path* is a tokenizer definition — the check for a downloaded
    ``tokenizer.json``.

    Parsing alone would admit the very thing this guards against: a server's JSON error body
    is valid JSON. So it also requires the ``model`` key, the tokenizer algorithm, which
    every HuggingFace ``tokenizer.json`` carries.

    Parsing the whole file has a second effect worth keeping: a truncated download does not
    parse, so this path detects an interrupted transfer even when the server sent no
    ``Content-Length`` — which the model download cannot (see
    :func:`model_download.download_to`).
    """
    try:
        with open(path, "rb") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        # ValueError covers JSONDecodeError and a bad encoding; either way not a tokenizer
        return False
    return isinstance(data, dict) and "model" in data


def list_models(settings: dict) -> list:
    """The **valid** GGUF files in the Ask models folder (:func:`ask_models_dir`),
    newest first — files that only carry a ``.gguf`` name but aren't GGUF are
    skipped."""
    d = ask_models_dir(settings)
    if not d.exists():
        return []
    files = [p for p in d.glob("*.gguf") if p.is_file() and is_gguf(p)]
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def resolve_model_path(settings: dict) -> str:
    """The **absolute** path of the GGUF the local backend should load.

    ``ask.gguf.filename`` is a bare filename in :func:`ask_models_dir`; it is
    joined to that folder — only the basename is used, so a hand-edited absolute
    path collapses to its name there — and returned if it exists and is a real
    GGUF. A **set but unresolvable** choice yields ``""`` — the error is surfaced
    by the caller (a banner naming the wanted model), not papered over by loading
    a different model. Only an **empty** choice falls back to the newest model in
    the folder.
    """
    explicit = get_setting(settings, "ask.gguf.filename") or ""
    if explicit:
        cand = ask_models_dir(settings) / Path(explicit).name
        if cand.exists() and is_gguf(cand):
            return str(cand)
        return ""      # set but gone/invalid: block, don't silently switch
    models = list_models(settings)
    return str(models[0]) if models else ""


def ask_gguf_wanted_path(settings: dict) -> str:
    """The absolute path the chosen model *would* have, whether or not it exists —
    the models folder + the stored filename (basename only). An **empty** choice
    falls back to :func:`resolve_model_path` (the newest model, or ``""``).

    Unlike :func:`resolve_model_path` this does not blank a set-but-missing choice:
    the error path needs the *wanted* name to tell the user which model is gone,
    where ``resolve_model_path`` would already have returned ``""``."""
    name = get_setting(settings, "ask.gguf.filename") or ""
    if not name:
        return resolve_model_path(settings)
    return str(ask_models_dir(settings) / Path(name).name)


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
        # metadata probe: None means 'block count unknown', handled by the caller
        return None
    return None


# Setting path → environment variable consumed by WebKitGTK at startup.
_WEBKIT_ENV_KEYS = {
    "webkit.disable_dmabuf": "WEBKIT_DISABLE_DMABUF_RENDERER",
    "webkit.disable_compositing": "WEBKIT_DISABLE_COMPOSITING_MODE",
}


def _is_secret(path: str) -> bool:
    """Whether a flattened setting *path* names a secret (masked in logs).

    Matched on the leaf so it survives nesting (``ask.api_key``,
    ``semantic.openai.api_key``) and any legacy flat ``*_api_key``. The value
    itself lives in the OS keyring, not the settings; this only guards a legacy
    plaintext copy a hand-edited settings.yaml might still hold.

    **Convention (a promise, because this is a pattern, not a list):** a secret
    setting MUST use a leaf ending in ``api_key``. A secret named otherwise
    (``token``, ``…secret``) would silently go unmasked — extend this predicate
    when adding one, since nothing else forces the thought.
    """
    return path.rsplit(".", 1)[-1].endswith("api_key")


def _deep_merge(base: dict, override: dict) -> None:
    """Recursively merge *override* into *base*, in place.

    A dict value merges branch-wise so a user file that overrides one leaf
    (``ask: {top_k: 12}``) keeps the branch's other defaults; any non-dict value
    replaces. Opaque data leaves default to ``{}``, so merging a user's dict into
    them is the same as replacing — no special case needed.
    """
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


# Last settings dict logged by load_settings(), so we dump the full dict only
# when it changes — startup calls load_settings() ~7 times and logging the whole
# config each time is pure noise.
_last_logged_settings = None


def _migrate_settings(settings: dict) -> None:
    """In-place migration of renamed/removed settings for old configs."""
    # semantic_onnx_model / semantic_onnx_tokenizer were replaced by the folder
    # setting semantic.onnx.dir; carry a custom old location over so semantic
    # search keeps finding the model instead of silently reverting to default.
    # Drop both old keys unconditionally so the migration is one-shot once the
    # config is next saved — otherwise they linger and a later "reset to default"
    # (dir → "") gets silently undone by re-deriving from them on every load.
    model = settings.pop("semantic_onnx_model", None)
    tokenizer = settings.pop("semantic_onnx_tokenizer", None)
    old = model or tokenizer
    if old and not get_setting(settings, "semantic.onnx.dir"):
        set_setting(settings, "semantic.onnx.dir", os.path.dirname(old))


def load_settings() -> dict:
    """Load app settings from settings.yaml, with safe defaults."""
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
            return copy.deepcopy(_DEFAULT_SETTINGS)
        with open(CONFIG_FILE, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except (yaml.YAMLError, OSError) as exc:
        logger.warning("Failed to parse config for settings: %s", exc)
        return copy.deepcopy(_DEFAULT_SETTINGS)
    settings = copy.deepcopy(_DEFAULT_SETTINGS)
    _deep_merge(settings, data.get("settings") or {})
    _migrate_settings(settings)
    _validate_settings(settings)
    global _last_logged_settings
    # Secrets belong in the keyring, not settings — but mask them in the debug
    # dump anyway (defence in depth: a legacy settings.yaml may still carry one, and
    # loglevel: debug is what the manual-testing loop sets). Flatten first so the
    # mask and the change-detection compare leaves, not whole branches.
    loggable = {p: ("***" if _is_secret(p) and v else v)
                for p, v in _flatten(settings).items() if p != "log.level"}
    if loggable != _last_logged_settings:
        logger.debug("Loaded settings: %s", loggable)
        _last_logged_settings = loggable
    return settings


def _log_settings_write(before: dict, after: dict) -> None:
    """Report what a write changes, at a level a settings reset cannot hide.

    ``save_settings`` replaces the whole block, so a caller holding a stale or partial
    snapshot silently erases everything not in it — that is how a leaked timer once
    wiped 50 settings. Both dicts are **flattened** first, so a dropped *nested leaf*
    (``ask.server.url`` gone while the ``ask`` branch stays) is still seen — a
    top-level diff would miss it. Dropped leaves are a WARNING, ordinary changes an
    INFO; both survive the ``loglevel: info`` a reset restores.
    """
    # Which of the five writers this is. The logger name is core.config for all of
    # them and _FORMAT carries no filename, so without this a DROPS warning says what
    # was lost but not who lost it — the exact question the reset investigation could
    # not answer. (logging's stacklevel= cannot help: the format has no %(filename)s.)
    stack = traceback.extract_stack(limit=3)
    frame = stack[0] if len(stack) >= 3 else None
    where = f"{os.path.basename(frame.filename)}:{frame.lineno}" if frame else "?"

    fbefore, fafter = _flatten(before), _flatten(after)
    dropped = sorted(set(fbefore) - set(fafter))
    changed = sorted(k for k in fafter if k in fbefore and fbefore[k] != fafter[k])
    added = sorted(set(fafter) - set(fbefore))

    def _v(key, value):
        return "***" if _is_secret(key) and value else value

    if dropped:
        logger.warning(
            "settings write from %s DROPS %d leaf(s) — the caller's snapshot is partial "
            "or stale: %s", where, len(dropped), ", ".join(dropped))
    if changed or added:
        parts = [f"{k}: {_v(k, fbefore[k])!r} -> {_v(k, fafter[k])!r}" for k in changed]
        parts += [f"{k}: (new) {_v(k, fafter[k])!r}" for k in added]
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
    """Persist settings into settings.yaml (merged with existing vaults).

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
            logger.warning("Could not read existing %s while saving settings; other "
                           "sections may be dropped", CONFIG_FILE, exc_info=True)
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
    for setting_path, env_key in _WEBKIT_ENV_KEYS.items():
        if get_setting(values, setting_path):
            os.environ[env_key] = "1"
