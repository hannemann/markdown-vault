"""Markdown Vault — wikilink parsing and backlink discovery.

Provides helpers to extract ``[[Page]]`` / ``[[Page|alias]]`` style
links from Markdown text, resolve them to concrete files, and find
all files that link *to* a given target.
"""

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WikilinkInfo:
    """Parsed information about a single wikilink.

    Attributes
    ----------
    raw : str
        Original content between ``[[`` and ``]]`` (e.g. ``"VaultA>sub/note|Alias"``).
    stem : str
        Target path without vault prefix or alias (e.g. ``"sub/note"``).
    vault : str | None
        Vault name prefix if present (e.g. ``"VaultA"``), else ``None``.
    alias : str | None
        Display alias after ``|``, else ``None``.
    display : str
        Full display string: ``stem|alias`` or just ``stem``.
    """

    raw: str
    stem: str
    vault: str | None
    alias: str | None
    display: str


# Match wikilinks: [[...]] with optional |alias and >vault-prefix.
# The > must appear at the very start (right after [[).
# vault: only non-], non-whitespace chars.
# stem: non-|, non-] chars.
WIKILINK_RE = re.compile(
    r"\[\["
    r"(?:(?P<vault>[^>\s]+)>(?P<stem1>[^]\|]+))?"
    r"(?P<stem2>[^]\|]+)?"
    r"(?:\|(?P<alias>[^\]]+))?"
    r"\]\]"
)


def _info_from_match(m: re.Match) -> WikilinkInfo:
    """Build a ``WikilinkInfo`` from a ``WIKILINK_RE`` match."""
    raw = m.group(0)[2:-2]  # Strip [[ and ]]
    vault = m.group("vault")
    stem = m.group("stem1") or m.group("stem2")
    alias = m.group("alias")
    display = f"{stem}|{alias}" if alias else stem
    return WikilinkInfo(
        raw=raw,
        stem=stem,
        vault=vault,
        alias=alias,
        display=display,
    )


def wikilink_info_from_match(m: re.Match) -> WikilinkInfo:
    """Return the ``WikilinkInfo`` for a ``WIKILINK_RE`` match (public API)."""
    return _info_from_match(m)


def parse_wikilinks(text: str) -> list[WikilinkInfo]:
    """Parse all wikilinks in *text* and return a list of ``WikilinkInfo``.

    Supports vault-prefix syntax: ``[[VaultName>path/to/file|Alias]]``.
    The vault prefix must start with ``>`` immediately after ``[[``.
    """
    results: list[WikilinkInfo] = []
    for m in WIKILINK_RE.finditer(text):
        results.append(_info_from_match(m))
    return results
