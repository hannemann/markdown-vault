"""Import a local document (PDF, Word, PowerPoint, Excel, audio) as a Markdown note.

A dedicated, self-contained importer parallel to :mod:`web_import`; the two share
only :mod:`note_writer` (slug + collision-free path), never each other. Conversion
is dispatched by file suffix to a lightweight, torch-free backend per format class
— generalist within each class, exactly as a single tool would branch internally:

    PDF   -> pymupdf4llm       Word  -> mammoth
    PPTX  -> python-pptx       XLSX  -> openpyxl
    audio -> faster-whisper (CTranslate2, CPU; transcription model on first use)

These live in the optional AI stack (``make install-ai``); the heavy libraries are
imported lazily inside each handler so this module — and :func:`is_available` — stay
importable on a base install, letting the UI show the install hint instead of
crashing. There is no OCR: scanned documents yield their (empty) digital text only.
"""

import datetime
import importlib.util
import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

from . import note_writer

logger = logging.getLogger(__name__)

_INSTALL_HINT = ("Document import needs the optional AI stack, which isn't "
                 "installed. Add it to the app venv:\n  make install-ai")

# Default CTranslate2 Whisper model size for audio (overridable in Preferences).
# Options: tiny, base, small, medium, large-v3 — bigger = more accurate, slower,
# larger download. Downloaded explicitly via Preferences, never during an import.
_WHISPER_MODEL = "base"


def whisper_model_name() -> str:
    """The configured Whisper model size/id (Preferences → Audio transcription),
    defaulting to ``base``."""
    from . import config
    return (config.load_settings().get("document_whisper_model") or _WHISPER_MODEL).strip()


def _whisper_repo(name: str) -> str:
    """Hugging Face repo for a size name (``base`` → ``Systran/faster-whisper-base``);
    a full ``owner/repo`` id is used as-is."""
    return name if "/" in name else f"Systran/faster-whisper-{name}"


_WHISPER_FILES = ["config.json", "preprocessor_config.json", "model.bin",
                  "tokenizer.json", "vocabulary.*"]


def whisper_model_dir(name: str | None = None) -> "Path":
    """App-local folder for a Whisper model size, beside the other downloaded models
    (``<state>/models/whisper-<size>/``) — not the global HuggingFace cache, so it is
    visible, managed by the app, and removed with it."""
    from . import config
    safe = (name or whisper_model_name()).replace("/", "--")
    return config.models_dir() / f"whisper-{safe}"

_AUDIO_SUFFIXES = (".mp3", ".wav", ".m4a", ".ogg", ".flac", ".opus", ".aac")

# The backend each format needs. Availability is checked against just these, so
# opening a PDF never probes the audio stack. (markdownify, used by the docx path,
# is a base dependency and always present, so it is not listed.)
_HANDLER_MODULES = {
    ".pdf": ("pymupdf4llm",),
    ".docx": ("mammoth",),
    ".pptx": ("pptx",),
    ".xlsx": ("openpyxl",),
    **{s: ("faster_whisper",) for s in _AUDIO_SUFFIXES},
}


@dataclass
class DocumentResult:
    """A converted document ready to become a note."""
    path: str          # absolute path of the source file (kept as the note's source)
    title: str         # note title (document metadata title, else the file stem)
    markdown: str      # the converted body


def _installed(module: str) -> bool:
    """Whether *module* is importable — via ``find_spec``, which locates it WITHOUT
    executing it (loading e.g. faster_whisper runs CTranslate2's native extension,
    which must never happen just to answer 'is it available?')."""
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def is_available(suffix: str | None = None) -> str | None:
    """``None`` when the backend(s) needed for *suffix* are importable, else an install
    hint. With no *suffix* the whole feature is checked (any format usable). Only the
    modules that *suffix* actually needs are probed, and never executed."""
    if suffix is not None:
        modules = _HANDLER_MODULES.get(suffix.lower(), ())
    else:
        modules = {m for mods in _HANDLER_MODULES.values() for m in mods}
    if any(not _installed(m) for m in modules):
        return _INSTALL_HINT
    return None


# ── Per-format conversion ──────────────────────────────────────────


def _rows_to_pipe_table(rows: list[list[str]]) -> str:
    """A GitHub-style pipe table from *rows* (first row is the header). Returns "" for
    no rows. Cells have pipes/newlines neutralised so one cell can't break the table."""
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    def cell(v):
        return " ".join(str(v).split()).replace("|", "\\|") or " "
    def line(r):
        r = list(r) + [""] * (width - len(r))
        return "| " + " | ".join(cell(c) for c in r) + " |"
    out = [line(rows[0]), "| " + " | ".join(["---"] * width) + " |"]
    out += [line(r) for r in rows[1:]]
    return "\n".join(out)


def _pdf_title(path: Path) -> str:
    try:
        import pymupdf
        with pymupdf.open(str(path)) as doc:
            return " ".join((doc.metadata or {}).get("title", "").split())
    except Exception as exc:                       # metadata is best-effort only
        logger.debug("PDF title read failed for %s: %s", path, exc)
        return ""


def _convert_pdf(path: Path) -> tuple[str, str]:
    import pymupdf4llm
    md = pymupdf4llm.to_markdown(str(path))
    return md, _pdf_title(path)


def _convert_docx(path: Path) -> tuple[str, str]:
    # Route via mammoth's HTML, not its Markdown: mammoth's Markdown writer drops
    # tables (its HTML keeps them), so convert to HTML and let markdownify emit the
    # pipe tables — the same converter the web importer relies on.
    import mammoth
    import markdownify
    with open(path, "rb") as fh:
        result = mammoth.convert_to_html(fh)
    for msg in result.messages:                    # unsupported styles etc. — not fatal
        logger.debug("mammoth: %s", msg)
    md = markdownify.markdownify(result.value, heading_style="ATX", bullets="-")
    return md, ""                                  # docx has no reliable title metadata


def _convert_pptx(path: Path) -> tuple[str, str]:
    from pptx import Presentation
    prs = Presentation(str(path))
    parts: list[str] = []
    for i, slide in enumerate(prs.slides, 1):
        parts.append(f"## Slide {i}")
        for shape in slide.shapes:
            if shape.has_table:
                rows = [[c.text for c in row.cells] for row in shape.table.rows]
                table = _rows_to_pipe_table(rows)
                if table:
                    parts.append(table)
            elif shape.has_text_frame:
                text = shape.text_frame.text.strip()
                if text:
                    parts.append(text)
        notes = slide.notes_slide.notes_text_frame.text.strip() \
            if slide.has_notes_slide else ""
        if notes:
            parts.append(f"> **Notes:** {notes}")
    title = " ".join((prs.core_properties.title or "").split())
    return "\n\n".join(parts), title


def _convert_xlsx(path: Path) -> tuple[str, str]:
    import openpyxl
    wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    parts: list[str] = []
    for ws in wb.worksheets:
        rows = [["" if c is None else str(c) for c in row]
                for row in ws.iter_rows(values_only=True)]
        table = _rows_to_pipe_table(rows)
        if table:
            parts.append(f"## {ws.title}\n\n{table}")
    title = " ".join((wb.properties.title or "").split()) if wb.properties else ""
    wb.close()
    return "\n\n".join(parts), title


def _model_missing_msg() -> str:
    return (f"The transcription model ('{whisper_model_name()}') isn't downloaded "
            "yet. Download it in Preferences → Search before importing audio.")


def whisper_model_ready(name: str | None = None) -> bool:
    """True if the model is downloaded into its app-local folder. A plain file check
    (no network, and no loading of ``faster_whisper``), so it is cheap and safe to
    call from anywhere."""
    return (whisper_model_dir(name) / "model.bin").exists()


def download_whisper_model(name: str | None = None, tqdm_class=None) -> str:
    """Download the transcription model into its app-local folder (beside the other
    models, not the global HF cache) and return that path. Blocking — meant for a
    background thread behind the Preferences download button. *tqdm_class* is forwarded
    to ``snapshot_download`` so the caller can drive a progress bar (faster-whisper's
    own ``download_model`` does not expose it)."""
    from huggingface_hub import snapshot_download
    name = name or whisper_model_name()
    dest = whisper_model_dir(name)
    dest.mkdir(parents=True, exist_ok=True)
    snapshot_download(_whisper_repo(name), allow_patterns=_WHISPER_FILES,
                      local_dir=str(dest), tqdm_class=tqdm_class)
    return str(dest)


def _convert_audio(path: Path) -> tuple[str, str]:
    from faster_whisper import WhisperModel
    if not whisper_model_ready():                  # never download silently mid-import
        raise RuntimeError(_model_missing_msg())
    model = WhisperModel(str(whisper_model_dir()), device="cpu", compute_type="int8")
    segments, info = model.transcribe(str(path))
    text = " ".join(seg.text.strip() for seg in segments).strip()
    logger.info("Transcribed %s (%s, %.1fs)", path.name,
                getattr(info, "language", "?"), getattr(info, "duration", 0.0))
    return text, ""


_HANDLERS = {
    ".pdf": _convert_pdf,
    ".docx": _convert_docx,
    ".pptx": _convert_pptx,
    ".xlsx": _convert_xlsx,
    **{s: _convert_audio for s in _AUDIO_SUFFIXES},
}

SUPPORTED_SUFFIXES = tuple(sorted(_HANDLERS))


def needs_transcription_model(suffix: str) -> bool:
    """True if *suffix* is an audio format — its import needs the Whisper model."""
    return suffix.lower() in _AUDIO_SUFFIXES


def convert(path: str | Path) -> DocumentResult:
    """Convert *path* to a :class:`DocumentResult` by dispatching on its suffix.
    Raises ``ValueError`` for an unsupported type and lets a backend's own error
    (corrupt/locked file) propagate to the caller for display."""
    path = Path(path)
    handler = _HANDLERS.get(path.suffix.lower())
    if handler is None:
        raise ValueError(f"Unsupported file type: {path.suffix or '(none)'}")
    markdown, title = handler(path)
    return DocumentResult(path=str(path.resolve()),
                          title=title or path.stem, markdown=markdown)


# ── Note assembly + save ───────────────────────────────────────────


def to_note(result: DocumentResult, today: datetime.date | None = None) -> str:
    """Assemble the vault note: YAML frontmatter (title, source file, import date)
    via ``yaml.safe_dump`` so any title round-trips, then the converted body."""
    today = today or datetime.date.today()
    clean = lambda v: " ".join(str(v).split())
    fields = {"title": clean(result.title),
              "source": clean(result.path),
              "imported": today.isoformat()}
    front = yaml.safe_dump(fields, default_flow_style=False,
                           allow_unicode=True, sort_keys=False).strip()
    return f"---\n{front}\n---\n\n{result.markdown.strip()}\n"


def save_to_vault(result: DocumentResult, vault_dir: str | Path,
                  today: datetime.date | None = None,
                  name: str | None = None) -> Path:
    """Write the note into *vault_dir* as ``<slug>.md`` (never overwriting — a numeric
    suffix is added on collision). *name* overrides the stem; a blank one falls back to
    the document title. Returns the written path."""
    vault_dir = Path(vault_dir)
    vault_dir.mkdir(parents=True, exist_ok=True)
    stem = note_writer.slug(name if name and name.strip() else result.title,
                            fallback="imported-document")
    target = note_writer.unique_path(vault_dir, stem)
    target.write_text(to_note(result, today=today), encoding="utf-8")
    return target


def import_file(path: str | Path, vault_dir: str | Path,
                name: str | None = None) -> Path:
    """Convert *path* and save it into *vault_dir* — the whole pipeline in one call."""
    return save_to_vault(convert(path), vault_dir, name=name)
