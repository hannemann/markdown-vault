"""In-process answer generation with llama-cpp-python — no LLM server needed.

Loads a GGUF model directly via ``llama_cpp.Llama`` and answers chat completions
inside this process, so Ask does not depend on a running Ollama/llama.cpp server.
The heavy ``llama_cpp`` import and the model load are lazy — the base app never
touches them — and one loaded model is cached across questions, reloaded only
when its path or runtime parameters change (loading is seconds and gigabytes of
RAM, far too costly to repeat per question).

The GGUF carries its own tokenizer and chat template, so
:meth:`LlamaCppChat.chat` mirrors the server backends: it hands the model a
``(system, user)`` message pair and returns the reply text.
"""

import logging
import os
import threading

logger = logging.getLogger(__name__)

# One cached model: (key, Llama). Guarded because Ask runs on a worker thread.
_MODEL = None
_MODEL_KEY = None
_LOCK = threading.Lock()

# llama.cpp log forwarding. Keep a strong reference to the ctypes callback (else
# it is garbage-collected and the C side calls a dangling pointer).
_LOG_CB = None
_LOG_INSTALLED = False
_LOG_LOCK = threading.Lock()
_LOG_BUF: list = []

# Cancellation. A ggml abort callback on the model context polls the current
# generation's should_cancel predicate; returning True aborts the running
# decode (prompt processing included). Strong ref to the ctypes callback kept.
_ABORT_HOLDER: dict = {"fn": None}
_ABORT_CB = None


def _abort_predicate(_user_data=None) -> bool:
    fn = _ABORT_HOLDER["fn"]
    try:
        return bool(fn()) if fn else False
    except Exception:          # noqa: BLE001 — never abort on a broken predicate
        return False


def _install_abort(llama) -> None:
    """Install the ggml abort callback on *llama*'s context (best-effort — some
    builds/versions may not expose it, in which case cancellation degrades to
    discarding the result while the compute finishes)."""
    global _ABORT_CB
    try:
        import llama_cpp
        if _ABORT_CB is None:
            _ABORT_CB = llama_cpp.ggml_abort_callback(_abort_predicate)
        llama_cpp.llama_set_abort_callback(llama._ctx.ctx, _ABORT_CB, None)
    except Exception:          # noqa: BLE001 — no abort API / private layout moved
        logger.debug("abort callback not installed", exc_info=True)


def _install_llama_logging() -> None:
    """Route llama.cpp's own logs into the dedicated ``markdown-vault.llama.log``
    (via :func:`logging_setup.get_llama_logger`). Registered once; no-op without
    the binding. llama.cpp emits text in fragments, so lines are buffered and
    flushed on newline."""
    global _LOG_CB, _LOG_INSTALLED
    if _LOG_INSTALLED:
        return
    try:
        import llama_cpp
    except ImportError:
        return
    from . import logging_setup
    _LOG_INSTALLED = True
    llog = logging_setup.get_llama_logger()
    # ggml log levels → Python levels; CONT/unknown continue at INFO.
    levels = {1: logging.DEBUG, 2: logging.INFO, 3: logging.WARNING,
              4: logging.ERROR}

    @llama_cpp.llama_log_callback
    def _cb(level, text, _user_data):
        try:
            s = (text.decode("utf-8", "replace")
                 if isinstance(text, (bytes, bytearray)) else str(text or ""))
        except Exception:      # noqa: BLE001 — never let logging crash inference
            return
        with _LOG_LOCK:
            _LOG_BUF.append(s)
            parts = "".join(_LOG_BUF).split("\n")
            _LOG_BUF.clear()
            remainder = parts.pop()          # trailing piece has no newline yet
            if remainder:
                _LOG_BUF.append(remainder)
            lines = parts
        for line in lines:
            line = line.rstrip()
            if line:
                llog.log(levels.get(level, logging.INFO), "%s", line)

    llama_cpp.llama_log_set(_cb, None)
    _LOG_CB = _cb                            # strong ref, must outlive the call


def is_available() -> bool:
    """Whether the ``llama_cpp`` binding is importable (the optional dependency
    is installed). Does not load a model."""
    try:
        import llama_cpp  # noqa: F401
        return True
    except ImportError:
        return False


def availability(model_path: str) -> str | None:
    """``None`` if a local answer can be generated, else a user-facing reason —
    the binding is missing, or the model file is not there yet."""
    if not is_available():
        return ("Local answer generation needs the llama-cpp-python package. "
                "Install the optional AI dependencies (make install-ai) and "
                "restart, or switch the Ask backend to a server in Preferences.")
    if not model_path or not os.path.exists(model_path):
        return (f"No local model file at {model_path or '(unset)'}. Download one "
                "in Preferences → Search → Ask (Model file → Download), then ask "
                "again.")
    try:                                   # a GGUF starts with the 'GGUF' magic
        with open(model_path, "rb") as fh:
            if fh.read(4) != b"GGUF":
                return (f"{model_path} is not a valid GGUF model file — "
                        "re-download it (use the file's download link, not its "
                        "web page).")
    except OSError:
        pass
    return None


_GPU_SUPPORTED = None


def supports_gpu() -> bool:
    """Whether the installed ``llama_cpp`` build can offload to a GPU (a Vulkan/
    CUDA/Metal build). ``False`` when the binding is missing or it is a CPU-only
    build. Cached — importing the binding is the only cost, and the answer can't
    change without reinstalling."""
    global _GPU_SUPPORTED
    if _GPU_SUPPORTED is None:
        try:
            import llama_cpp
            _GPU_SUPPORTED = bool(llama_cpp.llama_supports_gpu_offload())
        except Exception:      # noqa: BLE001 — missing binding / older API
            _GPU_SUPPORTED = False
    return _GPU_SUPPORTED


def physical_cores() -> int:
    """Physical CPU cores (distinct package+core in the Linux topology), so the
    thread default counts real cores, not SMT siblings. Falls back to half the
    logical count, then 1."""
    import glob
    from pathlib import Path
    ids = set()
    for core_f in glob.glob("/sys/devices/system/cpu/cpu[0-9]*/topology/core_id"):
        pkg_f = core_f.replace("core_id", "physical_package_id")
        try:
            core = Path(core_f).read_text().strip()
            package = Path(pkg_f).read_text().strip() if os.path.exists(pkg_f) else "0"
            ids.add((package, core))
        except OSError:
            pass
    if ids:
        return len(ids)
    return max(1, (os.cpu_count() or 2) // 2)


def default_threads() -> int:
    """The safe default thread count: half the physical cores (at least 1). This
    leaves compute headroom so the machine stays responsive while an answer is
    generated; the user can raise it for faster answers."""
    return max(1, physical_cores() // 2)


def _load(key):
    """Load a GGUF into a ``llama_cpp.Llama``. *key* is the cache key
    ``(path, n_ctx, n_gpu_layers, n_threads)``."""
    from llama_cpp import Llama
    kwargs = _llama_kwargs(key)
    logger.info("loading local model %s", kwargs)
    return Llama(**kwargs)


def _llama_kwargs(key) -> dict:
    """The ``Llama(**kwargs)`` for a cache *key* — pure, so the thread capping is
    testable without loading a model."""
    path, n_ctx, n_gpu_layers, n_threads = key
    # verbose=True so llama-cpp-python doesn't install its own suppressing log
    # callback — ours (see _install_llama_logging) then receives the load logs.
    kwargs = dict(model_path=path, n_ctx=n_ctx, n_gpu_layers=n_gpu_layers,
                  verbose=True)
    if n_threads:
        # Cap BOTH pools. n_threads governs token generation, but prompt
        # processing (prefill) — the heavy all-core burst that dominates CPU
        # inference — is governed by n_threads_batch, which otherwise defaults
        # to *every* core. Leaving it uncapped is what still spiked all cores
        # (and browned out a machine with no battery to buffer the surge) even
        # after the user lowered the thread count.
        kwargs["n_threads"] = n_threads
        kwargs["n_threads_batch"] = n_threads
    return kwargs


def get_model(model_path: str, n_ctx: int, n_gpu_layers: int = 0,
              n_threads: int = 0, on_load=None):
    """The cached ``Llama`` for these parameters, loading (and evicting the
    previous one) only when the key changes. *on_load* is called right before an
    actual (cache-miss) load, so the UI can show a 'Loading model…' phase only
    when a load really happens."""
    global _MODEL, _MODEL_KEY
    _install_llama_logging()             # capture the load logs into the llama log
    key = (model_path, int(n_ctx), int(n_gpu_layers), int(n_threads))
    with _LOCK:
        if _MODEL_KEY != key:
            if on_load is not None:
                on_load()
            _MODEL = _load(key)          # replaces the old model (frees its RAM)
            _MODEL_KEY = key
        return _MODEL


class LlamaCppChat:
    """A :class:`ask.ChatBackend` over an in-process GGUF model.

    *on_phase* is an optional ``callback(phase)`` the UI hooks for status: it
    fires ``"loading"`` only when the model actually loads and ``"thinking"``
    just before generation. *_model* is an injection seam for tests: pass a stub
    with a ``create_chat_completion`` method and no real model is loaded.
    """

    def __init__(self, model_path: str, num_ctx: int = 8192,
                 n_gpu_layers: int = 0, n_threads: int = 0,
                 temperature: float = 0.2, on_phase=None, should_cancel=None,
                 _model=None) -> None:
        self.model_path = model_path
        self.num_ctx = num_ctx
        self.n_gpu_layers = n_gpu_layers
        self.n_threads = n_threads
        self.temperature = temperature
        self._on_phase = on_phase
        self._should_cancel = should_cancel
        self._model = _model

    def _phase(self, name: str) -> None:
        if self._on_phase is not None:
            self._on_phase(name)

    def _cancelled(self) -> bool:
        try:
            return bool(self._should_cancel and self._should_cancel())
        except Exception:      # noqa: BLE001 — a broken predicate never aborts
            return False

    def _llama(self):
        if self._model is not None:
            return self._model
        return get_model(self.model_path, self.num_ctx, self.n_gpu_layers,
                         self.n_threads, on_load=lambda: self._phase("loading"))

    def chat(self, system: str, user: str) -> str:
        llama = self._llama()          # may fire "loading" on a cache miss
        self._phase("thinking")
        # Arm the abort callback so closing the palette / asking again interrupts
        # the running decode (prompt processing included), not just its display.
        _install_abort(llama)
        _ABORT_HOLDER["fn"] = self._should_cancel
        try:
            out = llama.create_chat_completion(
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": user}],
                temperature=self.temperature,
            )
        except Exception:          # noqa: BLE001
            if self._cancelled():
                return ""          # intentionally aborted; the result is discarded
            raise
        finally:
            _ABORT_HOLDER["fn"] = None
        text = ((out.get("choices") or [{}])[0].get("message") or {}).get(
            "content") or ""
        return text.strip()
