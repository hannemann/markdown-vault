"""Web import — turn a web page into a clean Markdown note (prototype).

Fetches a URL and extracts the main content as Markdown with Trafilatura — a
Python extractor (no browser), so it stays lightweight and local. Trafilatura is a
base dependency (requirements.txt), but the code still degrades to a clear "not
installed" message via :func:`availability` rather than crashing, in case a venv
is missing it.

Trafilatura does not render JavaScript. A later step can reuse the app's own
WebKitGTK to render JS-heavy pages and feed the rendered HTML to :func:`extract`;
the extraction/assembly below is unchanged by where the HTML comes from.

CLI (once ``trafilatura`` is installed in the venv):
    python -m markdown_vault.web_import <url> [--vault DIR] [--print]
"""

import argparse
import datetime
import logging
import re
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_INSTALL_HINT = ("Web import needs Trafilatura, which isn't installed. Install it "
                 "into the app venv:\n"
                 "  ~/.local/share/markdown-vault/venv/bin/pip install trafilatura")


@dataclass
class ImportResult:
    """The outcome of importing a URL."""
    url: str
    title: str
    markdown: str
    author: str = ""
    date: str = ""            # publication date as reported by the page (YYYY-MM-DD)
    sitename: str = ""


def availability() -> str | None:
    """``None`` when web import can run, else a message explaining what's missing.
    Checks only that the optional Trafilatura dependency is importable."""
    try:
        import trafilatura  # noqa: F401
    except ImportError:
        return _INSTALL_HINT
    return None


def validate_url(url: str) -> str:
    """Return *url* stripped, or raise ``ValueError`` if it isn't an ``http(s)``
    URL — the only schemes we fetch."""
    url = (url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"Not an http(s) URL: {url!r}")
    return url


def fetch_html(url: str, timeout: int = 20) -> str:
    """Fetch *url* and return its decoded HTML. ``http``/``https`` only."""
    url = validate_url(url)
    req = urllib.request.Request(url, headers={"User-Agent": "markdown-vault"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        charset = resp.headers.get_content_charset() or "utf-8"
    return raw.decode(charset, errors="replace")


def extract(html: str, url: str | None = None) -> ImportResult:
    """Extract the main content of *html* as Markdown via Trafilatura."""
    import trafilatura            # optional dep; availability() gates callers

    markdown = trafilatura.extract(
        html, url=url, output_format="markdown",
        include_links=True, include_images=True, include_tables=True,
        include_formatting=True, favor_precision=True) or ""
    meta = trafilatura.extract_metadata(html, default_url=url)
    title = (getattr(meta, "title", None) or "").strip()
    if not title:
        title = (url or "Imported page")
    return ImportResult(
        url=url or "", title=title, markdown=markdown,
        author=(getattr(meta, "author", None) or "").strip(),
        date=(getattr(meta, "date", None) or "").strip(),
        sitename=(getattr(meta, "sitename", None) or "").strip())


def import_url(url: str, timeout: int = 20) -> ImportResult:
    """Fetch *url* and extract it — the whole pipeline in one call."""
    url = validate_url(url)
    return extract(fetch_html(url, timeout=timeout), url=url)


# ── Note assembly ──────────────────────────────────────────────────

def _yaml_escape(value: str) -> str:
    """Quote a scalar for a YAML frontmatter value if it needs it."""
    value = value.replace("\n", " ").strip()
    if value and (value[0] in "\"'>|@`#&*!%[]{},:" or ": " in value
                  or value != value.strip()):
        return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'
    return value


def to_note(result: ImportResult, today: datetime.date | None = None) -> str:
    """Assemble a vault note: YAML frontmatter (title, source, import date, and
    author/date/site when known) followed by the extracted Markdown body."""
    today = today or datetime.date.today()
    lines = ["---",
             f"title: {_yaml_escape(result.title)}",
             f"source: {_yaml_escape(result.url)}",
             f"imported: {today.isoformat()}"]
    if result.author:
        lines.append(f"author: {_yaml_escape(result.author)}")
    if result.date:
        lines.append(f"published: {_yaml_escape(result.date)}")
    if result.sitename:
        lines.append(f"site: {_yaml_escape(result.sitename)}")
    lines += ["---", "", result.markdown.strip(), ""]
    return "\n".join(lines)


_SLUG_STRIP = re.compile(r"[^\w\s-]", re.UNICODE)
_SLUG_DASH = re.compile(r"[\s_-]+")


def slug(text: str, max_len: int = 60) -> str:
    """A safe kebab-case filename stem from *text* (no extension). Falls back to
    ``"imported-page"`` when nothing usable remains."""
    text = _SLUG_STRIP.sub("", (text or "").lower())
    text = _SLUG_DASH.sub("-", text).strip("-")
    return text[:max_len].strip("-") or "imported-page"


def save_to_vault(result: ImportResult, vault_dir: str | Path,
                  today: datetime.date | None = None) -> Path:
    """Write the assembled note into *vault_dir* as ``<slug>.md`` (never
    overwriting: a numeric suffix is added on collision). Returns the path."""
    vault_dir = Path(vault_dir)
    vault_dir.mkdir(parents=True, exist_ok=True)
    stem = slug(result.title)
    target = vault_dir / f"{stem}.md"
    n = 2
    while target.exists():
        target = vault_dir / f"{stem}-{n}.md"
        n += 1
    target.write_text(to_note(result, today=today), encoding="utf-8")
    return target


# ── CLI (prototype driver) ─────────────────────────────────────────

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="markdown_vault.web_import",
                                     description="Import a web page as a Markdown note.")
    parser.add_argument("url")
    parser.add_argument("--vault", help="vault directory to write the note into")
    parser.add_argument("--print", dest="to_stdout", action="store_true",
                        help="print the note instead of writing a file")
    args = parser.parse_args(argv)

    unavailable = availability()
    if unavailable:
        print(unavailable, file=sys.stderr)
        return 2
    try:
        result = import_url(args.url)
    except (ValueError, OSError) as exc:
        print(f"Import failed: {exc}", file=sys.stderr)
        return 1
    if not result.markdown.strip():
        print("Nothing extractable on that page.", file=sys.stderr)
        return 1

    if args.to_stdout or not args.vault:
        print(to_note(result))
    else:
        path = save_to_vault(result, args.vault)
        print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
