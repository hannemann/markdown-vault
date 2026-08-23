"""The model list for the active Ask backend — shared by Preferences and the
Quick-Open footer picker.

Two things are easy to get wrong and both produce a control that lies about what
it does, so they live here instead of in each picker:

* **Which setting selects a model depends on the backend.** The local backend
  loads ``ask.gguf.filename`` (a file), the server backends send ``ask.server.model``
  (a name). A picker that writes the wrong one changes nothing.
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
from dataclasses import dataclass, field
from pathlib import Path

from markdown_vault.core import config, secret_store
from markdown_vault.core.i18n import _
from markdown_vault.search.ask import openai_base

logger = logging.getLogger(__name__)

#: Backends that fetch their model list from a server (the rest is local files).
SERVER_BACKENDS = ("ollama", "openai")

_ENDPOINTS = {"ollama": "/api/tags", "openai": "/v1/models"}

#: Settings path holding the per-endpoint memory: ``{"<backend>|<url>": "model"}``.
MEMORY_KEY = "ask.server.model_by_endpoint"

#: Settings path holding one server URL per backend.
URL_MEMORY_KEY = "ask.server.url_by_backend"

#: Typical server URL per backend — llama.cpp serves :8080, Ollama :11434.
DEFAULT_URLS = {"ollama": "http://localhost:11434",
                "openai": "http://localhost:8080"}

#: Keyring name the API key used before it became per-endpoint.
LEGACY_KEY_NAME = "ask_api_key"

_cache: dict = {}
_status_by_endpoint: dict = {}
_inflight: set = set()
_lock = threading.Lock()

# ---------------------------------------------------------------- endpoint status

#: Nothing asked yet.
UNKNOWN = "unknown"
#: A request is out; the answer is not in.
PROBING = "probing"
#: Models listed.
OK = "ok"
#: HTTP 200 with an empty list.
EMPTY = "empty"
#: No list endpoint (404) or an unreadable answer — llama.cpp serves one model and
#: lists nothing, which is normal, not broken.
NO_LIST = "no_list"
#: The server refused the credentials (401/403).
UNAUTHORIZED = "unauthorized"
#: Any other HTTP error (500, 429, redirect loop): the server answered, but said
#: nothing about whether it can chat.
LIST_ERROR = "list_error"
#: No answer at all — refused, DNS failure, timeout.
UNREACHABLE = "unreachable"
#: The *local* backend cannot load its chosen GGUF (missing, not a GGUF, or the
#: binding is absent). Not a server state — the verdict wears the same shape so the
#: palette blocks and banners it exactly like a dead server. The user-facing reason
#: (from ``llama_runtime.availability``) rides in :attr:`EndpointStatus.error`.
LOCAL_UNAVAILABLE = "local_unavailable"

#: States in which asking is certain to fail: there is no server (or it rejects the
#: credentials the chat endpoint needs just as much), or the local model won't load.
#: Everything else may warn.
_BLOCKING = (UNAUTHORIZED, UNREACHABLE, LOCAL_UNAVAILABLE)


@dataclass
class EndpointStatus:
    """What the last model-list request says about one endpoint.

    The distinction the UI needs is *not* "did we get models" but "can a question
    work at all" — a server may legitimately have no list endpoint. So the class
    answers three separate questions: may we ask (:attr:`can_ask`), is there a list
    to choose from (:attr:`models_usable`), and is there something to tell the user
    (:attr:`message`).
    """

    state: str = UNKNOWN
    url: str = ""
    models: list = field(default_factory=list)
    error: str = ""            # the raw exception text, for the log and the detail

    @property
    def can_ask(self) -> bool:
        """False only when a question is certain to fail. An endpoint nobody has
        probed yet (or is being probed) does not block — the caller waits instead."""
        return self.state not in _BLOCKING

    @property
    def pending(self) -> bool:
        """A request is out and the verdict is not in — a caller with something to
        send should wait for it rather than fire blind."""
        return self.state == PROBING

    @property
    def transient(self) -> bool:
        """Whether re-probing could change this verdict.

        ``ok`` and ``no_list`` are properties of the server, not of its mood: a
        server without a list endpoint will not grow one while the app runs, so
        checking again on every palette open would just cost a round trip. An empty
        list, a rejected key, an unreachable host or a server error can all change
        under the running app, so those are worth another look.
        """
        return self.state not in (OK, NO_LIST)

    @property
    def models_usable(self) -> bool:
        """Whether there is a real list to pick from."""
        return self.state == OK and bool(self.models)

    @property
    def is_local(self) -> bool:
        """A local-model verdict: the fix is choosing or downloading a model in the
        settings, not re-probing a server — so the banner button leads there."""
        return self.state == LOCAL_UNAVAILABLE

    @property
    def message(self) -> str:
        """One sentence for the user, or ``""`` when there is nothing to say.

        Silent for ``ok``/``unknown``/``probing`` (nothing is wrong or nothing is
        known yet) and for ``no_list`` — a server without a list endpoint works
        fine, so warning about it would train the user to ignore warnings.
        """
        if self.state == UNREACHABLE:
            return _("{url} is not reachable — a question cannot be answered. "
                     "Check the server URL in Preferences → Search → Ask. "
                     "({error})").format(url=self.url, error=self.error)
        if self.state == UNAUTHORIZED:
            return _("The server rejected the API key — a question cannot be "
                     "answered. Add or fix the key in Preferences → Search → Ask.")
        if self.state == EMPTY:
            return _("{url} reports no models. Asking may still work if the "
                     "server serves a fixed model; otherwise install or configure "
                     "one.").format(url=self.url)
        if self.state == LIST_ERROR:
            return _("{url} could not list its models ({error}). Asking "
                     "may still work.").format(url=self.url, error=self.error)
        if self.state == LOCAL_UNAVAILABLE:
            return self.error      # the availability() reason, already user-facing
        return ""


def local_unavailable(reason: str) -> EndpointStatus:
    """A blocking verdict for the local backend whose chosen GGUF cannot load;
    *reason* is the user-facing text from :func:`llama_runtime.availability`. Shaped
    like a server verdict so the palette banners and blocks it the same way."""
    return EndpointStatus(state=LOCAL_UNAVAILABLE, error=reason)


def note_chat_failure(backend: str, url: str, exc: Exception) -> str:
    """Record what a failed **chat** request proves about the endpoint, and return
    the sentence for the user.

    The chat call is the most authoritative evidence there is — the model list is
    only a proxy for it. Without this, a server that dies while the palette is open
    keeps its cheerful verdict: no warning, submitting stays enabled, and every
    question fails one after another.

    Only endpoint-level verdicts are recorded (``unreachable``, ``unauthorized``).
    A chat-specific failure must not be relabelled through the list vocabulary: a
    chat 404 means "no such model", not "this server has no list endpoint", and a
    500 is about the request, not about reachability.
    """
    st = _classify(backend, url, exc)
    if st.state in _BLOCKING:
        _set_status(backend, url, st)
        cache_put(backend, url, [])       # the cached list is no longer trustworthy
    return st.message or _("The server could not answer: {reason}").format(
        reason=_reason(exc))


def explain(backend: str, url: str, exc: Exception) -> str:
    """A readable sentence for a failed **chat** request, from the same
    classification as the model list — so the answer area and the palette's banner
    speak one language instead of one saying "not reachable" and the other printing
    ``<urlopen error [Errno 111]>``. Falls back to the raw text for states that
    carry no message (a server without a list endpoint can still fail a chat)."""
    st = _classify(backend, url, exc)
    return st.message or _("The server could not answer: {reason}").format(
        reason=exc)


def status(backend: str, url: str) -> EndpointStatus:
    """The last known status of this endpoint — ``UNKNOWN`` if never probed."""
    with _lock:
        st = _status_by_endpoint.get(endpoint_key(backend, url))
    return st or EndpointStatus(url=_norm_url(backend, url))


def _set_status(backend: str, url: str, st: EndpointStatus) -> None:
    with _lock:
        _status_by_endpoint[endpoint_key(backend, url)] = st


def _classify(backend: str, url: str, exc: Exception) -> EndpointStatus:
    """Turn a failed list request into a state. One place, so the palette and
    Preferences cannot disagree about what a 404 means."""
    norm = _norm_url(backend, url)
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code in (401, 403):
            return EndpointStatus(UNAUTHORIZED, norm, error=f"HTTP {exc.code}")
        if exc.code == 404:
            return EndpointStatus(NO_LIST, norm, error="HTTP 404")
        return EndpointStatus(LIST_ERROR, norm, error=f"HTTP {exc.code}")
    if isinstance(exc, ValueError):        # JSON garbage, or not JSON at all
        return EndpointStatus(NO_LIST, norm, error=_reason(exc))
    return EndpointStatus(UNREACHABLE, norm, error=_reason(exc))


def _reason(exc: Exception) -> str:
    """The readable part of a network error. ``URLError`` stringifies as
    ``<urlopen error Connection refused>`` — repr noise in a user-facing sentence —
    while its ``reason`` is the plain cause."""
    reason = getattr(exc, "reason", None)
    return str(reason) if reason else str(exc)


def effective_backend(settings: dict) -> str:
    """The backend that would actually answer: ``auto`` always means the
    in-process one, so a picker must not offer server models there."""
    engine = config.get_setting(settings, "ask.engine") or config.default("ask.engine")
    if engine == "auto":
        return "local"
    return config.get_setting(settings, "ask.backend") or config.default("ask.backend")


def setting_key(backend: str) -> str:
    """The settings path that selects a model for *backend*."""
    return "ask.server.model" if backend in SERVER_BACKENDS else "ask.gguf.filename"


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
        _status_by_endpoint.clear()
        _inflight.clear()


def refresh_async(backend: str, url: str, api_key: str = "", on_settled=None) -> None:
    """Ask the server for its models in the background and record what came back.

    *on_settled* is called with the resulting :class:`EndpointStatus` when the
    request **finished, whatever the outcome** — from the worker thread, so a GTK
    caller must marshal it (``GLib.idle_add``). Reporting failures too is the whole
    point: it is what lets the UI say "not reachable" instead of silently keeping a
    stale list. A request already running for the same endpoint is not started
    twice.
    """
    if backend not in SERVER_BACKENDS:
        return
    key = endpoint_key(backend, url)
    with _lock:
        if key in _inflight:
            return
        _inflight.add(key)
    _set_status(backend, url, EndpointStatus(PROBING, _norm_url(backend, url)))

    def work():
        try:
            st = probe(backend, url, api_key)
        finally:
            with _lock:
                _inflight.discard(key)
        if on_settled is not None:
            on_settled(st)

    threading.Thread(target=work, daemon=True, name="ask-models").start()


def probe(backend: str, url: str, api_key: str = "",
          record: bool = True) -> EndpointStatus:
    """Ask the server for its models **now** and (by default) record the outcome.

    Blocking — for a caller that already runs its own worker thread (the explicit
    refresh in Preferences). Deliberately not deduplicated: it is a thing the user
    asked for. Both Ask entry points end up here, so the two surfaces cannot
    disagree about what the server said.

    *record*=False classifies and returns the status but writes **neither** the
    per-endpoint status **nor** the model cache. It is for a *second* consumer that
    shares the endpoint keying but must not affect Ask — the embedding backend:
    its verdict must never mute Ask, even against the same server (D4/ZB1).
    """
    try:
        models = fetch(backend, url, api_key)
        st = EndpointStatus(OK if models else EMPTY,
                            _norm_url(backend, url), models=models)
        if record:
            cache_put(backend, url, models)
    except Exception as exc:  # noqa: BLE001 — every outcome becomes a state
        st = _classify(backend, url, exc)
        if record:
            # A failure supersedes an earlier list: keeping it would make a server
            # that has since died look healthy for the rest of the session.
            cache_put(backend, url, [])
        logger.info("model list for %s|%s: %s (%s)",
                    backend, _norm_url(backend, url), st.state, st.error)
    if record:
        _set_status(backend, url, st)
    return st


# ---------------------------------------------------------------------- selection


def current(settings: dict) -> str:
    """The model the active backend would actually use right now."""
    if effective_backend(settings) not in SERVER_BACKENDS:
        return config.resolve_model_path(settings)
    return config.get_setting(settings, "ask.server.model") or ""


def _url_of(settings: dict) -> str:
    return config.get_setting(settings, "ask.server.url") or config.default("ask.server.url")


def list_for(settings: dict, on_refresh=None) -> list:
    """``[(label, value)]`` for the picker — local GGUF files or server models.

    Never blocks: for a server backend an empty cache yields the configured model
    alone and schedules a refresh. *on_refresh* is then called (from the worker
    thread) with the resulting :class:`EndpointStatus`, so the picker can
    repopulate and the UI can show what the server said.
    """
    backend = effective_backend(settings)
    if backend not in SERVER_BACKENDS:
        return [(Path(p).name, str(p)) for p in config.list_models(settings)]

    url = _url_of(settings)
    models = cache_get(backend, url)
    if models:
        return [(m, m) for m in models]
    if status(backend, url).state == UNKNOWN:
        # Only probe an endpoint nobody has asked yet. Probing "whenever the cache
        # is empty" would loop: a failure clears the cache, the settle callback
        # refreshes the picker, which lands here again — one 5 s attempt after
        # another for as long as the app runs. Re-checking is an explicit act
        # (reopening the palette, or "Try again").
        refresh_async(backend, url, api_key(settings), on_settled=on_refresh)
    cur = current(settings)
    return [(cur, cur)] if cur else []


def recall(settings: dict, backend: str, url: str) -> str:
    """The model last chosen for this endpoint, or ``""`` if it is new to us."""
    return (config.get_setting(settings, MEMORY_KEY) or {}).get(endpoint_key(backend, url), "")


def remember(settings: dict, backend: str, url: str, model: str) -> None:
    """Record *model* as the choice for this endpoint **and** make it active.

    For the **local** backend the picker's value is a full path; store only the
    **filename** (``ask.gguf.filename`` is a name in ``ask.gguf.dir``), so the choice
    survives the models folder moving. Server backends store the model name as-is.
    """
    if backend not in SERVER_BACKENDS:
        config.set_setting(settings, setting_key(backend), Path(model).name)
        return
    config.set_setting(settings, setting_key(backend), model)
    memory = dict(config.get_setting(settings, MEMORY_KEY) or {})
    memory[endpoint_key(backend, url)] = model
    config.set_setting(settings, MEMORY_KEY, memory)


def recall_url(settings: dict, backend: str) -> str:
    """The server URL for *backend* — the one last used there, else its usual port."""
    stored = (config.get_setting(settings, URL_MEMORY_KEY) or {}).get(backend)
    return stored or DEFAULT_URLS.get(backend, "")


def remember_url(settings: dict, backend: str, url: str) -> None:
    """Record *url* as this backend's server (not the app's)."""
    if backend not in SERVER_BACKENDS or not url:
        return
    urls = dict(config.get_setting(settings, URL_MEMORY_KEY) or {})
    urls[backend] = url
    config.set_setting(settings, URL_MEMORY_KEY, urls)


def switch_backend(settings: dict, previous: str, backend: str) -> str:
    """Switch the Ask backend, giving each provider its own URL and model back.

    Files the URL currently in ``ask.server.url`` under *previous*, then makes
    *backend* active with its own URL and the model chosen there. Returns the now
    active URL. The local backend has neither, so it leaves both alone.
    """
    if previous in SERVER_BACKENDS:
        remember_url(settings, previous, config.get_setting(settings, "ask.server.url") or "")
    config.set_setting(settings, "ask.backend", backend)
    if backend not in SERVER_BACKENDS:
        return config.get_setting(settings, "ask.server.url") or ""
    url = recall_url(settings, backend)
    config.set_setting(settings, "ask.server.url", url)
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
    backend = config.get_setting(settings, "ask.backend") or config.default("ask.backend")
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

    Only touches the model — the caller owns ``ask.backend``/``ask.server.url``.
    Returns the now-active model (``""`` when this endpoint is unknown, so the
    picker shows nothing selected rather than another server's model).
    """
    if backend not in SERVER_BACKENDS:
        return config.resolve_model_path(settings)
    model = recall(settings, backend, url)
    config.set_setting(settings, "ask.server.model", model)
    return model
