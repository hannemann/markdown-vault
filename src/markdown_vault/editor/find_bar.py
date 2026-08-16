"""Markdown Vault — in-view find bar (Ctrl+F).

An inline bar with a search entry, match counter, prev/next buttons, query
modifiers (case / whole-word / regex) and a replace row.  It is UI-only: it
emits signals and the main window wires them to whichever view is being
searched.  The modifiers and the replace row are editor-only and hidden when
the preview is the active view (see :meth:`set_editor_mode`).
"""

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk, Gdk, GObject


class FindBar(Gtk.Box):
    """Inline find/replace bar. Backend-agnostic — driven via signals.

    Signals:
        search-changed(str): the query text changed.
        search-next(): go to the next match (Enter / down button).
        search-prev(): go to the previous match (Shift+Enter / up button).
        options-changed(): a case/word/regex toggle changed.
        replace-one(): replace the current match.
        replace-all(): replace every match.
        closed(): the bar was dismissed (Esc / close button).
    """

    __gsignals__ = {
        "search-changed": (GObject.SignalFlags.RUN_LAST, None, (str,)),
        "search-next": (GObject.SignalFlags.RUN_LAST, None, ()),
        "search-prev": (GObject.SignalFlags.RUN_LAST, None, ()),
        "options-changed": (GObject.SignalFlags.RUN_LAST, None, ()),
        "replace-one": (GObject.SignalFlags.RUN_LAST, None, ()),
        "replace-all": (GObject.SignalFlags.RUN_LAST, None, ()),
        "closed": (GObject.SignalFlags.RUN_LAST, None, ()),
    }

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.add_css_class("find-bar")
        self.set_visible(False)

        # ── Find row ─────────────────────────────────────────────────
        find_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        self._entry = Gtk.SearchEntry()
        self._entry.set_hexpand(False)
        self._entry.set_width_chars(40)
        self._entry.set_max_width_chars(40)
        self._entry.set_placeholder_text("Find in view…")
        self._entry.connect(
            "search-changed",
            lambda e: self.emit("search-changed", e.get_text()),
        )
        self._entry.connect("activate", lambda _e: self.emit("search-next"))
        # Capture phase so Esc/Enter reach us before the SearchEntry's own
        # stop-search/activate handling.
        key = Gtk.EventControllerKey()
        key.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        key.connect("key-pressed", self._on_key)
        self._entry.add_controller(key)
        find_row.append(self._entry)

        self._counter = Gtk.Label()
        self._counter.add_css_class("find-counter")
        self._counter.add_css_class("dim-label")

        # Query modifiers (editor-only).
        self._case_btn = self._make_toggle("Aa", "Case sensitive")
        self._word_btn = self._make_toggle("W", "Whole word")
        self._regex_btn = self._make_toggle(".*", "Regular expression")
        for btn in (self._case_btn, self._word_btn, self._regex_btn):
            btn.connect("toggled", lambda *_: self.emit("options-changed"))
            find_row.append(btn)

        prev_btn = Gtk.Button(icon_name="go-up-symbolic")
        prev_btn.add_css_class("flat")
        prev_btn.set_tooltip_text("Previous match (Shift+Enter)")
        prev_btn.connect("clicked", lambda *_: self.emit("search-prev"))
        find_row.append(prev_btn)

        next_btn = Gtk.Button(icon_name="go-down-symbolic")
        next_btn.add_css_class("flat")
        next_btn.set_tooltip_text("Next match (Enter)")
        next_btn.connect("clicked", lambda *_: self.emit("search-next"))
        find_row.append(next_btn)

        find_row.append(self._counter)  # after nav so Aa lines up with Replace

        spacer = Gtk.Box(hexpand=True)  # push the close button to the right
        find_row.append(spacer)

        close_btn = Gtk.Button(icon_name="window-close-symbolic")
        close_btn.add_css_class("flat")
        close_btn.set_tooltip_text("Close (Esc)")
        close_btn.connect("clicked", lambda *_: self.close())
        find_row.append(close_btn)

        self.append(find_row)

        # ── Replace row (editor-only) ────────────────────────────────
        self._replace_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._replace_entry = Gtk.Entry()
        self._replace_entry.set_hexpand(False)
        self._replace_entry.set_width_chars(40)
        self._replace_entry.set_max_width_chars(40)
        self._replace_entry.set_placeholder_text("Replace with…")
        # Match the search entry's leading icon so both fields are the same size.
        self._replace_entry.set_icon_from_icon_name(
            Gtk.EntryIconPosition.PRIMARY, "document-edit-symbolic",
        )
        self._replace_entry.connect("activate", lambda *_: self.emit("replace-one"))
        rkey = Gtk.EventControllerKey()
        rkey.connect("key-pressed", self._on_replace_key)
        self._replace_entry.add_controller(rkey)
        # SearchEntry and Entry have different internal chrome, so force both
        # fields to the exact same width.
        size_group = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)
        size_group.add_widget(self._entry)
        size_group.add_widget(self._replace_entry)
        self._replace_row.append(self._replace_entry)

        replace_btn = Gtk.Button(label="Replace")
        replace_btn.add_css_class("flat")
        replace_btn.connect("clicked", lambda *_: self.emit("replace-one"))
        self._replace_row.append(replace_btn)

        replace_all_btn = Gtk.Button(label="Replace All")
        replace_all_btn.add_css_class("flat")
        replace_all_btn.connect("clicked", lambda *_: self.emit("replace-all"))
        self._replace_row.append(replace_all_btn)

        self._replace_row.set_visible(False)  # shown only via Ctrl+R
        self.append(self._replace_row)

    @staticmethod
    def _make_toggle(label: str, tooltip: str) -> Gtk.ToggleButton:
        btn = Gtk.ToggleButton(label=label)
        btn.set_tooltip_text(tooltip)
        btn.add_css_class("flat")
        return btn

    # ── Public API ───────────────────────────────────────────────────

    def open(self) -> None:
        """Show the bar, focus the entry, and re-run any existing query."""
        self.set_visible(True)
        self._entry.grab_focus()
        if self._entry.get_text():
            self.emit("search-changed", self._entry.get_text())

    def focus_replace(self) -> None:
        """Show the bar + replace row and focus the replace field (Ctrl+R)."""
        self.set_visible(True)
        self._replace_row.set_visible(True)
        self._replace_entry.grab_focus()

    def close(self) -> None:
        self.set_visible(False)
        self.emit("closed")

    def get_text(self) -> str:
        return self._entry.get_text()

    def get_replace_text(self) -> str:
        return self._replace_entry.get_text()

    def get_options(self) -> tuple[bool, bool, bool]:
        """Return (case_sensitive, whole_word, regex)."""
        return (
            self._case_btn.get_active(),
            self._word_btn.get_active(),
            self._regex_btn.get_active(),
        )

    def set_editor_mode(self, is_editor: bool) -> None:
        """Show the query modifiers only when searching the editor.

        The preview view supports neither modifiers nor replace, so a preview
        search also hides the replace row.
        """
        for btn in (self._case_btn, self._word_btn, self._regex_btn):
            btn.set_visible(is_editor)
        if not is_editor:
            self._replace_row.set_visible(False)

    def set_replace_visible(self, visible: bool) -> None:
        """Show/hide the replace row (Ctrl+R shows it, Ctrl+F hides it)."""
        self._replace_row.set_visible(visible)

    def set_count(self, current: int, total: int) -> None:
        """Update the match counter. ``total`` < 0 means 'still counting'."""
        if not self._entry.get_text():
            self._counter.set_text("")
        elif total < 0 or current > total:
            self._counter.set_text("…")
        elif total == 0:
            self._counter.set_text("0/0")
        elif current > 0:
            self._counter.set_text(f"{current}/{total}")
        else:
            self._counter.set_text(f"{total}")

    # ── Internal ─────────────────────────────────────────────────────

    def _on_key(self, _ctrl, keyval: int, _keycode: int, state) -> bool:
        if keyval == Gdk.KEY_Escape:
            self.close()
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            if state & Gdk.ModifierType.SHIFT_MASK:
                self.emit("search-prev")
            else:
                self.emit("search-next")
            return True
        return False

    def _on_replace_key(self, _ctrl, keyval: int, _keycode: int, _state) -> bool:
        if keyval == Gdk.KEY_Escape:
            self.close()
            return True
        return False  # let Enter fall through to activate -> replace-one
