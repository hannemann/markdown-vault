"""Answer questions from the user's own notes (local RAG).

Retrieval is done by the semantic index; this module only turns the retrieved
passages plus a question into a *grounded* prompt and asks a local Ollama chat
model to write the answer.  The model is instructed to answer ONLY from the
provided excerpts, so it stays within the user's knowledge base and cites its
sources — it does not draw on outside/training knowledge.
"""

import json
import logging
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Protocol

logger = logging.getLogger(__name__)


class ChatBackend(Protocol):
    """Anything that turns a (system, user) prompt into a reply — OllamaChat or
    OpenAIChat both satisfy this structurally."""

    def chat(self, system: str, user: str) -> str: ...


@dataclass
class Source:
    """A note passage handed to the model, numbered for citation.

    *text* is the excerpt itself, kept so citation verification can check the
    answer's numbers against what the excerpt actually contains.
    """

    n: int
    path: str
    line: int
    text: str = ""


@dataclass
class Answer:
    text: str
    sources: list = field(default_factory=list)      # the excerpts the answer cited
    considered: list = field(default_factory=list)   # retrieved but not cited
    error: str | None = None
    warnings: list = field(default_factory=list)


# The rules are in English (a neutral instruction language the model follows
# regardless of output language); {language} is the user's OS language, which the
# answer must be written in — not the language these rules or the notes are in.
_SYSTEM = (
    "You answer the user's question using ONLY the provided note excerpts — never "
    "from outside or general world knowledge. Rules:\n"
    "- The excerpts ARE your knowledge base. If the answer is stated in an excerpt, "
    "or can be directly read or derived from the excerpts, give it directly and "
    "concisely. Do NOT hedge with 'no explicit information' when the fact is present, "
    "even if phrased differently than the question — extract it and answer.\n"
    "- Cite the excerpt you used by number, e.g. [1], [2].\n"
    "- You MAY compare, rank, count and combine values across several excerpts to "
    "reach the answer — pick the extreme (largest, smallest, most, fewest) among "
    "the excerpted values.\n"
    "- BUT if the answer is NOT contained in any excerpt, you MUST say plainly that "
    "the notes do not contain it. Never guess, never fill the gap from outside or "
    "general knowledge, never invent a name, number, date or fact. A made-up answer "
    "is worse than admitting the notes don't say.\n"
    "- Do not read metaphors or comparisons as facts.\n"
    "- Attribute every value — a number, name, date or property — to the exact "
    "entity and category the excerpt states it for. Never carry a value over from "
    "a neighbouring item, or from a broader or narrower category (a whole vs. a "
    "subgroup), to the thing that was asked.\n"
    "- Answer concisely, and write the answer in {language}."
)

#: Public alias — the built-in prompt the Preferences editor resets to.
DEFAULT_SYSTEM_PROMPT = _SYSTEM


def build_messages(question: str, hits, language: str = "English",
                   system_template: str | None = None) -> tuple[str, str, list]:
    """Build ``(system, user, sources)`` from a question and retrieved hits.

    *hits* is a list of ``(chunk, score)`` where *chunk* has ``path``, ``line``
    and ``text`` (the semantic index's :class:`Chunk`).  *system_template* is the
    (user-configurable) system prompt; its ``{language}`` placeholder is filled
    with *language* — via ``str.replace`` so a custom prompt with stray ``{}``
    can't crash the call.
    """
    sources: list = []
    blocks: list = []
    for i, (chunk, _score) in enumerate(hits, start=1):
        name = os.path.basename(chunk.path)
        sources.append(Source(n=i, path=chunk.path, line=chunk.line, text=chunk.text))
        blocks.append(f"[{i}] {name} (line {chunk.line}):\n{chunk.text.strip()}")
    context = "\n\n".join(blocks) if blocks else "(no excerpts)"
    system = (system_template or _SYSTEM).replace("{language}", language)
    user = (
        f"Question: {question}\n\n"
        f"Note excerpts:\n{context}\n\n"
        "Answer the question using only these excerpts."
    )
    return system, user, sources


# Citation markers the model writes, e.g. "[1]", "[1, 2]".  One group may hold
# several comma-separated numbers.
_CITE_RE = re.compile(r"\[\s*(\d+(?:\s*,\s*\d+)*)\s*\]")
# A number token: digits with interior grouping/decimal separators.
_NUM_RE = re.compile(r"\d[\d.,]*\d|\d")
# A value *attributed* to a source: a number directly before the citation on the
# SAME line, with only unit-ish words between (e.g. "318 Earth masses [1]", "95
# moons [1]").  The gap allows spaces/tabs but NOT newlines, and a clause
# boundary (comma) breaks the class — so a list item like "- Jupiter: 95" is not
# bridged across a paragraph to a later "Aus [4]", and a synthesised "5 planets
# in total, see [1]" is not treated as attributed either.
_ATTRIB_RE = re.compile(r"(\d[\d.,]*\d|\d)[A-Za-zÀ-ÿ%°/·×\- \t]{0,15}$")
# How far back from a citation we look for that attributed number.
_ATTRIB_WINDOW = 40


def _norm_num(tok: str) -> str:
    """Digits only, so "1.898" / "1,898" / "1898" compare equal."""
    return re.sub(r"[.,\s]", "", tok)


def _excerpt_numbers(text: str) -> set:
    return {n for n in (_norm_num(m) for m in _NUM_RE.findall(text)) if n}


def _cited_nums(group: str) -> list:
    return [int(x) for x in re.split(r"\s*,\s*", group.strip())]


def verify_citations(text: str, sources: list) -> tuple[str, list, list]:
    """Check an answer's citations against the excerpts it was given.

    Returns ``(cleaned_text, cited_sources, warnings)``:

    * **Stufe 1 (referential integrity):** citation markers pointing outside
      ``1..N`` are *invented sources* — they are stripped from the text and
      reported.  The returned sources are only those the answer actually cited
      (falling back to all excerpts when it cited none, so a forgetful model
      never yields an empty source list).
    * **Stufe 2a (numeric grounding):** a number the answer attributes directly
      to a source — the pattern ``"<value> … [n]"`` — must occur in excerpt
      ``[n]``.  If it does not, that is flagged (advisory).  Numbers *not*
      attributed to a specific source (synthesised counts/sums) are left alone,
      so cross-excerpt derivation is not penalised.
    """
    n = len(sources)
    by_n = {s.n: s for s in sources}
    warnings: list = []

    # --- Stufe 1 -------------------------------------------------------
    all_cited = [c for m in _CITE_RE.finditer(text) for c in _cited_nums(m.group(1))]
    invalid = sorted({c for c in all_cited if c not in by_n})
    valid = sorted({c for c in all_cited if c in by_n})

    def _prune(m):
        keep = [c for c in _cited_nums(m.group(1)) if c in by_n]
        return "[" + ", ".join(map(str, keep)) + "]" if keep else ""

    cleaned = _CITE_RE.sub(_prune, text)
    cleaned = re.sub(r"\s+([.,;:!?])", r"\1", cleaned)      # tidy " ." left behind
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned).strip()

    if invalid:
        marks = ", ".join(f"[{c}]" for c in invalid)
        excerpts = f"{n} excerpt{'' if n == 1 else 's'}"
        warnings.append(
            f"Removed invented citation {marks} — the answer had only "
            f"{excerpts} to cite.")

    cited_sources = ([by_n[c] for c in valid if c in by_n] if valid
                     else list(sources))

    # --- Stufe 2a ------------------------------------------------------
    for m in _CITE_RE.finditer(cleaned):
        ns = [c for c in _cited_nums(m.group(1)) if c in by_n]
        if not ns:
            continue
        before = cleaned[max(0, m.start() - _ATTRIB_WINDOW):m.start()]
        am = _ATTRIB_RE.search(before)
        if am is None:                 # no number attributed to this citation
            continue
        val = am.group(1)
        norm = _norm_num(val)
        if norm and not any(norm in _excerpt_numbers(by_n[c].text) for c in ns):
            marks = ", ".join(f"[{c}]" for c in ns)
            warnings.append(
                f"Value {val} is attributed to {marks} but isn't in that "
                f"excerpt — verify.")

    return cleaned, cited_sources, list(dict.fromkeys(warnings))


# A reasoning model (Qwen3, DeepSeek-R1, …) emits a <think>…</think> block that
# we never want in the grounded answer — strip it defensively regardless of the
# backend's own filtering.
_THINK_RE = re.compile(r"<think>.*?</think>", re.S)


class OllamaChat:
    """Chat completion via a running Ollama server (``POST /api/chat``).

    No extra Python dependency; with a localhost URL nothing leaves the machine.
    *think* toggles a reasoning model's thinking via Ollama's ``think`` field:
    ``None`` uses the model default, ``False`` disables it. Ollama keeps any
    reasoning out of ``message.content`` (it goes to ``message.thinking``, which
    we ignore); the field is harmless for models without a thinking mode.
    """

    #: Endpoint identity, so a failed request can be classified the same way the
    #: model list is (see :func:`_explain_chat_error`).
    backend_name = "ollama"

    #: Context window requested from Ollama.  Its default (num_ctx=2048) silently
    #: truncates the prompt, and note-level retrieval (whole notes) routinely
    #: exceeds that — a truncated prompt makes the model refuse or drop its
    #: citations.  8192 covers the typical multi-note context on a GPU host.
    DEFAULT_NUM_CTX = 8192

    def __init__(self, model: str, url: str = "http://localhost:11434",
                 timeout: float = 120.0, think: bool | None = None,
                 num_ctx: int = DEFAULT_NUM_CTX, api_key: str = "") -> None:
        self.model = model
        self.url = url.rstrip("/")
        self.timeout = timeout
        self.think = think
        self.num_ctx = num_ctx
        self.api_key = api_key

    def chat(self, system: str, user: str) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "options": {"temperature": 0.2, "num_ctx": self.num_ctx},
        }
        if self.think is not None:   # Ollama's reasoning toggle; None = model default
            payload["think"] = self.think
        body = json.dumps(payload).encode()
        headers = {"Content-Type": "application/json"}
        if self.api_key:             # for an Ollama put behind an auth proxy
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(
            self.url + "/api/chat", data=body, headers=headers,
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read())
        text = ((data.get("message") or {}).get("content") or "")
        return _THINK_RE.sub("", text).strip()


def openai_base(url: str) -> str:
    """Normalise an OpenAI-compatible base URL: drop a trailing slash and a
    trailing ``/v1``, because callers append ``/v1/chat/completions`` and
    ``/v1/models`` themselves. So ``https://host/v1`` and ``https://host`` both
    work and no one produces ``…/v1/v1/…`` (404)."""
    url = (url or "").rstrip("/")
    if url.endswith("/v1"):
        url = url[:-3].rstrip("/")
    return url


class OpenAIChat:
    """Chat completion via an OpenAI-compatible server (llama.cpp, vLLM, …):
    ``POST /v1/chat/completions``. *url* is the server base (e.g.
    ``http://host:8080``). *think=False* disables a reasoning model's thinking
    via ``chat_template_kwargs`` (llama.cpp / Qwen3)."""

    #: Endpoint identity — see :func:`_explain_chat_error`.
    backend_name = "openai"

    def __init__(self, model: str, url: str = "http://localhost:8080",
                 timeout: float = 120.0, think: bool | None = None,
                 api_key: str = "") -> None:
        self.model = model
        self.url = openai_base(url)
        self.timeout = timeout
        self.think = think
        self.api_key = api_key

    def chat(self, system: str, user: str) -> str:
        payload = {
            "model": self.model or "default",  # llama.cpp ignores it; keep valid
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "temperature": 0.2,
        }
        if self.think is False:
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        body = json.dumps(payload).encode()
        headers = {"Content-Type": "application/json"}
        if self.api_key:                       # auth only when a key is configured
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(
            self.url + "/v1/chat/completions", data=body, headers=headers,
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read())
        text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        return _THINK_RE.sub("", text).strip()


# Context budget --------------------------------------------------------------
# A rough chars-per-token for German/English. The budget is a safety net against
# prompt truncation, not a tokenizer, so an approximation is fine.
_CHARS_PER_TOKEN = 3.5
# Tokens held back from the context window for the system prompt, the question
# and the answer the model still has to generate.
_ANSWER_RESERVE_TOKENS = 1024


def context_char_budget(num_ctx: int) -> int:
    """Characters of note excerpts that fit *num_ctx* tokens, leaving room for
    the system prompt, question and the generated answer."""
    return max(0, int((num_ctx - _ANSWER_RESERVE_TOKENS) * _CHARS_PER_TOKEN))


# A boundary note kept only as a head must still be substantial enough to be a
# useful, citable source — a 10-character sliver is noise, so drop it instead.
_MIN_BOUNDARY_CHARS = 200


def fit_to_budget(hits, budget: int):
    """Fit ranked *hits* into a *budget* of characters without truncating the
    prompt — and without corrupting a note.

    Notes are kept whole, best-first, until the budget is spent; the note that
    straddles the boundary keeps its *head* (if at least ``_MIN_BOUNDARY_CHARS``
    of it fit — a shorter sliver is dropped), and any lower-ranked notes past it
    are dropped.  This deliberately favours a few intact notes over many
    fragments: slicing each note down to an equal floor threw away both the
    note's head and its matched passage (a mid-note slice is incoherent context
    for the model).  When everything already fits (the common case for
    normal-sized notes) the hits are returned unchanged.
    """
    if not hits or budget <= 0:
        return hits
    if sum(len(c.text) for c, _ in hits) <= budget:
        return hits
    out = []
    used = 0
    for c, sc in hits:
        room = budget - used
        if room <= 0:
            break
        if len(c.text) <= room:
            out.append((c, sc))
            used += len(c.text)
        elif room >= _MIN_BOUNDARY_CHARS:        # boundary note: keep its head
            out.append((SimpleNamespace(path=c.path, line=c.line,
                                        text=c.text[:room]), sc))
            break
        else:                                    # too little room left — drop it
            break
    return out


# Where the context-window control lives — the Ask subpage sits under Search.
_CTX_WINDOW_PATH = "Preferences → Search → Ask → Context window"


def budget_warning(kept: int, retrieved: int) -> list:
    """The user-facing note when the context budget dropped notes, so the trim
    is visible instead of silent (Answer.warnings renders it)."""
    dropped = retrieved - kept
    if dropped <= 0:
        return []
    tail = f"To fit more, raise the context window ({_CTX_WINDOW_PATH})."
    if kept == 0:
        return [f"None of the {retrieved} matching notes fit the model's "
                f"context window — they are larger than it allows. {tail}"]
    return [f"{kept} of {retrieved} matching notes fit the model's context "
            f"window; the {dropped} least-relevant did not. {tail}"]


def deprecated_dropped_warning(dropped: int) -> list:
    """The user-facing note when the "hide deprecated" filter removed some (but not
    all) retrieved notes from the answer's context, so the exclusion is visible
    instead of silent (Answer.warnings renders it)."""
    if dropped <= 0:
        return []
    noun = "note" if dropped == 1 else "notes"
    return [f"{dropped} deprecated {noun} matched but were excluded by the "
            "“hide deprecated” filter. Turn it off to include them."]


def _no_context_answer() -> Answer:
    """The reply when the budget fits no note at all — there is nothing to ground
    on, so we don't spend a backend round-trip on an empty "(no excerpts)" prompt
    (which would only invite the model to invent). The text carries the
    explanation, so it is not also duplicated as a warning banner."""
    return Answer(
        text="I found matching notes, but none fit the model's context window, "
             "so there's nothing I can ground an answer on. Raise the context "
             f"window in {_CTX_WINDOW_PATH}.")


def _explain_chat_error(chat, exc: Exception) -> str:
    """Turn a failed chat request into a sentence.

    A server backend carries its identity (``backend_name`` + ``url``), so the same
    classification as the model list applies and the wording matches the palette's
    banner. The in-process backend has no endpoint to blame, so it gets the plain
    reason — still a sentence, not a raw repr.
    """
    backend = getattr(chat, "backend_name", "")
    url = getattr(chat, "url", "")
    if backend and url:
        from markdown_vault.search import ask_models   # local: ask_models imports us
        # Records the verdict as well as wording it: a server that died while the
        # palette was open must not keep its earlier "all good" status.
        return ask_models.note_chat_failure(backend, url, exc)
    return f"The model could not answer: {exc}"


def answer(question: str, hits, chat: ChatBackend, language: str = "English",
           system_template: str | None = None, char_budget: int | None = None,
           extra_warnings: list | None = None) -> Answer:
    """Retrieve-augmented generation: ground *question* in *hits* and ask *chat*.

    *language* is the language the answer must be written in; *system_template*
    the (user-configurable) system prompt.  *char_budget*, when given, caps the
    excerpt characters (see :func:`fit_to_budget`) so the prompt fits the
    backend's context window instead of being silently truncated — and a warning
    reports any notes the cap dropped.  *extra_warnings* are prepended to the
    result, so a caller that already applied the budget (and logged it) can pass
    the note through instead of having it re-computed here.
    """
    if not hits:
        return Answer(text="I couldn't find anything about that in your notes.")
    extra = list(extra_warnings or [])
    if char_budget:
        fitted = fit_to_budget(hits, char_budget)
        extra += budget_warning(len(fitted), len(hits))
        hits = fitted
        if not hits:
            return _no_context_answer()
    system, user, sources = build_messages(question, hits, language, system_template)
    from markdown_vault.search import llama_runtime
    try:
        text = chat.chat(system, user)
    except llama_runtime.ReasoningBudgetExhausted:
        # The model spent its whole budget thinking; its raw chain of thought must
        # not be shipped as the answer. Explain, and keep the retrieved notes
        # visible (dimmed) so the user can still read them directly.
        return Answer(
            text="The model used its entire answer budget thinking and never "
                 "produced an answer. Raise “Max answer length”, or turn "
                 "Reasoning off.",
            considered=sources, warnings=extra)
    except (OSError, ValueError) as exc:  # URLError is an OSError subclass
        logger.warning("chat request failed: %s", exc)
        return Answer(text="", sources=sources, error=_explain_chat_error(chat, exc))
    cleaned, cited, warnings = verify_citations(text, sources)
    cited_ns = {s.n for s in cited}
    considered = [s for s in sources if s.n not in cited_ns]
    return Answer(text=cleaned, sources=cited, considered=considered,
                  warnings=extra + warnings)


def answer_question(question: str, semantic_index, settings: dict, vaults,
                    language: str, top_k: int | None = None,
                    note_paths=None, on_phase=None, on_token=None,
                    should_cancel=None) -> Answer:
    """The full Ask pipeline: retrieve the top passages for *question* from
    *semantic_index* (scoped to *vaults*), build the configured chat backend,
    and let it write a grounded, citation-verified answer.

    This is the single source of the retrieval + backend + context-budget
    wiring, so every caller exercises identical logic instead of restating it.
    *top_k* defaults to the user's ``ask_top_k`` setting; pass it explicitly only
    to override (e.g. an eval sweep).  *note_paths*, when given, skips retrieval
    and uses exactly those notes as context (the user picked them).
    """
    from markdown_vault.markdown import frontmatter
    from markdown_vault.core import config  # local import keeps ask import-light
    if semantic_index is None:
        return Answer(text="Semantic search is not active — without an index I "
                           "can't search your notes.")
    dep_note: list = []
    if note_paths:
        hits = semantic_index.note_hits(note_paths)
    else:
        if top_k is None:
            top_k = int(config.get_setting(settings, "ask.top_k")
                        or config.default("ask.top_k"))
        hits = semantic_index.retrieve(question, top_k=top_k, vaults=vaults,
                                       hybrid=bool(config.get_setting(settings, "ask.hybrid")))
        if config.get_setting(settings, "hide_deprecated"):
            # The shared "hide deprecated" filter applies to RAG retrieval too —
            # drop deprecated notes from the context. An explicit user filter, not
            # the automatic down-ranking we deliberately avoid.
            kept = [(c, s) for c, s in hits
                    if frontmatter.status_of(c.path) != "deprecated"]
            if hits and not kept:
                # Graceful fallback: everything that matched is deprecated. Rather
                # than a bare "nothing found", say so and offer the excluded notes
                # (dimmed) — turning the filter off is how to actually use them.
                excluded = [Source(n=i, path=c.path, line=c.line, text=c.text)
                            for i, (c, _s) in enumerate(hits, start=1)]
                return Answer(
                    text="Only deprecated notes match your question — they're "
                         "excluded by the “hide deprecated” filter. Turn it off to "
                         "use them.",
                    considered=excluded)
            # Partial drop: some notes were excluded — say so instead of silently
            # answering from a thinner context.
            dep_note = deprecated_dropped_warning(len(hits) - len(kept))
            hits = kept
    # Only override thinking when the user turned reasoning OFF, so non-reasoning
    # models (and Ollama, which errors on an unknown "think") keep their default.
    think = False if not config.get_setting(settings, "ask.reasoning", True) else None
    num_ctx = int(config.get_setting(settings, "ask.num_ctx")
                  or config.default("ask.num_ctx"))
    # Answer engine: "auto" configures everything (always the in-process backend,
    # GPU offload when the build supports it, a safe thread count); "manual" honours
    # the advanced backend/threads/GPU settings; "off" produces no answers.
    engine = config.get_setting(settings, "ask.engine") or config.default("ask.engine")
    if engine == "off":
        return Answer(text="Answers are turned off. Turn the answer engine on in "
                           "Preferences → Search → Ask.")
    from markdown_vault.search import ask_models   # local: ask_models imports us
    backend = ask_models.effective_backend(settings)
    # Build the chat backend and decide whether we cap the context ourselves. We
    # size the window for the in-process and Ollama backends (so the prompt fits
    # instead of being silently truncated); the OpenAI-compatible server sizes
    # its own context, so no client budget there.
    if backend == "local":
        from markdown_vault.search import llama_runtime
        gguf = config.resolve_model_path(settings)
        unavailable = llama_runtime.availability(gguf)
        if unavailable:                       # no binding or no model file yet
            return Answer(text=unavailable)
        if engine == "auto":
            # Automatic owns the whole runtime: GPU offload when the build can,
            # else pure CPU, and llama.cpp's own KV / flash / batch defaults. It
            # must NOT inherit any hidden Manual-page knob — a leftover (V=q4_0
            # with flash off that won't load, max_tokens=128 that truncates, or
            # num_ctx at its 2048 minimum that shrinks the context) would break or
            # silently degrade every answer with no visible control.
            n_gpu_layers = 999 if llama_runtime.supports_gpu() else 0
            type_k = type_v = "f16"
            flash_attn = False
            n_batch = n_ubatch = 0
            use_mmap = True
            num_ctx = int(config.default("ask.num_ctx"))
            max_tokens = int(config.default("ask.max_tokens"))
        else:
            n_gpu_layers = int(config.get_setting(settings, "ask.local.n_gpu_layers") or 0)
            type_k = config.get_setting(settings, "ask.local.kv_type_k") or "f16"
            type_v = config.get_setting(settings, "ask.local.kv_type_v") or "f16"
            flash_attn = bool(config.get_setting(settings, "ask.local.flash_attn"))
            n_batch = int(config.get_setting(settings, "ask.local.n_batch") or 0)
            n_ubatch = int(config.get_setting(settings, "ask.local.n_ubatch") or 0)
            use_mmap = bool(config.get_setting(settings, "ask.local.use_mmap", True))
            max_tokens = int(config.get_setting(settings, "ask.max_tokens") or 1024)
        # 0 threads → the safe default (half the physical cores), in both modes.
        n_threads = (int(config.get_setting(settings, "ask.local.n_threads") or 0)
                     or llama_runtime.default_threads())
        chat = llama_runtime.LlamaCppChat(
            gguf, num_ctx=num_ctx, n_gpu_layers=n_gpu_layers, n_threads=n_threads,
            type_k=type_k, type_v=type_v, flash_attn=flash_attn, use_mmap=use_mmap,
            n_batch=n_batch, n_ubatch=n_ubatch, max_tokens=max_tokens, think=think,
            on_phase=on_phase, on_token=on_token, should_cancel=should_cancel)
        char_budget = context_char_budget(num_ctx)
    elif backend == "openai":
        # No fallback to the default model name here: that default belongs to
        # Ollama, and sending it to an OpenAI-compatible server would be a model
        # it does not have. Empty means "the server's own default".
        chat = OpenAIChat(model=config.get_setting(settings, "ask.server.model") or "",
                          url=config.get_setting(settings, "ask.server.url")
                          or config.default("ask.server.url"),
                          think=think, api_key=ask_models.api_key(settings))
        char_budget = None
    else:  # ollama
        chat = OllamaChat(model=config.get_setting(settings, "ask.server.model")
                          or config.default("ask.server.model"),
                          url=config.get_setting(settings, "ask.server.url")
                          or config.default("ask.server.url"),
                          think=think, num_ctx=num_ctx,
                          api_key=ask_models.api_key(settings))
        char_budget = context_char_budget(num_ctx)
    # Apply the budget once here (not again in answer()): the log and the warning
    # both need the post-budget set, so fit, then hand the fitted hits down.
    retrieved = len(hits)
    budget_note: list = []
    if char_budget:
        hits = fit_to_budget(hits, char_budget)
        budget_note = budget_warning(len(hits), retrieved)
    logger.info(
        "ask %r -> %d/%d passages (%s, context budget): %s",
        question, len(hits), retrieved, backend,
        [("/".join(c.path.rsplit("/", 2)[-2:]), round(s, 3)) for c, s in hits])
    if not hits:                              # budget fit nothing — don't call out
        return _no_context_answer()
    if on_phase is not None and backend != "local":
        on_phase("thinking")                  # local fires its own load/think phases
    return answer(question, hits, chat, language=language,
                  system_template=config.get_setting(settings, "ask.system_prompt") or None,
                  extra_warnings=dep_note + budget_note)
