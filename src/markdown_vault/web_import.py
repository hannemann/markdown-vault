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
import ipaddress
import logging
import re
import socket
import sys
import urllib.error
import urllib.request

import yaml
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse

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
# No "span"/"div": presentational wrappers carry no table meaning and only leak
# markup noise into a kept table; nh3 unwraps them, preserving their text.
# "img" is kept so a normalised image inside a complex table survives (its src has
# already been resolved to absolute and every other attribute stripped upstream).
_TABLE_TAGS = {"table", "thead", "tbody", "tfoot", "tr", "td", "th", "caption",
               "colgroup", "col", "b", "strong", "i", "em", "code", "a", "br",
               "sup", "sub", "u", "s", "abbr", "img"}
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
                    "img": {"src", "alt"},
                    "col": {"span"}, "colgroup": {"span"}}).strip()


def _html_to_markdown(content_html: str) -> str:
    from markdownify import MarkdownConverter

    class _Converter(MarkdownConverter):
        def convert_table(self, el, text, parent_tags):
            if _is_complex_table(el):
                return "\n\n" + _sanitize_table_html(str(el)) + "\n\n"
            return super().convert_table(el, text, parent_tags)

    # keep_inline_images_in: markdownify otherwise reduces an <img> in a table cell
    # to its alt text; the app's renderer (markdown.extensions.tables) renders
    # ``| ![alt](url) |`` as an image, so keep the image markdown in the pipe cell.
    return _Converter(heading_style="ATX", bullets="-",
                      keep_inline_images_in=["td", "th"]).convert(content_html).strip()


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


# ── Images (Strategy C: normalise <img> in place) ──────────────────
#
# Trafilatura's own image handling is unreliable — it drops most images and, on
# some markup (Wikipedia), emits a mangled ``![<span>…]`` src. So before handing
# the HTML over we clean every ``<img>`` down to a bare ``src``/``alt``, resolving
# the URL to absolute and dropping tracking pixels; whatever Trafilatura then keeps
# as content comes out as a sound ``![alt](https://…)``. Recall stays bounded by
# Trafilatura's content model — this fixes correctness, not completeness.

_SRC_ATTRS = ("src", "data-src", "data-original", "data-lazy-src")
_PIXEL_STYLE = re.compile(r"\b(?:width|height)\s*:\s*[01]px", re.I)


def _is_tracking_pixel(el) -> bool:
    """A 1x1 (or 0-sized) image is a beacon, not content."""
    for attr in ("width", "height"):
        v = (el.get(attr) or "").strip()
        if v[:1].isdigit() and int(re.match(r"\d+", v).group()) <= 1:
            return True
    return bool(_PIXEL_STYLE.search(el.get("style") or ""))


def _pick_img_src(el, base_url: str | None) -> str | None:
    """The best source URL for *el*, resolved to absolute against *base_url*.
    Prefers a plain ``src``, then common lazy-load attributes, then the largest
    ``srcset`` candidate. Returns ``None`` when nothing usable is present."""
    chosen = next((el.get(a).strip() for a in _SRC_ATTRS if (el.get(a) or "").strip()), "")
    if not chosen:
        srcset = (el.get("srcset") or el.get("data-srcset") or "").strip()
        best_w = -1
        for cand in srcset.split(","):
            parts = cand.split()
            if not parts:
                continue
            w = int(parts[1][:-1]) if len(parts) > 1 and parts[1].endswith("w") \
                and parts[1][:-1].isdigit() else 0
            if w >= best_w:
                best_w, chosen = w, parts[0]
    if not chosen:
        return None
    resolved = urljoin(base_url, chosen) if base_url else chosen
    # Scheme allowlist — every other URL in this module is scheme-checked; keep
    # this one consistent. javascript:/other schemes are dropped; data: only for
    # images (not, say, data:text/html).
    if urlparse(resolved).scheme.lower() in ("http", "https") \
            or resolved.lower().startswith("data:image/"):
        return resolved
    return None


def _normalize_images(html: str, base_url: str | None) -> str:
    """Reduce every ``<img>`` to a bare absolute ``src`` + ``alt``, dropping
    tracking pixels and sourceless images. Returns the modified HTML."""
    from lxml import html as LH
    tree = LH.fromstring(html)
    _normalize_images_tree(tree, base_url)
    return LH.tostring(tree, encoding="unicode")


def _normalize_images_tree(tree, base_url: str | None) -> None:
    for el in list(tree.iter("img")):
        if _is_tracking_pixel(el):
            el.drop_tree()
            continue
        src = _pick_img_src(el, base_url)
        if not src:
            el.drop_tree()
            continue
        alt = el.get("alt") or ""
        for attr in list(el.attrib):
            del el.attrib[attr]
        el.set("src", src)
        el.set("alt", alt)


def _clean_content_html(html: str, base_url: str | None) -> str:
    """Pre-extraction DOM cleanup in a single parse: unwrap presentational
    ``<span>`` (Trafilatura otherwise leaks syntax-highlight spans into code
    blocks as raw ``<span>`` noise) keeping their text, then normalise images."""
    from lxml import html as LH
    tree = LH.fromstring(html)
    for sp in list(tree.iter("span")):
        sp.drop_tag()          # unwrap: keep the text, drop the presentational tag
    _normalize_images_tree(tree, base_url)
    return LH.tostring(tree, encoding="unicode")


def extract(html: str, url: str | None = None) -> ImportResult:
    """Extract *html* as a Markdown note: Trafilatura for the prose, with every real
    table kept at its exact position via the placeholder swap and converted
    faithfully (simple -> pipe, complex -> sanitised HTML), and every ``<img>``
    normalised to a clean absolute URL first (tracking pixels dropped)."""
    import trafilatura
    # Clean the whole tree FIRST — unwrap spans and normalise every <img> to a
    # clean absolute src (tracking pixels dropped) — so images inside tables are
    # normalised too, before _inject_placeholders converts those tables.
    cleaned = _clean_content_html(html, url)
    modified, tables = _inject_placeholders(cleaned)
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


# ── Optional local image download ──────────────────────────────────

_IMG_MD = re.compile(r"!\[([^\]]*)\]\(<?(https?://[^)>\s]+)>?\)")
_MAX_IMAGES = 100                       # per note, bounds a hostile page's fan-out
_MAX_IMAGE_TOTAL = 100 * 1024 * 1024    # total bytes downloaded per note
_IMG_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".ico", ".avif")
_CTYPE_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif",
              "image/webp": ".webp", "image/svg+xml": ".svg", "image/bmp": ".bmp",
              "image/x-icon": ".ico", "image/avif": ".avif"}
_FNAME_SANITISE = re.compile(r"[^a-z0-9._-]+")


def _addr_blocked(addr: str) -> bool:
    """Whether an IP string is a non-public (private/loopback/link-local/reserved)
    address we must not fetch — the link-local range covers cloud metadata
    endpoints (169.254.169.254)."""
    try:
        ip = ipaddress.ip_address(addr.split("%")[0])
    except ValueError:
        return True
    return (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
            or ip.is_multicast or ip.is_unspecified)


def _host_is_public(host: str) -> bool:
    """Resolve *host* and refuse if any resolved address is non-public. Guards the
    image download (URLs come from the page, not the user) against blind SSRF to
    localhost/LAN/metadata endpoints."""
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError):
        return False
    return bool(infos) and not any(_addr_blocked(i[4][0]) for i in infos)


class _ImageRedirectGuard(_HttpRedirectGuard):
    """Redirect guard for image fetches: the inherited scheme check plus a
    public-address check on the redirect target. Image URLs come from the page,
    so a 302 to ``127.0.0.1`` or the metadata endpoint must be refused too. The
    page fetch keeps the plain guard so a user-typed intranet URL still works.
    (DNS rebinding between resolve and connect remains an accepted residual.)"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and not _host_is_public(urlparse(new.full_url).hostname or ""):
            raise urllib.error.HTTPError(newurl, code,
                                         "refusing a redirect to a non-public host",
                                         headers, fp)
        return new


def _fetch_image(url: str, timeout: int = 20):
    """Download *url* as ``(bytes, content_type)``; ``None`` on any error, a
    non-image response, or a non-public host. Same http(s)-only, size-capped,
    redirect-guarded fetch as the page itself, plus an SSRF address guard on the
    initial URL and every redirect target."""
    host = urlparse(url).hostname or ""
    if not _host_is_public(host):
        logger.warning("web_import: refusing image from non-public host %r", host)
        return None
    try:
        opener = urllib.request.build_opener(_ImageRedirectGuard())
        req = urllib.request.Request(url, headers={"User-Agent": "markdown-vault"})
        with opener.open(req, timeout=timeout) as resp:
            ctype = resp.headers.get_content_type()
            if not ctype.startswith("image/"):
                return None
            raw = resp.read(_MAX_BYTES + 1)
            if len(raw) > _MAX_BYTES:
                return None
        return raw, ctype
    except (urllib.error.URLError, ValueError, OSError) as exc:
        logger.warning("web_import: image download failed for %s: %s", url, exc)
        return None


def _image_filename(url: str, content_type: str, taken: set) -> str:
    """A safe, unique local filename for an image *url*, keeping its extension
    (or deriving one from *content_type*)."""
    name = _FNAME_SANITISE.sub("-", urlparse(url).path.rsplit("/", 1)[-1].lower()).strip("-.")
    stem, dot, ext = name.rpartition(".")
    if dot and f".{ext}" in _IMG_EXTS:
        stem, ext = stem, f".{ext}"
    else:
        stem, ext = name, _CTYPE_EXT.get(content_type, ".img")
    stem = stem or "image"
    candidate = f"{stem}{ext}"
    n = 2
    while candidate in taken:
        candidate = f"{stem}-{n}{ext}"
        n += 1
    taken.add(candidate)
    return candidate


def _localize_images(markdown: str, dest_dir: Path, rel_prefix: str,
                     fetch=_fetch_image) -> str:
    """Download each remote image referenced in *markdown* into *dest_dir* and
    rewrite its link to ``rel_prefix/<file>``. Each URL is fetched once (dedup);
    a failed download leaves the original remote URL in place so nothing is lost.
    Non-http(s) links (already-local, ``data:``) are untouched. Bounded by
    ``_MAX_IMAGES`` and ``_MAX_IMAGE_TOTAL`` so a hostile page cannot make the
    importer download without limit; images past a limit keep their remote URL."""
    mapping: dict[str, str | None] = {}
    taken: set = set()
    dest_dir = Path(dest_dir)
    total = 0
    for _alt, url in _IMG_MD.findall(markdown):
        if url in mapping:
            continue
        if len(taken) >= _MAX_IMAGES or total >= _MAX_IMAGE_TOTAL:
            logger.warning("web_import: image limit reached, keeping remote URL %s", url)
            mapping[url] = None
            continue
        got = fetch(url)
        if not got:
            mapping[url] = None
            continue
        data, ctype = got
        dest_dir.mkdir(parents=True, exist_ok=True)
        fname = _image_filename(url, ctype, taken)
        (dest_dir / fname).write_bytes(data)
        total += len(data)
        mapping[url] = f"{rel_prefix}/{fname}"

    def repl(m):
        rel = mapping.get(m.group(2))
        return f"![{m.group(1)}]({rel})" if rel else m.group(0)

    return _IMG_MD.sub(repl, markdown)


def save_to_vault(result: ImportResult, vault_dir: str | Path,
                  today: datetime.date | None = None,
                  download_images: bool = False, name: str | None = None) -> Path:
    """Write the assembled note into *vault_dir* as ``<slug>.md`` (never
    overwriting: a numeric suffix is added on collision). Returns the path.

    *name* overrides the filename stem (slugged); a blank one falls back to the
    page title. When *download_images* is set, remote images are downloaded into
    ``attachments/<slug>/`` beside the note and rewritten to relative links, so
    the note and its images can be removed together."""
    import dataclasses
    vault_dir = Path(vault_dir)
    vault_dir.mkdir(parents=True, exist_ok=True)
    stem = slug(name) if name and name.strip() else slug(result.title)
    target = vault_dir / f"{stem}.md"
    n = 2
    while target.exists():
        target = vault_dir / f"{stem}-{n}.md"
        n += 1
    stem = target.stem
    if download_images:
        localized = _localize_images(result.markdown, vault_dir / "attachments" / stem,
                                     f"attachments/{stem}")
        result = dataclasses.replace(result, markdown=localized)
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
    parser.add_argument("--download-images", action="store_true",
                        help="download images into attachments/<note>/ beside the note")
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
        path = save_to_vault(result, args.vault, download_images=args.download_images)
        print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
