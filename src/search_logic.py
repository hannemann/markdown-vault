"""Search logic for vault full-text search.

Pure Python functions for querying vault directories. No GTK dependencies.
"""

import os
import re
from datetime import datetime
from pathlib import Path


def search_vaults(
    query: str,
    vault_paths: list[str],
    max_results: int = 50,
) -> list[tuple[str, int, str]]:
    """Search all vaults for *query*.

    Returns a list of ``(filepath, line_number, line_text)`` tuples,
    limited to *max_results*.
    """
    if not query:
        return []

    results: list[tuple[str, int, str]] = []
    query_lower = query.lower()

    for vault_path in vault_paths:
        for root, _dirs, files in os.walk(vault_path):
            for fname in files:
                if not fname.endswith(".md"):
                    continue
                fpath = os.path.join(root, fname)
                try:
                    with open(fpath, "r", encoding="utf-8") as fh:
                        for i, line in enumerate(fh, 1):
                            if query_lower in line.lower():
                                results.append((fpath, i, line))
                except OSError:
                    continue

    return results[:max_results]


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)


def extract_headings(text: str) -> list[tuple[int, str, int]]:
    """Extract Markdown headings from *text*.

    Returns a list of ``(level, heading_text, line_number)`` tuples,
    where *line_number* is 0-based.
    """
    results: list[tuple[int, str, int]] = []
    for match in _HEADING_RE.finditer(text):
        level = len(match.group(1))
        heading = match.group(2)
        line = text[:match.start()].count("\n")
        results.append((level, heading, line))
    return results


def compute_file_details(
    file_path: str | Path,
    text: str = "",
) -> dict[str, str | int]:
    """Compute metadata for a file.

    Returns a dict with keys: word_count, line_count, size, modified.
    """
    p = Path(file_path)
    try:
        stat = p.stat()
    except OSError:
        return {
            "word_count": 0,
            "line_count": 0,
            "size": 0,
            "modified": "",
        }

    word_count = len(text.split()) if text else 0
    line_count = text.count("\n") + 1 if text else 0
    modified = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")

    return {
        "word_count": word_count,
        "line_count": line_count,
        "size": stat.st_size,
        "modified": modified,
    }