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

logger = logging.getLogger(__name__)


@dataclass
class Source:
    """A note passage handed to the model, numbered for citation."""

    n: int
    path: str
    line: int


@dataclass
class Answer:
    text: str
    sources: list = field(default_factory=list)
    error: str | None = None


# The rules are in English (a neutral instruction language the model follows
# regardless of output language); {language} is the user's OS language, which the
# answer must be written in — not the language these rules or the notes are in.
_SYSTEM = (
    "You answer the user's question using ONLY the provided note excerpts — never "
    "from outside knowledge. Rules:\n"
    "- Base every value you use on a specific excerpt and cite it by number, e.g. [1], [2].\n"
    "- You MAY compare, rank, count and combine values ACROSS several excerpts to "
    "reach the answer — e.g. to find the largest, smallest, most or fewest, pick "
    "the extreme among the excerpted values. Every number you compare must come "
    "from an excerpt.\n"
    "- Do not invent facts that are not in any excerpt, and do not read metaphors "
    "or comparisons as facts.\n"
    "- Use only the excerpts relevant to the question; ignore the rest.\n"
    "- If the values needed are missing, say so. Otherwise give a direct answer.\n"
    "- Answer concisely, and write the answer in {language}."
)


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
        sources.append(Source(n=i, path=chunk.path, line=chunk.line))
        blocks.append(f"[{i}] {name} (line {chunk.line}):\n{chunk.text.strip()}")
    context = "\n\n".join(blocks) if blocks else "(no excerpts)"
    system = (system_template or _SYSTEM).replace("{language}", language)
    user = (
        f"Question: {question}\n\n"
        f"Note excerpts:\n{context}\n\n"
        "Answer the question using only these excerpts."
    )
    return system, user, sources


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

    def __init__(self, model: str, url: str = "http://localhost:11434",
                 timeout: float = 120.0, think: bool | None = None) -> None:
        self.model = model
        self.url = url.rstrip("/")
        self.timeout = timeout
        self.think = think

    def chat(self, system: str, user: str) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "options": {"temperature": 0.2},
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


def answer(question: str, hits, chat: OllamaChat, language: str = "English",
           system_template: str | None = None) -> Answer:
    """Retrieve-augmented generation: ground *question* in *hits* and ask *chat*.

    *language* is the language the answer must be written in; *system_template*
    the (user-configurable) system prompt.
    """
    if not hits:
        return Answer(text="I couldn't find anything about that in your notes.")
    system, user, sources = build_messages(question, hits, language, system_template)
    try:
        text = chat.chat(system, user)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        logger.warning("ollama chat failed: %s", exc)
        return Answer(text="", sources=sources, error=str(exc))
    return Answer(text=text, sources=sources)
