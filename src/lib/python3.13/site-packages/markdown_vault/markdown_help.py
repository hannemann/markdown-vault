"""Markdown Vault — Markdown syntax help overlay.

A semi-transparent overlay showing a quick-reference for Markdown syntax,
organised into categories and spread across multiple pages.  Toggled via
``Ctrl+Space`` (``win.toggle-help``).
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Gdk

# ── Syntax data ──────────────────────────────────────────────────────

_PAGES: list[list[tuple[str, list[tuple[str, str]]]]] = [
    # Page 1 — Basics
    [
        (
            "Headings",
            [
                ("# Heading 1", "# ..."),
                ("## Heading 2", "## ..."),
                ("### Heading 3", "### ..."),
                ("#### – ######", "#### ... through ######"),
            ],
        ),
        (
            "Text Formatting",
            [
                ("**Bold**", "**text**"),
                ("*Italic*", "*text*"),
                ("~~Strikethrough~~", "~~text~~"),
                ("==Highlight==", "==text=="),
                ("`Inline Code`", "`code`"),
                ("Superscript ^sup^", "^text^"),
                ("Subscript ~sub~", "~text~"),
            ],
        ),
        (
            "Lists",
            [
                ("Unordered", "- item  or  * item"),
                ("Ordered", "1. item"),
                ("Nested", "  - sub-item (2 spaces)"),
                ("Task List", "- [ ] todo  /  - [x] done"),
            ],
        ),
        (
            "Links & Images",
            [
                ("Link", "[text](https://url)"),
                ("Image", "![alt](path/to/img.png)"),
                ("Wikilink", "[[page name]]"),
            ],
        ),
    ],
    # Page 2 — Structure
    [
        (
            "Code Blocks",
            [
                ("Fenced", "``` lang\\ncode\\n```"),
                ("Indented", "    (4 spaces)"),
                ("Language label", "```python"),
                ("Highlight lines", "``` {.highlight=[1,3]}"),
            ],
        ),
        (
            "Blockquotes",
            [
                ("Simple", "> quoted text"),
                ("Nested", "> > deeper quote"),
                ("With attribution", "> — Author"),
            ],
        ),
        (
            "Horizontal Rule",
            [
                ("Any of these", "---  or  ***  or  ___"),
            ],
        ),
        (
            "Tables",
            [
                ("Pipe", "| col1 | col2 |"),
                ("Separator", "|------|------|"),
                ("Alignment", "|:left|:-:mid|right:|"),
            ],
        ),
    ],
    # Page 3 — Advanced
    [
        (
            "Math (LaTeX)",
            [
                ("Inline", "$E = mc^2$"),
                ("Block", "$$\\\\int f(x)\\\\,dx$$"),
                ("Fraction", "$\\\\frac{a}{b}$"),
                ("Sum", "$\\\\sum_{i=0}^{n} x_i$"),
            ],
        ),
        (
            "Footnotes",
            [
                ("Reference", "Text with footnote[^1]"),
                ("Definition", "[^1]: Footnote content"),
            ],
        ),
        (
            "Emoji Shortcodes",
            [
                ("Syntax", ":smile:  :rocket:  :heart:"),
                ("Thousands", ":thumbsup: :fire: :check_mark:"),
            ],
        ),
        (
            "Keyboard Keys",
            [
                ("Key notation", "++ctrl+c++"),
                ("With shift", "++shift+tab++"),
            ],
        ),
    ],
]

TOTAL_PAGES = len(_PAGES)


# ── Single category widget ───────────────────────────────────────────

def _build_category(
    title: str, entries: list[tuple[str, str]],
) -> Gtk.Box:
    """Return a vertical box for one syntax category."""
    outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    outer.add_css_class("help-category")

    header = Gtk.Label(label=title, xalign=0.0)
    header.add_css_class("help-category-title")
    outer.append(header)

    grid = Gtk.Grid(column_spacing=18, row_spacing=2)
    grid.add_css_class("help-grid")
    for row_idx, (label, syntax) in enumerate(entries):
        lbl = Gtk.Label(label=label, xalign=0.0)
        lbl.add_css_class("help-entry-label")
        grid.attach(lbl, 0, row_idx, 1, 1)

        syn = Gtk.Label(label=syntax, xalign=0.0)
        syn.add_css_class("help-entry-syntax")
        syn.add_css_class("mono")
        grid.attach(syn, 1, row_idx, 1, 1)

    outer.append(grid)
    return outer


# ── Single page widget ───────────────────────────────────────────────

def _build_page(
    categories: list[tuple[str, list[tuple[str, str]]]],
) -> Gtk.Widget:
    """Return a 2×2 grid of categories for one page."""
    page = Gtk.Grid(column_spacing=24, row_spacing=12, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
    page.add_css_class("help-page")

    for idx, (title, entries) in enumerate(categories):
        col = idx % 2
        row = idx // 2
        page.attach(_build_category(title, entries), col, row, 1, 1)

    return page


# ── Navigation dots ──────────────────────────────────────────────────

class _NavDots(Gtk.Box):
    """Row of clickable dots indicating the current page."""

    def __init__(self, total: int, on_select=None) -> None:
        super().__init__(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=8,
            halign=Gtk.Align.CENTER,
        )
        self.add_css_class("help-nav-dots")
        self._dots: list[Gtk.Button] = []
        self._active: int = 0
        self._on_select = on_select
        for i in range(total):
            btn = Gtk.Button()
            btn.add_css_class("help-dot")
            btn._page_index = i  # type: ignore[attr-defined]
            # Connect with proper lambda to capture the index
            btn.connect("clicked", lambda _btn, i=i: self._on_clicked(i))
            self.append(btn)
            self._dots.append(btn)
        self._update_dots()

    def set_active(self, index: int) -> None:
        self._active = index
        self._update_dots()

    def _update_dots(self) -> None:
        for i, btn in enumerate(self._dots):
            if i == self._active:
                btn.add_css_class("active")
            else:
                btn.remove_css_class("active")

    def _on_clicked(self, page_index: int) -> None:
        if self._on_select:
            self._on_select(page_index)


# ── Main overlay widget ──────────────────────────────────────────────

class MarkdownHelpOverlay(Gtk.Box):
    """Semi-transparent overlay that shows Markdown syntax help.

    The overlay is shown/hidden via ``toggle()`` and closed on Escape
    or ``Ctrl+Space`` (re-toggle).
    """

    def __init__(self) -> None:
        super().__init__(
            orientation=Gtk.Orientation.VERTICAL,
            hexpand=True,
            vexpand=True,
        )
        self.set_focusable(True)
        self.add_css_class("help-overlay")
        self.set_visible(False)

        self._current_page = 0

        # Key controller for Escape.
        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key_ctrl)

        # Semi-transparent backdrop — click to close.
        self._click_ctrl = Gtk.GestureClick()
        self._click_ctrl.connect("released", self._on_backdrop_click)
        self._click_ctrl.set_button(0)  # Primary button
        self.add_controller(self._click_ctrl)

        # ── Content card ─────────────────────────────────────────────
        self._card = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=0,
            halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
        )
        self._card.add_css_class("help-card")
        self.append(self._card)

        # Title bar with close button.
        title_bar = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=8,
        )
        title_bar.add_css_class("help-title-bar")

        title = Gtk.Label(label="Markdown Syntax Quick Reference", xalign=0.0)
        title.add_css_class("help-title")
        title_bar.append(title)

        spacer = Gtk.Box(hexpand=True)
        title_bar.append(spacer)

        close_btn = Gtk.Button(icon_name="window-close-symbolic")
        close_btn.add_css_class("flat")
        close_btn.add_css_class("help-close-btn")
        close_btn.set_tooltip_text("Close (Esc)")
        close_btn.connect("clicked", lambda *_: self.hide_overlay())
        self._close_btn = close_btn
        title_bar.append(close_btn)

        self._card.append(title_bar)

        # Stack for pages.
        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self._stack.set_transition_duration(200)
        self._stack.set_vexpand(True)
        self._stack.add_css_class("help-stack")

        self._pages: list[Gtk.Widget] = []
        for page_cats in _PAGES:
            page = _build_page(page_cats)
            self._stack.add_named(page, None)
            self._pages.append(page)

        self._card.append(self._stack)

        # Navigation bar: prev / dots / next.
        nav = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=12,
            halign=Gtk.Align.CENTER,
        )
        nav.add_css_class("help-nav")

        self._prev_btn = Gtk.Button(icon_name="go-previous-symbolic")
        self._prev_btn.add_css_class("flat")
        self._prev_btn.connect("clicked", lambda *_: self._goto_page(self._current_page - 1))
        nav.append(self._prev_btn)

        self._dots = _NavDots(TOTAL_PAGES, on_select=self._goto_page)
        nav.append(self._dots)

        self._next_btn = Gtk.Button(icon_name="go-next-symbolic")
        self._next_btn.add_css_class("flat")
        self._next_btn.connect("clicked", lambda *_: self._goto_page(self._current_page + 1))
        nav.append(self._next_btn)

        self._card.append(nav)

        # Page counter label.
        self._page_label = Gtk.Label(xalign=0.5)
        self._page_label.add_css_class("help-page-label")
        self._card.append(self._page_label)

        self._update_nav()

    # ── Public API ───────────────────────────────────────────────────

    def toggle(self) -> None:
        """Toggle the overlay visibility."""
        if self.get_visible():
            self.hide_overlay()
        else:
            self.show_overlay()

    def show_overlay(self) -> None:
        self._current_page = 0
        if self._pages:
            self._stack.set_visible_child(self._pages[0])
        self._update_nav()
        self.set_visible(True)
        self._resize_card()
        # Grab focus and ensure the overlay gets keyboard events
        self.grab_focus()
        # Focus the active dot so keyboard navigation works
        if hasattr(self, '_dots') and self._dots._dots:
            self._dots._dots[self._current_page].grab_focus()

    def _resize_card(self) -> None:
        """Set card to 90% of the toplevel window size."""
        root = self.get_root()
        if root is None:
            return
        w = root.get_allocated_width()
        h = root.get_allocated_height()
        if w > 0 and h > 0:
            self._card.set_size_request(int(w * 0.9), int(h * 0.9))

    def hide_overlay(self) -> None:
        self.set_visible(False)

    # ── Navigation ───────────────────────────────────────────────────

    def _goto_page(self, page: int) -> None:
        page = max(0, min(page, TOTAL_PAGES - 1))
        if page == self._current_page:
            return
        self._current_page = page
        if page < len(self._pages):
            self._stack.set_visible_child(self._pages[page])
        self._update_nav()

    def _update_nav(self) -> None:
        self._dots.set_active(self._current_page)
        self._prev_btn.set_sensitive(self._current_page > 0)
        self._next_btn.set_sensitive(self._current_page < TOTAL_PAGES - 1)
        self._page_label.set_text(
            f"{self._current_page + 1} / {TOTAL_PAGES}"
        )

    # ── Keyboard ─────────────────────────────────────────────────────

    def _on_key_pressed(self, _ctrl, keyval: int, _keycode: int, _state) -> bool:
        if keyval == Gdk.KEY_Escape:
            self.hide_overlay()
            return True
        if keyval == Gdk.KEY_Left or keyval == Gdk.KEY_Page_Up:
            self._goto_page(self._current_page - 1)
            return True
        if keyval == Gdk.KEY_Right or keyval == Gdk.KEY_Page_Down:
            self._goto_page(self._current_page + 1)
            return True
        return False

    def _on_backdrop_click(self, _gesture, _n_press, click_x, click_y) -> None:
        # Get card allocation
        card_alloc = self._card.get_allocation()
        # Check if click is inside the card
        if (click_x >= card_alloc.x and click_x <= card_alloc.x + card_alloc.width and
                click_y >= card_alloc.y and click_y <= card_alloc.y + card_alloc.height):
            # Click was on the card, do nothing
            return
        # Click was on the backdrop, close the overlay
        self.hide_overlay()
