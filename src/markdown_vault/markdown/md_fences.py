"""Shared fenced-code tracking for line-by-line Markdown scanners.

Several scanners across the app need to know whether a source line sits inside a
fenced code block so they do not rewrite, mark, or misinterpret its contents.
Each used to carry its own ``in_fence`` toggle, at four different levels of
correctness — three of which had bugs where an inner fence or a differing fence
character flipped the state (see review findings R103.1, R113.1, R115.1). This
module is the single correct implementation they all share.

:class:`FenceTracker` applies the CommonMark closing rule: a fence opened by a
run of *N* backticks or tildes closes only on a later line that is a run of the
**same** character, **at least N** long, and nothing else — so a ``` inside a
```` block, or a ``~~~`` against a backtick fence, is content, not a close.
"""

import re

# An opening fence: 3+ backticks or tildes (optionally indented), then an
# optional info string. The info string is captured for callers that need the
# code language. Public so single-line "does this line open a fence?" checks
# (which need no state) share the one pattern too.
FENCE_OPEN_RE = re.compile(r"^[ \t]*(`{3,}|~{3,})[ \t]*(\S.*)?$")


class FenceTracker:
    """Line-by-line fenced-code state machine.

    Feed source lines in order to :meth:`feed`; query :attr:`in_fence` for the
    state after the last line, and :attr:`opened` / :attr:`closed` / :attr:`info`
    for what the last line did.
    """

    def __init__(self) -> None:
        self._char = ""
        self._len = 0
        self._in = False
        self.opened = False     # the last fed line opened a fence
        self.closed = False     # the last fed line closed a fence
        self.info = ""          # info string (e.g. language) of the fence just opened

    @property
    def in_fence(self) -> bool:
        """Whether the scanner is inside a fenced block after the last :meth:`feed`.

        The opening marker counts as inside; the closing marker ends the fence,
        so ``in_fence`` is ``False`` after it (``feed()`` still returns ``True``
        for that line, since the marker itself belongs to the block).
        """
        return self._in

    def feed(self, line: str) -> bool:
        """Advance the state by one *line*.

        Returns ``True`` if the line belongs to a fenced block — its opening
        marker, any content between the markers, or its closing marker.
        """
        self.opened = False
        self.closed = False
        self.info = ""
        if self._in:
            stripped = line.strip()
            if (stripped and set(stripped) == {self._char}
                    and len(stripped) >= self._len):
                self._in = False
                self.closed = True
            return True
        m = FENCE_OPEN_RE.match(line)
        if m:
            self._in = True
            self._char = m.group(1)[0]
            self._len = len(m.group(1))
            self.info = (m.group(2) or "").strip()
            self.opened = True
            return True
        return False
