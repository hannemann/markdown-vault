"""Markdown Vault — wikilink parsing and backlink discovery.

Provides helpers to extract ``[[Page]]`` / ``[[Page|alias]]`` style
links from Markdown text, resolve them to concrete files, and find
all files that link *to* a given target.
"""

import os
import re
from pathlib import Path

WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")


def parse_wikilinks(text: str) -> list[tuple[str, str | None]]:
    """Return all ``(page, alias)`` pairs found in *text*.

    ``alias`` is ``None`` when no pipe syntax is used.
    """
    return [(m.group(1), m.group(2)) for m in WIKILINK_RE.finditer(text)]


def resolve_link(
    page_name: str,
    current_file: Path,
    vault_paths: list[str],
) -> Path | None:
    """Resolve *page_name* to an existing ``.md`` file.

    The search order is:

    1. Same directory as *current_file*
    2. Each vault root (in order)

    Returns ``None`` when no matching file is found.
    """
    current_dir = current_file.parent
    candidates: list[Path] = [
        current_dir / f"{page_name}.md",
        current_dir / page_name,
    ]
    for vp in vault_paths:
        vault = Path(vp)
        candidates.append(vault / f"{page_name}.md")
        candidates.append(vault / page_name)
    for candidate in candidates:
        if candidate.exists() and candidate.suffix == ".md":
            return candidate
    return None


def find_backlinks(target_file: Path, vault_paths: list[str]) -> list[Path]:
    """Return all ``.md`` files in *vault_paths* that link to *target_file*.

    Matching is done by comparing the link target against the stem
    (filename without extension) of *target_file*.
    """
    backlinks: list[Path] = []
    target_stem = target_file.stem
    for vp in vault_paths:
        for root, _dirs, files in os.walk(vp):
            for fname in files:
                if not fname.endswith(".md"):
                    continue
                fpath = Path(root) / fname
                if fpath.resolve() == target_file.resolve():
                    continue
                try:
                    text = fpath.read_text(encoding="utf-8")
                except OSError:
                    continue
                for page, _alias in parse_wikilinks(text):
                    if page == target_stem:
                        backlinks.append(fpath)
                        break
    return backlinks
