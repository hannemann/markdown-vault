"""Following a link inside a note.

The rule is one rule, and it was written out three times in ``MainWindow``:
resolve the target's vault, switch vaults first if it lives in another one, open
it (in place or in a new tab), then scroll to the anchor if the link named one.
Two of those three handlers were identical line for line except for which open
function they called.

That duplication is why this exists, not the coupling count — it costs one wire
more than it removes. What it buys is a single place where "cross-vault link"
and "jump to heading" are decided, and a name for what the code is doing.
"""

import logging
from pathlib import Path

import gi

logger = logging.getLogger(__name__)

gi.require_version("Gtk", "4.0")

from markdown_vault.core import path_utils
from markdown_vault.uikit import dialogs


class LinkNavigator:
    """Turns a clicked link into "open this, there, and scroll to that"."""

    def __init__(self, *, parent, get_current_tab, get_active_vault,
                 open_in_place, open_in_new_tab, switch_vault, is_open) -> None:
        self._parent = parent                    # dialog parent for "not found"
        self._get_current_tab = get_current_tab
        self._is_open = is_open                  # note already in some tab?
        self._get_active_vault = get_active_vault
        self._open_in_place = open_in_place
        self._open_in_new_tab = open_in_new_tab
        self._switch_vault = switch_vault

    # ── the signal handlers the preview connects to ────────────────

    def on_link_clicked(self, _preview, file_path: str, fragment: str = "") -> None:
        """A plain click — follow the link in the same tab where that is safe."""
        self.follow(file_path, fragment, new_tab=False)

    def on_link_new_tab(self, _preview, file_path: str, fragment: str = "") -> None:
        """Middle-click / Ctrl+click — always a new tab."""
        self.follow(file_path, fragment, new_tab=True)

    def on_link_not_found(self, _preview, path_str: str) -> None:
        """The wikilink does not resolve — say so, with a readable target name."""
        dialogs.show_link_not_found(self._parent, self.display_name(path_str))

    # ── the rule, once ─────────────────────────────────────────────

    def follow(self, file_path: str, fragment: str = "", *, new_tab: bool) -> None:
        """Open *file_path*, switching vaults first when it lives in another one.

        The anchor jump is handed to the preview *after* opening, because only
        then is it clear which one shows the target: depending on the view mode a
        link reuses the tab or opens a new one. The cross-vault case defers it
        the same way, via the switch's post-open callback.
        """
        vault = path_utils.find_vault_for_dir(str(Path(file_path).parent))
        # Whether the note is already open decides how the jump gets there, and
        # opening changes the answer — so ask now, for both routes.
        was_open = self._is_open(file_path)
        post = ((lambda: self._jump(fragment, rendered=not was_open))
                if fragment else None)
        if vault and vault != self._get_active_vault():
            self._switch_vault(vault, open_file_path=file_path, post_open_fn=post)
            return
        if new_tab:
            self._open_in_new_tab(file_path)
        else:
            self._open_in_place(file_path)
        if post is not None:
            self._jump(fragment, rendered=not was_open)

    def _jump(self, fragment: str, *, rendered: bool = True) -> None:
        """Get *fragment* to the preview that now shows the note.

        Two cases, and taking the wrong one loses the jump:

        * The note was **not** open, so opening it renders — and the window then
          rebuilds the stack and reloads the preview, discarding any scroll made
          now. The jump is *armed* and runs when that load reports FINISHED.
        * The note **was** already open, so the tab is merely activated: nothing
          renders, no FINISHED arrives, and an armed jump would sit there
          unspent (and fire on some later, unrelated load). Its content is on
          screen already, so scroll right away.
        """
        tab = self._get_current_tab()
        if not tab or not fragment:
            return
        if rendered:
            tab.preview.arm_anchor(fragment)
        else:
            tab.preview.scroll_to_anchor(fragment)

    def scroll_to_anchor(self, fragment: str) -> None:
        """Scroll the current tab's preview to *fragment* — the heading a
        cross-note wikilink (``[[Other#Heading]]``) pointed at. Deferred inside
        the preview until the freshly opened note has rendered."""
        tab = self._get_current_tab()
        if tab and fragment:
            tab.preview.scroll_to_anchor(fragment)

    @staticmethod
    def display_name(uri: str) -> str:
        """A user-friendly target name from a ``vault:`` URI."""
        if not uri.startswith("vault:"):
            return uri
        vault, rel, _fragment = path_utils.parse_wikilink_url(uri)
        if not rel:
            return vault
        return f"{vault}>{rel}"
