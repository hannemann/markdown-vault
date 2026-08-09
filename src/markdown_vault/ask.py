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


_SYSTEM = (
    "Du beantwortest die Frage des Nutzers ausschließlich aus den bereitgestellten "
    "Notiz-Auszügen. Wichtige Regeln:\n"
    "- Jeder Auszug stammt aus einer EIGENEN Notiz (Dateiname ist angegeben) und "
    "kann ein ANDERES Thema behandeln. Übertrage Eigenschaften niemals von einem "
    "Thema auf ein anderes (Ringe eines Gasriesen gehören z. B. nicht zur Erde).\n"
    "- Verwende nur Auszüge, die sich direkt auf das Thema der Frage beziehen; "
    "ignoriere die übrigen.\n"
    "- Erfinde nichts und nutze kein Vorwissen. Fehlt die Information wirklich, "
    "sage: \"Das steht nicht in deinen Notizen.\"\n"
    "- Antworte knapp auf Deutsch und belege mit Quellennummern wie [1], [2]."
)


def build_messages(question: str, hits) -> tuple[str, str, list]:
    """Build ``(system, user, sources)`` from a question and retrieved hits.

    *hits* is a list of ``(chunk, score)`` where *chunk* has ``path``, ``line``
    and ``text`` (the semantic index's :class:`Chunk`).
    """
    sources: list = []
    blocks: list = []
    for i, (chunk, _score) in enumerate(hits, start=1):
        name = os.path.basename(chunk.path)
        sources.append(Source(n=i, path=chunk.path, line=chunk.line))
        blocks.append(f"[{i}] {name} (Zeile {chunk.line}):\n{chunk.text.strip()}")
    context = "\n\n".join(blocks) if blocks else "(keine Auszüge)"
    user = (
        f"Frage: {question}\n\n"
        f"Notiz-Auszüge:\n{context}\n\n"
        "Beantworte die Frage nur aus diesen Auszügen."
    )
    return _SYSTEM, user, sources


class OllamaChat:
    """Chat completion via a running Ollama server (``POST /api/chat``).

    No extra Python dependency; with a localhost URL nothing leaves the machine.
    """

    def __init__(self, model: str, url: str = "http://localhost:11434",
                 timeout: float = 120.0) -> None:
        self.model = model
        self.url = url.rstrip("/")
        self.timeout = timeout

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
        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            self.url + "/api/chat", data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read())
        return ((data.get("message") or {}).get("content") or "").strip()


def answer(question: str, hits, chat: OllamaChat) -> Answer:
    """Retrieve-augmented generation: ground *question* in *hits* and ask *chat*."""
    if not hits:
        return Answer(text="Dazu finde ich nichts in deinen Notizen.")
    system, user, sources = build_messages(question, hits)
    try:
        text = chat.chat(system, user)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        logger.warning("ollama chat failed: %s", exc)
        return Answer(text="", sources=sources, error=str(exc))
    return Answer(text=text, sources=sources)
