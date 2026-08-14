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
import urllib.error
import urllib.request

import yaml
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


_MAX_BYTES = 20 * 1024 * 1024   # cap the body: don't slurp a huge/hostile page
_HTML_TYPES = ("text/html", "application/xhtml+xml", "application/xml", "text/xml")


class _HttpRedirectGuard(urllib.request.HTTPRedirectHandler):
    """Keep validate_url's http(s) allowlist across redirects — urllib otherwise
    follows to ftp too, which would let a redirect leave the allowed schemes."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlparse(newurl).scheme not in ("http", "https"):
            raise urllib.error.HTTPError(
                newurl, code, "refusing a non-http(s) redirect", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_html(url: str, timeout: int = 20) -> str:
    """Fetch *url* and return its decoded HTML. ``http``/``https`` only, rejects a
    redirect that leaves those schemes, refuses non-HTML content types, and caps
    the body at 20 MB so a large or hostile URL can't exhaust memory."""
    url = validate_url(url)
    req = urllib.request.Request(url, headers={"User-Agent": "markdown-vault"})
    opener = urllib.request.build_opener(_HttpRedirectGuard())
    with opener.open(req, timeout=timeout) as resp:
        if resp.headers.get_content_type() not in _HTML_TYPES:
            raise ValueError(f"Not an HTML page: {resp.headers.get_content_type()}")
        length = resp.headers.get("Content-Length")
        if length and length.isdigit() and int(length) > _MAX_BYTES:
            raise ValueError(f"Page too large: {length} bytes")
        raw = resp.read(_MAX_BYTES + 1)
        if len(raw) > _MAX_BYTES:
            raise ValueError(f"Page exceeds the {_MAX_BYTES}-byte limit")
        charset = resp.headers.get_content_charset() or "utf-8"
    return raw.decode(charset, errors="replace")


def _metadata(html: str, url: str | None) -> dict:
    """title/author/date/site from the page's metadata (via Trafilatura's parser).
    Independent of body extraction, so both strategies share it — the A/B differs
    only in the body."""
    import trafilatura
    m = trafilatura.extract_metadata(html, default_url=url)
    g = lambda a: (getattr(m, a, None) or "").strip()
    return {"title": g("title"), "author": g("author"),
            "date": g("date"), "sitename": g("sitename")}


def _assemble(url: str | None, markdown: str, meta: dict,
              fallback_title: str = "") -> ImportResult:
    title = meta.get("title") or fallback_title or (url or "Imported page")
    return ImportResult(url=url or "", title=title, markdown=markdown,
                        author=meta.get("author", ""), date=meta.get("date", ""),
                        sitename=meta.get("sitename", ""))


# ── Table conversion: markdownify turns a simple table into a pipe table; one
#    too complex for that (col/rowspan, block content in a cell, multi-row header,
#    unequal rows) is kept as sanitised HTML, which the preview renders and
#    markdown passes through unchanged. ────────────────────────────────────────

# HTML kept for a table that can't be a markdown pipe table. Structure + safe
# inline formatting only; everything else (script/style/class/id/on*/…) is dropped.
_TABLE_TAGS = {"table", "thead", "tbody", "tfoot", "tr", "td", "th", "caption",
               "colgroup", "col", "b", "strong", "i", "em", "code", "a", "br",
               "sup", "sub", "span", "u", "s", "abbr"}
_BLOCK_TAGS = ["ul", "ol", "table", "p", "pre", "blockquote", "div", "dl",
               "h1", "h2", "h3", "h4", "h5", "h6"]


def _is_complex_table(el) -> bool:
    """Whether a table (bs4 element) has anything a GFM pipe table can't express:
    col/rowspan, a caption, a nested table, block content in a cell, more than one
    header row, or rows of unequal length. Such tables are kept as HTML."""
    cells = el.find_all(["td", "th"])
    if any(c.has_attr("colspan") or c.has_attr("rowspan") for c in cells):
        return True
    if el.find("caption") is not None or el.find("table") is not None:
        return True
    if any(c.find(_BLOCK_TAGS) is not None for c in cells):
        return True
    header_rows = [r for r in el.find_all("tr")
                   if r.find_all("th") and not r.find_all("td")]
    if len(header_rows) > 1:
        return True
    widths = {len(r.find_all(["td", "th"])) for r in el.find_all("tr")
              if r.find_all(["td", "th"])}
    return len(widths) > 1


def _sanitize_table_html(html: str) -> str:
    """Allowlist-sanitise retained table HTML (imported pages are untrusted)."""
    import nh3
    return nh3.clean(
        html, tags=_TABLE_TAGS,
        attributes={"a": {"href"}, "td": {"colspan", "rowspan"},
                    "th": {"colspan", "rowspan", "scope"},
                    "col": {"span"}, "colgroup": {"span"}}).strip()


def _html_to_markdown(content_html: str) -> str:
    from markdownify import MarkdownConverter

    class _Converter(MarkdownConverter):
        def convert_table(self, el, text, parent_tags):
            if _is_complex_table(el):
                return "\n\n" + _sanitize_table_html(str(el)) + "\n\n"
            return super().convert_table(el, text, parent_tags)

    return _Converter(heading_style="ATX", bullets="-").convert(content_html).strip()


# ── Table placeholder swap: keep every real table at its exact position ─────
#
# Trafilatura extracts clean prose but discards most tables (a hardcoded
# link-density heuristic, no off-switch). So before handing the HTML to it, swap
# each real table for a unique text marker. Trafilatura keeps the marker verbatim
# in the prose exactly where the table was; afterwards we swap the marker back for
# the table's markdown. No fuzzy matching — the position is exact by construction.
# A marker Trafilatura happens to drop (table was in a discarded region) leaves its
# table unrestored, so those are appended under a "Tables" heading — never lost.

# Tables whose class marks them as navigation/metadata boilerplate, not content.
_BOILERPLATE_TABLE_CLASS = re.compile(
    r"\b(navbox|vertical-navbox|metadata|mbox|ambox|sidebar|sistersitebox|toc|"
    r"reflist|reference|noprint|navigation)\b", re.I)
# Alphanumeric so it survives Trafilatura/markdown verbatim, distinctive so it
# can't collide with page text.
_MARKER = "mvxtablexplaceholderx{}xend"


def _keep_source_table(el) -> bool:
    if _BOILERPLATE_TABLE_CLASS.search(el.get("class") or ""):
        return False
    return not el.xpath("ancestor::table")   # nested tables ride with their parent


def _inject_placeholders(html: str):
    """Replace each kept source table with a unique text marker; return the modified
    HTML and the list of tables converted to markdown (index = marker number)."""
    from lxml import html as LH
    tree = LH.fromstring(html)
    tables = []
    for el in [t for t in tree.iter("table") if _keep_source_table(t)]:
        parent = el.getparent()
        if parent is None:
            continue                          # can't mark it → Trafilatura drops it
        tmd = _html_to_markdown(LH.tostring(el, encoding="unicode")).strip()
        if not tmd:
            continue
        marker = LH.Element("p")
        marker.text = _MARKER.format(len(tables))
        parent.replace(el, marker)
        tables.append(tmd)
    return LH.tostring(tree, encoding="unicode"), tables


def _restore_placeholders(markdown: str, tables: list) -> str:
    """Swap each marker back for its table; append any table whose marker did not
    survive Trafilatura so nothing is silently lost."""
    leftover = []
    for i, tmd in enumerate(tables):
        marker = _MARKER.format(i)
        if marker in markdown:
            markdown = markdown.replace(marker, "\n\n" + tmd + "\n\n")
        else:
            leftover.append(tmd)
    if leftover:
        markdown += "\n\n## Tables\n\n" + "\n\n".join(leftover)
    return re.sub(r"\n{3,}", "\n\n", markdown).strip()


def extract(html: str, url: str | None = None) -> ImportResult:
    """Extract *html* as a Markdown note: Trafilatura for the prose, with every real
    table kept at its exact position via the placeholder swap and converted
    faithfully (simple -> pipe, complex -> sanitised HTML)."""
    import trafilatura
    modified, tables = _inject_placeholders(html)
    prose = trafilatura.extract(
        modified, url=url, output_format="markdown", include_tables=False,
        include_links=True, include_images=True, include_formatting=True) or ""
    md = _restore_placeholders(prose, tables)
    return _assemble(url, md, _metadata(html, url))


def import_url(url: str, timeout: int = 20) -> ImportResult:
    """Fetch *url* and extract it as a note — the whole pipeline in one call."""
    url = validate_url(url)
    return extract(fetch_html(url, timeout=timeout), url=url)


# ── Note assembly ──────────────────────────────────────────────────

def to_note(result: ImportResult, today: datetime.date | None = None) -> str:
    """Assemble a vault note: YAML frontmatter (title, source, import date, and
    author/date/site when known) followed by the extracted Markdown body. The
    frontmatter is produced by ``yaml.safe_dump`` so every value is quoted exactly
    as YAML requires — a title like ``- foo``, ``? foo``, ``Foo # bar`` or ``true``
    round-trips instead of breaking the block (which would drop *all* frontmatter)."""
    today = today or datetime.date.today()
    clean = lambda v: " ".join(str(v).split())     # collapse stray whitespace/newlines
    fields = {"title": clean(result.title), "source": clean(result.url),
              "imported": today.isoformat()}
    if result.author:
        fields["author"] = clean(result.author)
    if result.date:
        fields["published"] = clean(result.date)
    if result.sitename:
        fields["site"] = clean(result.sitename)
    front = yaml.safe_dump(fields, default_flow_style=False,
                           allow_unicode=True, sort_keys=False).strip()
    return f"---\n{front}\n---\n\n{result.markdown.strip()}\n"


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
