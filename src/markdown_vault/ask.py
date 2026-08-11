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
    sources: list = field(default_factory=list)
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
    *think* toggles a reasoning model's thinking: leave ``None`` for the model's
    default (safe for models that don't support it), or ``False`` to disable it.
    """

    #: Context window requested from Ollama.  Its default (num_ctx=2048) silently
    #: truncates the prompt, and note-level retrieval (whole notes) routinely
    #: exceeds that — a truncated prompt makes the model refuse or drop its
    #: citations.  8192 covers the typical multi-note context on a GPU host.
    DEFAULT_NUM_CTX = 8192

    def __init__(self, model: str, url: str = "http://localhost:11434",
                 timeout: float = 120.0, think: bool | None = None,
                 num_ctx: int = DEFAULT_NUM_CTX) -> None:
        self.model = model
        self.url = url.rstrip("/")
        self.timeout = timeout
        self.think = think
        self.num_ctx = num_ctx

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
        if self.think is not None:   # only send when overriding, else model default
            payload["think"] = self.think
        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            self.url + "/api/chat", data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read())
        text = ((data.get("message") or {}).get("content") or "")
        return _THINK_RE.sub("", text).strip()


class OpenAIChat:
    """Chat completion via an OpenAI-compatible server (llama.cpp, vLLM, …):
    ``POST /v1/chat/completions``. *url* is the server base (e.g.
    ``http://host:8080``). *think=False* disables a reasoning model's thinking
    via ``chat_template_kwargs`` (llama.cpp / Qwen3)."""

    def __init__(self, model: str, url: str = "http://localhost:8080",
                 timeout: float = 120.0, think: bool | None = None) -> None:
        self.model = model
        self.url = url.rstrip("/")
        self.timeout = timeout
        self.think = think

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
        req = urllib.request.Request(
            self.url + "/v1/chat/completions", data=body,
            headers={"Content-Type": "application/json"},
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


def fit_to_budget(hits, budget: int):
    """Fit ranked *hits* into a *budget* of characters without truncating the
    prompt — and without corrupting a note.

    Notes are kept whole, best-first, until the budget is spent; the note that
    straddles the boundary keeps its *head*, and any lower-ranked notes past it
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
        else:                                    # boundary note: keep its head
            out.append((SimpleNamespace(path=c.path, line=c.line,
                                        text=c.text[:room]), sc))
            break
    return out


def answer(question: str, hits, chat: ChatBackend, language: str = "English",
           system_template: str | None = None, char_budget: int | None = None) -> Answer:
    """Retrieve-augmented generation: ground *question* in *hits* and ask *chat*.

    *language* is the language the answer must be written in; *system_template*
    the (user-configurable) system prompt.  *char_budget*, when given, caps the
    excerpt characters (see :func:`fit_to_budget`) so the prompt fits the
    backend's context window instead of being silently truncated.
    """
    if not hits:
        return Answer(text="I couldn't find anything about that in your notes.")
    if char_budget:
        hits = fit_to_budget(hits, char_budget)
    system, user, sources = build_messages(question, hits, language, system_template)
    try:
        text = chat.chat(system, user)
    except (OSError, ValueError) as exc:  # URLError is an OSError subclass
        logger.warning("ollama chat failed: %s", exc)
        return Answer(text="", sources=sources, error=str(exc))
    cleaned, cited, warnings = verify_citations(text, sources)
    return Answer(text=cleaned, sources=cited, warnings=warnings)


def answer_question(question: str, semantic_index, settings: dict, vaults,
                    language: str, top_k: int | None = None,
                    note_paths=None) -> Answer:
    """The full Ask pipeline: retrieve the top passages for *question* from
    *semantic_index* (scoped to *vaults*), build the configured chat backend,
    and let it write a grounded, citation-verified answer.

    This is the single source of the retrieval + backend + context-budget
    wiring, so every caller exercises identical logic instead of restating it.
    *top_k* defaults to the user's ``ask_top_k`` setting; pass it explicitly only
    to override (e.g. an eval sweep).  *note_paths*, when given, skips retrieval
    and uses exactly those notes as context (the user picked them).
    """
    from . import config  # local import keeps ask import-light for tests
    if semantic_index is None:
        return Answer(text="Semantic search is not active — without an index I "
                           "can't search your notes.")
    if note_paths:
        hits = semantic_index.note_hits(note_paths)
    else:
        if top_k is None:
            top_k = int(settings.get("ask_top_k") or config.default("ask_top_k"))
        hits = semantic_index.retrieve(question, top_k=top_k, vaults=vaults,
                                       hybrid=bool(settings.get("ask_hybrid")))
    logger.info(
        "ask %r -> %d passages: %s", question, len(hits),
        [("/".join(c.path.rsplit("/", 2)[-2:]), round(s, 3)) for c, s in hits])
    model = settings.get("ask_model") or config.default("ask_model")
    url = settings.get("ask_ollama_url") or config.default("ask_ollama_url")
    # Only override thinking when the user turned reasoning OFF, so non-reasoning
    # models (and Ollama, which errors on an unknown "think") keep their default.
    think = False if not settings.get("ask_reasoning", True) else None
    cls = OpenAIChat if settings.get("ask_backend") == "openai" else OllamaChat
    kwargs = dict(model=model, url=url, think=think)
    # Ollama: set the context window and cap excerpts to fit it. llama.cpp sizes
    # its context server-side, so we set neither num_ctx nor a client budget.
    char_budget = None
    if cls is OllamaChat:
        num_ctx = int(settings.get("ask_num_ctx") or config.default("ask_num_ctx"))
        kwargs["num_ctx"] = num_ctx
        char_budget = context_char_budget(num_ctx)
    chat = cls(**kwargs)
    return answer(question, hits, chat, language=language,
                  system_template=settings.get("ask_system_prompt") or None,
                  char_budget=char_budget)
