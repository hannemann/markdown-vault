"""Ordered, de-duplicated collection of graph node "cards".

Pure data (no GTK) so it is unit-testable and the panel widget stays a thin view over
it. A card is keyed by its file path: clicking the same node twice does not add a
second card, and the first one keeps its place and content.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Card:
    """One collected node: the info-panel content plus the file it links to."""

    path: str
    title: str
    desc: str
    vault: str
    color: str


class CardStore:
    def __init__(self) -> None:
        # dict preserves insertion order, which is the display order.
        self._by_path: dict[str, Card] = {}

    def add(self, card: Card) -> bool:
        """Add a card unless one with the same path is already present.

        Returns True if it was added, False if it was a duplicate. A duplicate is a
        no-op: the existing card keeps its position and content, so re-clicking a node
        never reorders or overwrites the collection.
        """
        if card.path in self._by_path:
            return False
        self._by_path[card.path] = card
        return True

    def remove(self, path: str) -> bool:
        """Remove the card for `path`; return True if one was there."""
        return self._by_path.pop(path, None) is not None

    def clear(self) -> None:
        self._by_path.clear()

    def cards(self) -> list:
        """The cards in insertion order."""
        return list(self._by_path.values())

    def __contains__(self, path: str) -> bool:
        return path in self._by_path

    def __len__(self) -> int:
        return len(self._by_path)
