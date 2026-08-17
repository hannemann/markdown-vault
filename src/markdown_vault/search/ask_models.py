"""The model list for the active Ask backend — shared by Preferences and the
Quick-Open footer picker.

Two things are easy to get wrong and both produce a control that lies about what
it does, so they live here instead of in each picker:

* **Which setting selects a model depends on the backend.** The local backend
  loads ``ask_gguf_path`` (a file), the server backends send ``ask_model`` (a
  name). A picker that writes the wrong one changes nothing.
* **A server's settings belong to that server, not to the app.** Switching from
  Ollama to a remote OpenAI-compatible server must not leave the Ollama model
  selected — it would be sent to a server that has never heard of it. The same
  goes for the URL (one shared value points the new backend at the old host) and
  for the API key (a paid provider's key must not travel to the next endpoint
  someone types in). Model, URL and key are therefore remembered per provider and
  restored when you come back.

Listing must never block: the palette opens on Ctrl+Space, so a server list is
served from cache and refreshed in the background.
"""

import json
import logging
import threading
import urllib.error
import urllib.request
from pathlib import Path

from markdown_vault.core import config, secret_store
from markdown_vault.search.ask import openai_base

logger = logging.getLogger(__name__)

#: Backends that fetch their model list from a server (the rest is local files).
SERVER_BACKENDS = ("ollama", "openai")

_ENDPOINTS = {"ollama": "/api/tags", "openai": "/v1/models"}

#: Settings key holding the per-endpoint memory: ``{"<backend>|<url>": "model"}``.
MEMORY_KEY = "ask_model_by_endpoint"

#: Settings key holding one server URL per backend.
URL_MEMORY_KEY = "ask_url_by_backend"

#: Typical server URL per backend — llama.cpp serves :8080, Ollama :11434.
DEFAULT_URLS = {"ollama": "http://localhost:11434",
                "openai": "http://localhost:8080"}

#: Keyring name the API key used before it became per-endpoint.
LEGACY_KEY_NAME = "ask_api_key"

_cache: dict = {}
_inflight: set = set()
_lock = threading.Lock()


def effective_backend(settings: dict) -> str:
    """The backend that would actually answer: ``auto`` always means the
    in-process one, so a picker must not offer server models there."""
    engine = settings.get("ask_engine") or config.default("ask_engine")
    if engine == "auto":
        return "local"
    return settings.get("ask_backend") or config.default("ask_backend")


def setting_key(backend: str) -> str:
    """The settings key that selects a model for *backend*."""
    return "ask_model" if backend in SERVER_BACKENDS else "ask_gguf_path"


def endpoint(backend: str):
    """The path listing the models of *backend*, or ``None`` if it has no server."""
    return _ENDPOINTS.get(backend)


def _norm_url(backend: str, url: str) -> str:
    """The URL in one canonical spelling, so ``host`` and ``host/v1`` — which
    address the same server — do not get two cache entries and two memories."""
    if backend == "openai":
        return openai_base(url)
    return (url or "").rstrip("/")


def endpoint_key(backend: str, url: str) -> str:
    """Identity of a server: same key ⇒ same model list and same remembered choice."""
    return f"{backend}|{_norm_url(backend, url)}"


# --------------------------------------------------------------------------- fetch


def fetch(backend: str, url: str, api_key: str = "") -> list:
    """Ask the server for its models. Blocking — call it from :func:`prime`, not
    from the UI thread. Returns ``[]`` for a backend without a model endpoint."""
    path = endpoint(backend)
    if not path:
        return []
    base = openai_base(url) if backend == "openai" else (url or "").rstrip("/")
    headers = {"Accept": "application/json"}
    if api_key:                                # auth only when a key is configured
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(base + path, headers=headers)
    with urllib.request.urlopen(req, timeout=5.0) as resp:
        data = json.loads(resp.read())
    if backend == "ollama":
        entries = data.get("models") or []
        return [m.get("name", "") for m in entries if m.get("name")]
    entries = data.get("data") or []
    return [m.get("id", "") for m in entries if m.get("id")]


def cache_put(backend: str, url: str, models: list) -> None:
    with _lock:
        _cache[endpoint_key(backend, url)] = list(models)


def cache_get(backend: str, url: str) -> list:
    with _lock:
        return list(_cache.get(endpoint_key(backend, url), []))


def clear_cache() -> None:
    with _lock:
        _cache.clear()
        _inflight.clear()


def prime(backend: str, url: str, api_key: str = "", on_done=None) -> None:
    """Refresh the cached list for a server in the background.

    *on_done* is called with the model list when the fetch succeeded — from the
    worker thread, so a GTK caller must marshal it (``GLib.idle_add``). A fetch
    already running for the same endpoint is not started twice.
    """
    if backend not in SERVER_BACKENDS:
        return
    key = endpoint_key(backend, url)
    with _lock:
        if key in _inflight:
            return
        _inflight.add(key)

    def work():
        try:
            models = fetch(backend, url, api_key)
            cache_put(backend, url, models)
            if on_done is not None:
                on_done(models)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            # Unreachable server / garbage answer: keep whatever we had and stay
            # quiet in the UI — the picker still offers the configured model.
            logger.info("model list unavailable for %s: %s", key, exc)
        except Exception:
            logger.warning("model list fetch failed for %s", key, exc_info=True)
        finally:
            with _lock:
                _inflight.discard(key)

    threading.Thread(target=work, daemon=True, name="ask-models").start()


# ---------------------------------------------------------------------- selection


def current(settings: dict) -> str:
    """The model the active backend would actually use right now."""
    if effective_backend(settings) not in SERVER_BACKENDS:
        return config.resolve_model_path(settings)
    return settings.get("ask_model") or ""


def _url_of(settings: dict) -> str:
    return settings.get("ask_ollama_url") or config.default("ask_ollama_url")


def list_for(settings: dict, on_refresh=None) -> list:
    """``[(label, value)]`` for the picker — local GGUF files or server models.

    Never blocks: for a server backend an empty cache yields the configured model
    alone and schedules a refresh. *on_refresh* is then called (from the worker
    thread) once the real list has arrived, so the picker can repopulate.
    """
    backend = effective_backend(settings)
    if backend not in SERVER_BACKENDS:
        return [(Path(p).name, str(p)) for p in config.list_models()]

    url = _url_of(settings)
    models = cache_get(backend, url)
    if models:
        return [(m, m) for m in models]
    prime(backend, url, api_key(settings), on_done=on_refresh)
    cur = current(settings)
    return [(cur, cur)] if cur else []


def recall(settings: dict, backend: str, url: str) -> str:
    """The model last chosen for this endpoint, or ``""`` if it is new to us."""
    return (settings.get(MEMORY_KEY) or {}).get(endpoint_key(backend, url), "")


def remember(settings: dict, backend: str, url: str, model: str) -> None:
    """Record *model* as the choice for this endpoint **and** make it active."""
    settings[setting_key(backend)] = model
    if backend not in SERVER_BACKENDS:
        return
    memory = dict(settings.get(MEMORY_KEY) or {})
    memory[endpoint_key(backend, url)] = model
    settings[MEMORY_KEY] = memory


def recall_url(settings: dict, backend: str) -> str:
    """The server URL for *backend* — the one last used there, else its usual port."""
    stored = (settings.get(URL_MEMORY_KEY) or {}).get(backend)
    return stored or DEFAULT_URLS.get(backend, "")


def remember_url(settings: dict, backend: str, url: str) -> None:
    """Record *url* as this backend's server (not the app's)."""
    if backend not in SERVER_BACKENDS or not url:
        return
    urls = dict(settings.get(URL_MEMORY_KEY) or {})
    urls[backend] = url
    settings[URL_MEMORY_KEY] = urls


def switch_backend(settings: dict, previous: str, backend: str) -> str:
    """Switch the Ask backend, giving each provider its own URL and model back.

    Files the URL currently in ``ask_ollama_url`` under *previous*, then makes
    *backend* active with its own URL and the model chosen there. Returns the now
    active URL. The local backend has neither, so it leaves both alone.
    """
    if previous in SERVER_BACKENDS:
        remember_url(settings, previous, settings.get("ask_ollama_url") or "")
    settings["ask_backend"] = backend
    if backend not in SERVER_BACKENDS:
        return settings.get("ask_ollama_url") or ""
    url = recall_url(settings, backend)
    settings["ask_ollama_url"] = url
    activate(settings, backend, url)
    return url


# --------------------------------------------------------------------------- key


def secret_name(backend: str, url: str) -> str:
    """Keyring name of the API key for this endpoint. The name carries the
    endpoint, so a key is only ever sent to the server it was entered for."""
    return f"{LEGACY_KEY_NAME}:{endpoint_key(backend, url)}"


def api_key(settings: dict) -> str:
    """The API key for the endpoint that would answer right now (``""`` if none)."""
    backend = effective_backend(settings)
    if backend not in SERVER_BACKENDS:
        return ""
    return secret_store.get_secret(secret_name(backend, _url_of(settings)))


def adopt_legacy_key(settings: dict) -> bool:
    """Move a pre-existing app-wide key onto the configured endpoint, once.

    The old single ``ask_api_key`` was already being sent to whatever server was
    configured, so adopting it for exactly that endpoint takes nothing away — and
    it stops there instead of following the next URL the user types.
    """
    legacy = secret_store.get_secret(LEGACY_KEY_NAME)
    if not legacy:
        return False
    backend = settings.get("ask_backend") or config.default("ask_backend")
    if backend not in SERVER_BACKENDS:
        return False
    name = secret_name(backend, _url_of(settings))
    if secret_store.get_secret(name):
        return False                      # this endpoint already has its own key
    if not secret_store.set_secret(name, legacy):
        return False
    secret_store.set_secret(LEGACY_KEY_NAME, "")   # no app-wide key left behind
    logger.info("adopted the app-wide Ask API key for %s", endpoint_key(
        backend, _url_of(settings)))
    return True


def activate(settings: dict, backend: str, url: str) -> str:
    """Reconcile the active model with a newly selected *backend*/*url*.

    Only touches the model — the caller owns ``ask_backend``/``ask_ollama_url``.
    Returns the now-active model (``""`` when this endpoint is unknown, so the
    picker shows nothing selected rather than another server's model).
    """
    if backend not in SERVER_BACKENDS:
        return config.resolve_model_path(settings)
    model = recall(settings, backend, url)
    settings["ask_model"] = model
    return model
