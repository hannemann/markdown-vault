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

# get_model hands every worker the same cached Llama; its C context is not
# reentrant, so only one generation may run on it at a time.
_CHAT_LOCK = threading.Lock()


class ReasoningBudgetExhausted(Exception):
    """A reasoning model spent its entire token budget inside an unclosed <think>
    block (no </think> ever arrived), so there is no answer — only the raw chain
    of thought, which must never be shown as the grounded answer."""


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
    except Exception:
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
        # no optional binding → llama log routing just stays uninstalled
        return
    from markdown_vault.core import logging_setup
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
        # import success IS the availability answer this returns
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
                "in Preferences → Search → Ask (Model Download), then ask "
                "again.")
    try:                                   # a GGUF starts with the 'GGUF' magic
        with open(model_path, "rb") as fh:
            if fh.read(4) != b"GGUF":
                return (f"{model_path} is not a valid GGUF model file — "
                        "re-download it (use the file's download link, not its "
                        "web page).")
    except OSError:
        # unreadable model file: let the real load report it, not this sniff
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
            # skip an unreadable topology entry; the empty-set fallback below covers it
            pass
    if ids:
        return len(ids)
    return max(1, (os.cpu_count() or 2) // 2)


def default_threads() -> int:
    """The safe default thread count: half the physical cores (at least 1). This
    leaves compute headroom so the machine stays responsive while an answer is
    generated; the user can raise it for faster answers."""
    return max(1, physical_cores() // 2)


def vram_bytes() -> int | None:
    """Device-local GPU memory in bytes (amdgpu ``mem_info_vram_total`` — on an
    APU this is the BIOS UMA carve-out). ``None`` if it can't be read."""
    import glob
    for f in glob.glob("/sys/class/drm/card*/device/mem_info_vram_total"):
        try:
            with open(f) as fh:
                return int(fh.read().strip())
        except (OSError, ValueError):
            # unreadable/garbled sysfs → VRAM unknown (None), callers handle it
            pass
    return None


def is_amd_gpu() -> bool:
    """Whether an AMD GPU is present (PCI vendor ``0x1002``). Its Mesa/RADV Vulkan
    flash-attention path can be unstable on older/APU parts (device faults).

    Intentionally retained though currently unused: a small, tested GPU probe kept
    alongside :func:`is_shared_memory_gpu` and :func:`supports_gpu` for future
    driver-specific decisions — not dead code awaiting removal (R52.5)."""
    import glob
    for f in glob.glob("/sys/class/drm/card*/device/vendor"):
        try:
            with open(f) as fh:
                if fh.read().strip().lower() == "0x1002":
                    return True
        except OSError:
            # unreadable vendor node → this card just doesn't count as AMD
            pass
    return False


def gtt_bytes() -> int | None:
    """GTT (system memory the GPU can borrow) in bytes. On an APU this dwarfs the
    small VRAM carve-out, which is how we tell shared-memory GPUs apart."""
    import glob
    for f in glob.glob("/sys/class/drm/card*/device/mem_info_gtt_total"):
        try:
            with open(f) as fh:
                return int(fh.read().strip())
        except (OSError, ValueError):
            # unreadable/garbled sysfs → GTT unknown (None), callers handle it
            pass
    return None


def _usable_vram(vram: int) -> int:
    """VRAM left for model weights after reserving KV cache + compute buffers +
    desktop headroom."""
    return max(0, vram - min(int(vram * 0.5), int(1.2 * 1024 ** 3)))


def is_shared_memory_gpu() -> bool:
    """Whether the GPU shares the CPU's RAM (an APU/iGPU): a small VRAM carve-out
    with a much larger GTT pool. A dedicated GPU, where GTT is comparable to or
    smaller than its VRAM, is not flagged."""
    vram, gtt = vram_bytes(), gtt_bytes()
    return bool(vram and gtt and gtt > vram * 2)


def recommended_gpu_layers(model_path: str) -> int | None:
    """'Layers that fit in VRAM' estimate for a *dedicated*-VRAM GPU:
    ``usable_VRAM ÷ per-layer weight size``. ``None`` when VRAM or the layer
    count is unknown. On a shared-memory GPU this number is misleading — see
    :func:`gpu_layers_advice`."""
    from markdown_vault.core import config
    vram = vram_bytes()
    layers = config.gguf_n_layers(model_path)
    if not vram or not layers or not os.path.exists(model_path):
        return None
    per_layer = os.path.getsize(model_path) / layers
    return max(0, min(int(layers), int(_usable_vram(vram) / per_layer)))


def gpu_layers_advice(model_path: str) -> str | None:
    """A correct, hardware-aware GPU-layers recommendation string, or ``None``.

    The key nuance: on a **shared-memory** GPU (an APU, where the CPU and GPU use
    the same RAM) a *partial* offload is slower than either extreme — splitting
    the graph forces the activations to be copied at every CPU↔GPU boundary. So
    there the advice is all-or-nothing, not a layer count. Only a dedicated-VRAM
    GPU that can't hold the whole model benefits from a partial offload."""
    vram = vram_bytes()
    if not vram or not os.path.exists(model_path):
        return None
    gb = vram / 1024 ** 3
    if is_shared_memory_gpu():         # APU: a partial offload hurts (graph splits)
        return (f"Shared-memory GPU ({gb:.1f} GB). Use 999 for the fastest "
                "generation (a large model or prompt can freeze the desktop while "
                "it runs) or 0 to keep the desktop responsive — a partial offload "
                "is slower here (constant CPU↔GPU copying).")
    # Dedicated-VRAM GPU: a partial offload of what doesn't fit is worthwhile.
    if os.path.getsize(model_path) <= _usable_vram(vram):
        return f"Fits in {gb:.1f} GB VRAM — offload all layers (999)."
    layers = recommended_gpu_layers(model_path)
    if layers is not None:
        return (f"Bigger than the {gb:.1f} GB VRAM — about {layers} layers fit; "
                "the rest run on the CPU.")
    return f"Detected VRAM: {gb:.1f} GB."


# KV-cache precision string → ggml type constant name. "f16" (and anything
# unknown) keeps llama.cpp's own default, so it needs no override.
_KV_GGML = {"q8_0": "GGML_TYPE_Q8_0", "q4_0": "GGML_TYPE_Q4_0"}


def kv_needs_flash(type_v: str) -> bool:
    """Whether a V-cache precision *type_v* needs flash attention — any value
    below f16 does; the K cache can be quantized freely."""
    return type_v in _KV_GGML


def _load(key):
    """Load a GGUF into a ``llama_cpp.Llama`` for cache *key*."""
    import llama_cpp
    kwargs = _llama_kwargs(key)
    for attr, kv_str in (("type_k", key[4]), ("type_v", key[5])):
        name = _KV_GGML.get(kv_str)
        if name is not None:
            t = getattr(llama_cpp, name, None)
            if t is not None:
                kwargs[attr] = t
    logger.info("loading local model %s", kwargs)
    return llama_cpp.Llama(**kwargs)


def _llama_kwargs(key) -> dict:
    """The ``Llama(**kwargs)`` for a cache *key* — pure (no llama_cpp import), so
    the thread/flash/offload wiring is testable without loading a model. The
    *key* is ``(path, n_ctx, n_gpu_layers, n_threads, type_k, type_v, flash_attn,
    offload_kqv, use_mmap, n_batch, n_ubatch)``; the KV-cache types are resolved
    to ggml constants in :func:`_load`."""
    (path, n_ctx, n_gpu_layers, n_threads, _tk, _tv, flash_attn,
     offload_kqv, use_mmap, n_batch, n_ubatch) = key
    # verbose=True so llama-cpp-python doesn't install its own suppressing log
    # callback — ours (see _install_llama_logging) then receives the load logs.
    kwargs = dict(model_path=path, n_ctx=n_ctx, n_gpu_layers=n_gpu_layers,
                  verbose=True, flash_attn=bool(flash_attn),
                  offload_kqv=bool(offload_kqv), use_mmap=bool(use_mmap))
    if n_threads:
        # Cap BOTH pools. n_threads governs token generation, but prompt
        # processing (prefill) — the heavy all-core burst that dominates CPU
        # inference — is governed by n_threads_batch, which otherwise defaults
        # to *every* core. Leaving it uncapped is what still spiked all cores
        # (and browned out a machine with no battery to buffer the surge) even
        # after the user lowered the thread count.
        kwargs["n_threads"] = n_threads
        kwargs["n_threads_batch"] = n_threads
    # n_batch is the logical batch (tokens per decode); n_ubatch the physical
    # micro-batch that sizes the prefill compute — the real speed lever. Each is
    # sent only when set, so 0 keeps llama.cpp's own default.
    if n_batch:
        kwargs["n_batch"] = n_batch
    if n_ubatch:
        kwargs["n_ubatch"] = n_ubatch
    return kwargs


def get_model(model_path: str, n_ctx: int, n_gpu_layers: int = 0,
              n_threads: int = 0, type_k: str = "f16", type_v: str = "f16",
              flash_attn: bool = False, offload_kqv: bool = True,
              use_mmap: bool = True, n_batch: int = 0, n_ubatch: int = 0,
              on_load=None):
    """The cached ``Llama`` for these parameters, loading (and evicting the
    previous one) only when the key changes. *on_load* is called right before an
    actual (cache-miss) load, so the UI can show a 'Loading model…' phase only
    when a load really happens."""
    global _MODEL, _MODEL_KEY
    _install_llama_logging()             # capture the load logs into the llama log
    key = (model_path, int(n_ctx), int(n_gpu_layers), int(n_threads),
           str(type_k), str(type_v), bool(flash_attn), bool(offload_kqv),
           bool(use_mmap), int(n_batch), int(n_ubatch))
    with _LOCK:
        if _MODEL_KEY != key:
            if on_load is not None:
                on_load()
            _MODEL = _load(key)          # replaces the old model (frees its RAM)
            _MODEL_KEY = key
        return _MODEL


# A reasoning model puts its chain of thought before a </think> marker (the
# opening <think> can come from the chat template, so we key on the close). The
# grounded answer is whatever follows the final </think>; the rest is hidden from
# the live stream and dropped from the result.
_UNSET = object()
_THINK_CLOSE = "</think>"


def _visible_text(raw: str) -> str:
    idx = raw.rfind(_THINK_CLOSE)
    if idx != -1:
        return raw[idx + len(_THINK_CLOSE):]
    i = raw.find("<think>")           # an explicit block, still streaming → hide
    if i != -1:
        return raw[:i]
    for k in range(len("<think>") - 1, 0, -1):   # a partial "<think" tail arriving
        if raw.endswith("<think>"[:k]):
            return raw[:-k]
    return raw


class LlamaCppChat:
    """A :class:`ask.ChatBackend` over an in-process GGUF model.

    *on_phase* is an optional ``callback(phase)`` the UI hooks for status: it
    fires ``"loading"`` only when the model actually loads and ``"thinking"``
    just before generation. *_model* is an injection seam for tests: pass a stub
    with a ``create_chat_completion`` method and no real model is loaded.
    """

    def __init__(self, model_path: str, num_ctx: int = 8192,
                 n_gpu_layers: int = 0, n_threads: int = 0,
                 temperature: float = 0.2, type_k: str = "f16",
                 type_v: str = "f16", flash_attn: bool = False,
                 offload_kqv: bool = True, use_mmap: bool = True,
                 n_batch: int = 0, n_ubatch: int = 0,
                 max_tokens: int = 1024, repeat_penalty: float = 1.1,
                 think: bool | None = None,
                 on_phase=None, on_token=None, should_cancel=None,
                 _model=None) -> None:
        self.model_path = model_path
        self.num_ctx = num_ctx
        self.think = think
        self.n_gpu_layers = n_gpu_layers
        self.n_threads = n_threads
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.repeat_penalty = repeat_penalty
        self.type_k = type_k
        self.type_v = type_v
        self.flash_attn = flash_attn
        self.offload_kqv = offload_kqv
        self.use_mmap = use_mmap
        self.n_batch = n_batch
        self.n_ubatch = n_ubatch
        self._on_phase = on_phase
        self._on_token = on_token
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
                         self.n_threads, type_k=self.type_k, type_v=self.type_v,
                         flash_attn=self.flash_attn, offload_kqv=self.offload_kqv,
                         use_mmap=self.use_mmap, n_batch=self.n_batch,
                         n_ubatch=self.n_ubatch,
                         on_load=lambda: self._phase("loading"))

    def _reasoning_formatter(self, llama):
        """A cached ``Jinja2ChatFormatter`` for models whose own chat template
        understands the ``enable_thinking`` toggle (reasoning models), else None.

        ``create_chat_completion`` can't forward template variables, so to steer a
        reasoning model we render its template ourselves and feed the raw prompt to
        ``create_completion`` — the same ``enable_thinking`` switch the llama.cpp
        server exposes, done in-process. Non-reasoning models have no such variable
        and stay on the proven ``create_chat_completion`` path untouched."""
        cached = getattr(llama, "_mv_rformatter", _UNSET)
        if cached is not _UNSET:
            return cached
        formatter = None
        try:
            template = (getattr(llama, "metadata", None) or {}).get(
                "tokenizer.chat_template")
            if template and "enable_thinking" in template:
                from llama_cpp import llama_chat_format
                eos = llama.detokenize([llama.token_eos()]).decode("utf-8", "replace")
                bos = llama.detokenize([llama.token_bos()]).decode("utf-8", "replace")
                formatter = llama_chat_format.Jinja2ChatFormatter(
                    template=template, eos_token=eos, bos_token=bos,
                    add_generation_prompt=True)
        except ImportError:        # llama_cpp not installed — expected on base
            logger.debug("llama_cpp not installed; reasoning toggle unavailable")
        except Exception:
            logger.warning("could not build a reasoning chat formatter; the "
                           "thinking toggle is unavailable for this model",
                           exc_info=True)
        llama._mv_rformatter = formatter
        return formatter

    def _stream_text(self, llama, system, user):
        """Yield the answer's text pieces, recording ``choices[0].finish_reason``
        of the final chunk in ``self._finish_reason`` ('length' when the model hit
        max_tokens). For a reasoning model with an explicit preference, render the
        template with ``enable_thinking`` and stream ``create_completion``;
        otherwise stream ``create_chat_completion``."""
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": user}]
        gen = dict(temperature=self.temperature, max_tokens=self.max_tokens,
                   repeat_penalty=self.repeat_penalty, stream=True)
        formatter = (self._reasoning_formatter(llama)
                     if self.think is not None else None)
        prompt = None
        if formatter is not None:
            try:
                prompt = formatter(messages=messages,
                                   enable_thinking=bool(self.think)).prompt
            except Exception:
                logger.warning("chat template render failed; using "
                               "create_chat_completion", exc_info=True)
        if prompt is not None:
            for chunk in llama.create_completion(prompt, **gen):
                ch = (chunk.get("choices") or [{}])[0]
                self._finish_reason = ch.get("finish_reason") or self._finish_reason
                yield ch.get("text") or ""
        else:
            for chunk in llama.create_chat_completion(messages=messages, **gen):
                ch = (chunk.get("choices") or [{}])[0]
                self._finish_reason = ch.get("finish_reason") or self._finish_reason
                yield (ch.get("delta") or {}).get("content") or ""

    def chat(self, system: str, user: str) -> str:
        # Hold the lock for the whole decode so two in-flight questions never share
        # the one cached Llama context. A superseded worker's should_cancel already
        # reads True, so its abort callback breaks it out (prefill included) and it
        # releases the lock promptly — the next worker then acquires it.
        with _CHAT_LOCK:
            if self._cancelled():          # superseded while we waited for the lock
                return ""
            llama = self._llama()          # may fire "loading" on a cache miss
            # Arm the abort callback INSIDE the lock, so the single global slot is
            # never clobbered by another worker: closing the palette / asking again
            # interrupts the running decode, prompt processing included.
            _install_abort(llama)
            _ABORT_HOLDER["fn"] = self._should_cancel
            # A template that prefills an OPEN <think> at the generation prompt
            # streams the chain of thought with no opening tag in the output, so
            # _visible_text can't spot it. Suppress the live stream until the first
            # </think> so the user never watches the private reasoning (including
            # values it is mid-way through rejecting). Key on the TEMPLATE, not on
            # `think`: DeepSeek-R1 distills mention <think> but have no
            # enable_thinking switch — that's the leaking set, and it fires on the
            # production path (think=None). Qwen3-style templates gate thinking with
            # enable_thinking, emit their own opening tag (already handled), and
            # render an empty <think></think> when reasoning is off (which must NOT
            # be hidden), so exclude them; the think=True clause still covers
            # explicitly opening one via the formatter.
            template = (getattr(llama, "metadata", None) or {}).get(
                "tokenizer.chat_template") or ""
            hide_until_close = (
                ("<think>" in template and "enable_thinking" not in template)
                or (self.think is True
                    and self._reasoning_formatter(llama) is not None))
            # Stream: the first iteration blocks on the prompt (prefill = "reading",
            # nothing visible yet), then tokens flow. on_token gets the FULL visible
            # answer whenever it changes and the consumer REPLACES its label — the
            # visible text can shrink or shift its prefix when a </think> boundary is
            # crossed, which a character delta would splice into garbage. A <think>
            # block is hidden from the stream and stripped from the result.
            self._phase("reading")
            raw: list = []
            last = ""
            self._finish_reason = None
            try:
                for piece in self._stream_text(llama, system, user):
                    if self._cancelled():
                        break              # closed / new question — stop generating
                    if not piece:
                        continue
                    raw.append(piece)
                    joined = "".join(raw)
                    if hide_until_close and _THINK_CLOSE not in joined:
                        continue           # still inside the prefilled think block
                    vis = _visible_text(joined)
                    if vis != last and self._on_token is not None:
                        self._on_token(vis)
                        last = vis
            except Exception:
                if self._cancelled():
                    return ""          # intentionally aborted; the result is discarded
                raise
            finally:
                _ABORT_HOLDER["fn"] = None
        if self._cancelled():
            return ""
        full = "".join(raw)
        visible = _visible_text(full).strip()
        # Ran out of tokens (finish_reason 'length') with no usable answer — the
        # only text is an unfinished chain of thought. Two shapes, both covered: a
        # PREFILLED <think> leaves the raw reasoning as the full text (non-empty, so
        # key on the missing close tag), while a SELF-tagged <think> (Qwen3) leaves
        # _visible_text empty (its open block is hidden). finish_reason is the exact
        # stop signal, so a short but complete answer is never misread. Signal
        # exhaustion rather than shipping reasoning or an empty answer.
        if self._finish_reason == "length" and (
                (hide_until_close and _THINK_CLOSE not in full) or not visible):
            raise ReasoningBudgetExhausted
        return visible
