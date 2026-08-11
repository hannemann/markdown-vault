"""Markdown Vault — preferences dialog.

Provides an ``Adw.PreferencesDialog`` for editing application settings
such as autosave interval, editor appearance, and default view mode.
Changes are applied immediately and persisted to ``vaults.yaml``.
"""

import logging
import threading
from pathlib import Path

import gi

logger = logging.getLogger(__name__)

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")

from gi.repository import Gtk, Adw, GObject, Gdk, GLib, Gio

from . import config
from . import dialogs

_VIEW_MODES = {"edit": "Edit", "render": "Render", "split": "Split"}
_LOGLEVELS = {"debug": "Debug", "info": "Info", "warning": "Warning", "error": "Error"}
_GLIB_LOGLEVELS = {
    "all": "All (debug+)",
    "warning": "Warning and up",
    "critical": "Critical and up",
    "error": "Error only",
}
_LOGLEVEL_MAP = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}


def _accel_to_label(accel: str) -> str:
    """Convert a GTK accelerator string to a human-readable label."""
    if not accel:
        return "None"
    ok, keyval, mods = Gtk.accelerator_parse(accel)
    if not ok or keyval == 0:
        return accel
    parts = []
    if mods & Gdk.ModifierType.SHIFT_MASK:
        parts.append("Shift")
    if mods & Gdk.ModifierType.CONTROL_MASK:
        parts.append("Ctrl")
    if mods & Gdk.ModifierType.ALT_MASK:
        parts.append("Alt")
    if mods & Gdk.ModifierType.SUPER_MASK:
        parts.append("Super")
    key_name = Gdk.keyval_name(keyval)
    if key_name:
        parts.append(key_name.capitalize())
    return "+".join(parts)


_RELEVANT_MODS = (
    Gdk.ModifierType.SHIFT_MASK
    | Gdk.ModifierType.CONTROL_MASK
    | Gdk.ModifierType.ALT_MASK
    | Gdk.ModifierType.SUPER_MASK
)


class PreferencesDialog(Adw.PreferencesDialog):
    """Application preferences dialog.

    Signals:
        settings-changed(): Emitted whenever a setting is modified.
    """

    __gsignals__ = {
        "settings-changed": (GObject.SignalFlags.RUN_LAST, None, ()),
    }

    # Semantic-search backends and their single source of truth for the default,
    # so the picker, sensitivity and config never drift (R22.10).
    _SEM_BACKENDS = ("onnx", "ollama")
    _SEM_BACKEND_DEFAULT = "onnx"

    def __init__(self, *, glib_loglevel_callback=None, on_reindex=None) -> None:
        """Initialize preferences dialog.

        *glib_loglevel_callback* is an optional callable that is invoked
        when the GLib log level changes (allows live reconfiguration
        without restart).

        *on_reindex* is an optional callable that discards the semantic-search
        cache and rebuilds the index against the currently selected backend,
        live.  When ``None`` the rebuild button is disabled.
        """
        super().__init__(title="Preferences")

        self._settings = config.load_settings()
        self._glib_loglevel_callback = glib_loglevel_callback
        self._on_reindex = on_reindex
        # Debounced disk writes for text entries (R22.11): typing a URL by hand
        # must not rewrite vaults.yaml on every keystroke.
        self._persist_id = None
        self.connect("closed", self._flush_persist)

        # ── General page ────────────────────────────────────────────
        general = Adw.PreferencesPage(title="General", icon_name="preferences-other-symbolic")

        # Autosave group.
        autosave_group = Adw.PreferencesGroup(title="Autosave")
        general.add(autosave_group)

        self._autosave_row = Adw.SpinRow(
            title="Autosave interval (seconds)",
            adjustment=Gtk.Adjustment.new(
                self._settings.get("autosave_interval", 30), 0, 600, 5, 10, 0,
            ),
        )
        self._autosave_row.connect("notify::value", self._on_autosave_changed)
        autosave_group.add(self._autosave_row)

        # Default view mode group.
        view_group = Adw.PreferencesGroup(title="Default View Mode")
        general.add(view_group)

        self._view_row = Adw.ComboRow(
            title="View mode for new tabs",
            model=Gtk.StringList.new(list(_VIEW_MODES.values())),
        )
        modes = list(_VIEW_MODES.keys())
        current_mode = self._settings.get("default_view_mode", "edit")
        self._view_row.set_selected(modes.index(current_mode) if current_mode in modes else 0)
        self._view_row.connect("notify::selected", self._on_view_mode_changed)
        view_group.add(self._view_row)

        self.add(general)

        # ── Editor page ─────────────────────────────────────────────
        editor = Adw.PreferencesPage(title="Editor", icon_name="document-edit-symbolic")

        font_group = Adw.PreferencesGroup(title="Font &amp; Layout")
        editor.add(font_group)

        self._font_row = Adw.SpinRow(
            title="Font size",
            adjustment=Gtk.Adjustment.new(
                self._settings.get("editor_font_size", 14), 8, 72, 1, 5, 0,
            ),
        )
        self._font_row.connect("notify::value", self._on_font_size_changed)
        font_group.add(self._font_row)

        self._tab_row = Adw.SpinRow(
            title="Tab width",
            adjustment=Gtk.Adjustment.new(
                self._settings.get("editor_tab_width", 4), 1, 16, 1, 4, 0,
            ),
        )
        self._tab_row.connect("notify::value", self._on_tab_width_changed)
        font_group.add(self._tab_row)

        self._wrap_row = Adw.SwitchRow(title="Word wrap")
        self._wrap_row.set_active(self._settings.get("editor_wrap_text", True))
        self._wrap_row.connect("notify::active", self._on_wrap_changed)
        font_group.add(self._wrap_row)

        # Tabs group.
        tabs_group = Adw.PreferencesGroup(title="Tabs")
        editor.add(tabs_group)

        self._tab_width_row = Adw.SpinRow(
            title="Minimum tab width (px)",
            adjustment=Gtk.Adjustment.new(
                self._settings.get("tab_min_width", 150), 50, 300, 10, 50, 0,
            ),
        )
        self._tab_width_row.connect("notify::value", self._on_tab_min_width_changed)
        tabs_group.add(self._tab_width_row)

        self._tab_wrap_row = Adw.SwitchRow(title="Wrap tabs")
        self._tab_wrap_row.set_active(self._settings.get("tab_wrap", False))
        self._tab_wrap_row.connect("notify::active", self._on_tab_wrap_changed)
        tabs_group.add(self._tab_wrap_row)

        # Wikilinks group.
        wikilink_group = Adw.PreferencesGroup(
            title="Wikilinks",
            description="Autofix and validation of [[wikilinks]] when saving.",
        )
        editor.add(wikilink_group)

        self._wl_rows: dict[str, Adw.SwitchRow] = {}
        for key, title, subtitle in (
            ("wikilink_autofix_normalize", "Normalize on save",
             "Trim whitespace inside [[…]] when saving."),
            ("wikilink_autofix_relink", "Auto-fix moved links",
             "Redirect a broken link when exactly one matching file exists."),
            ("wikilink_warn_on_save", "Warn about broken links",
             "After saving, show a notice for links that can't be resolved."),
            ("wikilink_mark_broken", "Mark broken links in the editor",
             "Gutter warning triangle and red underline on unresolved links."),
        ):
            row = Adw.SwitchRow(title=title, subtitle=subtitle)
            row.set_active(self._settings.get(key, False))
            row.connect("notify::active", self._on_wikilink_toggle_changed, key)
            wikilink_group.add(row)
            self._wl_rows[key] = row

        self.add(editor)

        # ── Preview page ────────────────────────────────────────────
        preview = Adw.PreferencesPage(title="Preview", icon_name="document-properties-symbolic")

        zoom_group = Adw.PreferencesGroup(title="Zoom")
        preview.add(zoom_group)

        zoom_value = self._settings.get("preview_zoom", 1.0)
        self._zoom_row = Adw.SpinRow(
            title="Default zoom level",
            adjustment=Gtk.Adjustment.new(zoom_value, 0.25, 5.0, 0.05, 0.25, 0),
            digits=2,
        )
        self._zoom_row.connect("notify::value", self._on_zoom_changed)
        zoom_group.add(self._zoom_row)

        privacy_group = Adw.PreferencesGroup(title="Privacy")
        preview.add(privacy_group)

        self._remote_images_row = Adw.SwitchRow(
            title="Allow remote images",
            subtitle=(
                "Load images from https:// URLs in notes. Off blocks them so a "
                "note cannot beacon your IP; scripts stay blocked either way."
            ),
        )
        self._remote_images_row.set_active(
            self._settings.get("preview_allow_remote_images", False)
        )
        self._remote_images_row.connect(
            "notify::active", self._on_remote_images_changed,
        )
        privacy_group.add(self._remote_images_row)

        self.add(preview)

        # ── Web page ────────────────────────────────────────────────
        web = Adw.PreferencesPage(
            title="Web", icon_name="applications-internet-symbolic",
        )

        web_group = Adw.PreferencesGroup(title="WebKit Rendering")
        web.add(web_group)

        self._dmabuf_row = Adw.SwitchRow(title="Disable DMA-BUF renderer")
        self._dmabuf_row.subtitle = (
            "Lowers GPU/video memory usage "
            "(WEBKIT_DISABLE_DMABUF_RENDERER). Takes effect after restart."
        )
        self._dmabuf_row.set_active(
            self._settings.get("webkit_disable_dmabuf", False),
        )
        self._dmabuf_row.connect(
            "notify::active", self._on_webkit_toggle_changed,
            "webkit_disable_dmabuf",
        )
        web_group.add(self._dmabuf_row)

        self._compositing_row = Adw.SwitchRow(
            title="Disable hardware acceleration",
        )
        self._compositing_row.subtitle = (
            "Render without the GPU (WEBKIT_DISABLE_COMPOSITING_MODE). "
            "Takes effect after restart."
        )
        self._compositing_row.set_active(
            self._settings.get("webkit_disable_compositing", False),
        )
        self._compositing_row.connect(
            "notify::active", self._on_webkit_toggle_changed,
            "webkit_disable_compositing",
        )
        web_group.add(self._compositing_row)

        self.add(web)

        # ── Search page (overview → Embedding / Ask / Prompt subpages) ──
        from . import ask as _ask
        self._emb_subpage = self._build_embedding_subpage()
        self._prompt_subpage = self._build_prompt_subpage(_ask)
        self._ask_subpage = self._build_ask_subpage()

        search = Adw.PreferencesPage(title="Search", icon_name="edit-find-symbolic")
        sem_group = Adw.PreferencesGroup(
            title="Semantic search",
            description=(
                "Find notes by meaning. Off by default; nothing is downloaded "
                "or contacted while disabled. Recommended backend: Local (ONNX) "
                "— runs in-process, no server, nothing leaves your machine. "
                "Ollama is an alternative if you already run a server (e.g. with "
                "a GPU). Changes take effect after restart."
            ),
        )
        search.add(sem_group)

        self._sem_enabled_row = Adw.SwitchRow(title="Enable semantic search")
        self._sem_enabled_row.set_active(
            self._settings.get("semantic_search_enabled", False))
        self._sem_enabled_row.connect(
            "notify::active", self._on_toggle_setting, "semantic_search_enabled")
        sem_group.add(self._sem_enabled_row)

        self._sem_backends = list(self._SEM_BACKENDS)
        self._sem_backend_row = Adw.ComboRow(
            title="Backend",
            subtitle="Local runs in-process (recommended); Ollama needs a server",
            model=Gtk.StringList.new(["Local (ONNX) — recommended", "Ollama (server)"]),
        )
        self._sem_backend_row.set_selected(self._sem_backend_index())
        self._sem_backend_row.connect("notify::selected", self._on_sem_backend_changed)
        sem_group.add(self._sem_backend_row)

        self._sem_score_row = Adw.SpinRow(
            title="Minimum similarity",
            subtitle="Higher = stricter (fewer, closer matches)",
            adjustment=Gtk.Adjustment.new(
                self._settings.get("semantic_min_score", 0.35), 0.0, 1.0, 0.05, 0.1, 0.0,
            ),
            digits=2,
        )
        self._sem_score_row.connect("notify::value", self._on_min_score_changed)
        sem_group.add(self._sem_score_row)

        # Rebuild: discard the cache and re-embed everything against the
        # currently selected backend, live — the way to apply a backend switch
        # (or a freshly downloaded model) without restarting.
        self._sem_rebuild_row = Adw.ActionRow(
            title="Rebuild index now",
            subtitle="Clear the cache and re-embed all notes with the selected backend",
        )
        self._sem_rebuild_btn = Gtk.Button(
            label="Rebuild", valign=Gtk.Align.CENTER)
        self._sem_rebuild_btn.add_css_class("destructive-action")
        self._sem_rebuild_btn.set_sensitive(self._on_reindex is not None)
        self._sem_rebuild_btn.connect("clicked", self._on_rebuild_index)
        self._sem_rebuild_row.add_suffix(self._sem_rebuild_btn)
        self._sem_rebuild_row.set_activatable_widget(self._sem_rebuild_btn)
        sem_group.add(self._sem_rebuild_row)

        # Navigation into the detailed configuration subpages.
        cfg_group = Adw.PreferencesGroup(title="Configuration")
        cfg_group.add(self._nav_row(
            "Embedding", "Model that turns notes into vectors", self._emb_subpage))
        cfg_group.add(self._nav_row(
            "Ask (answers from your notes)",
            "Chat model + prompt for synthesized answers", self._ask_subpage))
        search.add(cfg_group)

        self._update_sem_backend_sensitivity()
        self._refresh_onnx_status()
        self._refresh_ask_models()
        threading.Thread(target=self._probe_onnx_runtime, daemon=True).start()

        self.add(search)

        # ── Keyboard page ──────────────────────────────────────────
        keyboard = Adw.PreferencesPage(title="Keyboard", icon_name="input-keyboard-symbolic")

        kb_group = Adw.PreferencesGroup(title="Keybindings")
        keyboard.add(kb_group)

        self._next_tab_row = Adw.ActionRow(title="Next tab")
        self._next_tab_btn = Gtk.Button()
        self._next_tab_btn.add_css_class("flat")
        self._next_tab_btn.set_valign(Gtk.Align.CENTER)
        self._setup_keybinding_button(
            self._next_tab_btn, "keybinding_next_tab",
        )
        self._next_tab_row.add_suffix(self._next_tab_btn)
        kb_group.add(self._next_tab_row)

        self._prev_tab_row = Adw.ActionRow(title="Previous tab")
        self._prev_tab_btn = Gtk.Button()
        self._prev_tab_btn.add_css_class("flat")
        self._prev_tab_btn.set_valign(Gtk.Align.CENTER)
        self._setup_keybinding_button(
            self._prev_tab_btn, "keybinding_prev_tab",
        )
        self._prev_tab_row.add_suffix(self._prev_tab_btn)
        kb_group.add(self._prev_tab_row)

        switch_group = Adw.PreferencesGroup(title="Tab switching")
        keyboard.add(switch_group)

        self._mode_row = Adw.ComboRow(
            title="Tab switch behaviour",
            subtitle="MRU switches to the most recently used tab, Cycle goes in order",
            model=Gtk.StringList.new(["Most Recently Used", "Cycle in Order"]),
        )
        current_mode = self._settings.get("tab_switch_mode", "mru")
        self._mode_row.set_selected(0 if current_mode == "mru" else 1)
        self._mode_row.connect("notify::selected", self._on_tab_switch_mode_changed)
        switch_group.add(self._mode_row)

        self.add(keyboard)

        # ── Debug page ──────────────────────────────────────────────
        debug = Adw.PreferencesPage(title="Debug", icon_name="utilities-system-monitor-symbolic")

        log_group = Adw.PreferencesGroup(title="Logging")
        debug.add(log_group)

        self._loglevel_row = Adw.ComboRow(
            title="Log level",
            model=Gtk.StringList.new(list(_LOGLEVELS.values())),
        )
        current_level = self._settings.get("loglevel", "info")
        levels = list(_LOGLEVELS.keys())
        self._loglevel_row.set_selected(
            levels.index(current_level) if current_level in levels else 1
        )
        self._loglevel_row.connect("notify::selected", self._on_loglevel_changed)
        log_group.add(self._loglevel_row)

        self._tp_loglevel_row = Adw.ComboRow(
            title="Third-party log level",
            subtitle="markdown, pymdownx, pygments, xml",
            model=Gtk.StringList.new(list(_LOGLEVELS.values())),
        )
        tp_level = self._settings.get("third_party_loglevel", "warning")
        self._tp_loglevel_row.set_selected(
            levels.index(tp_level) if tp_level in levels else 2
        )
        self._tp_loglevel_row.connect("notify::selected", self._on_tp_loglevel_changed)
        log_group.add(self._tp_loglevel_row)

        # GLib log level (for Gtk/WebKit/GJS messages).
        self._glib_loglevel_row = Adw.ComboRow(
            title="GLib log level",
            subtitle="Gtk, WebKit, Gjs messages",
            model=Gtk.StringList.new(list(_GLIB_LOGLEVELS.values())),
        )
        glib_level = self._settings.get("glib_loglevel", "critical")
        glib_levels = list(_GLIB_LOGLEVELS.keys())
        self._glib_loglevel_row.set_selected(
            glib_levels.index(glib_level) if glib_level in glib_levels
            else glib_levels.index("warning")
        )
        self._glib_loglevel_row.connect("notify::selected", self._on_glib_loglevel_changed)
        log_group.add(self._glib_loglevel_row)

        # Debug dump toggles (only useful in debug mode).
        dump_group = Adw.PreferencesGroup(title="Debug Dumps")
        debug.add(dump_group)

        self._dump_toggles: dict[str, Adw.SwitchRow] = {}
        for key, title in (
            ("file_index", "File Index"),
            ("backlink_index", "Backlink Index"),
            ("preview_html", "Preview HTML"),
            ("vault_tree", "Vault Tree"),
            ("tabs", "Tabs"),
            ("sidebar", "Sidebar"),
        ):
            row = Adw.SwitchRow(title=title)
            row.set_active(self._settings.get(f"debug_dump_{key}", False))
            row.connect("notify::active", self._on_dump_toggle_changed, key)
            dump_group.add(row)
            self._dump_toggles[key] = row

        self.add(debug)

    # ── Search subpages ─────────────────────────────────────────────

    _LABEL_WIDTH = 140  # fixed title column so all fields start at the same x

    def _entry_row(self, title, key, trailing=None):
        """A list row whose value field is a plain ``Gtk.Entry``, so the default
        shows as an always-visible placeholder (like an HTML ``<input
        placeholder>``) — unlike ``Adw.EntryRow``, whose floating title covers
        the placeholder slot until focused.

        The row is hand-built (not ``Adw.ActionRow``, whose central title area
        expands so a suffix entry never fills the row): a fixed-width label, then
        the entry which ``hexpand``s to the row's right edge, then an optional
        *trailing* widget (e.g. a download button). So all fields start at the
        same x, and a row without a trailing widget lengthens the field by
        exactly that button's width — left and right edges line up. Returns
        ``(row, entry)``.
        """
        row = Adw.PreferencesRow(activatable=False)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12,
                      margin_start=12, margin_end=12, margin_top=10, margin_bottom=10)
        box.append(Gtk.Label(label=title, xalign=0.0, valign=Gtk.Align.CENTER,
                             width_request=self._LABEL_WIDTH))
        entry = Gtk.Entry(hexpand=True, valign=Gtk.Align.CENTER)
        entry.set_text(self._settings.get(key, "") or "")
        value = config.default(key)
        if value:
            entry.set_placeholder_text(f"{value} (default)")
        entry.connect("changed", self._on_entry_setting, key)
        box.append(entry)
        if trailing is not None:
            trailing.set_valign(Gtk.Align.CENTER)
            box.append(trailing)
        row.set_child(box)
        return row, entry

    def _nav_row(self, title, subtitle, subpage):
        """An activatable row with a chevron that pushes *subpage*."""
        row = Adw.ActionRow(title=title, subtitle=subtitle, activatable=True)
        row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        row.connect("activated", lambda *_: self.push_subpage(subpage))
        return row

    @staticmethod
    def _subpage(title, page):
        """Wrap an ``Adw.PreferencesPage`` as a pushable navigation subpage.

        The page needs its own header bar for the back button — an
        ``Adw.PreferencesPage`` has none — so put it in a toolbar view. The
        ``Adw.HeaderBar`` auto-shows a back button inside the navigation stack.
        """
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        toolbar.set_content(page)
        return Adw.NavigationPage(title=title, child=toolbar)

    def _build_embedding_subpage(self):
        """Backend detail: the local (ONNX) model and the Ollama alternative.
        The overview's Backend combo greys whichever set is inactive."""
        page = Adw.PreferencesPage(title="Embedding")

        local = Adw.PreferencesGroup(
            title="Local (ONNX)",
            description=(
                "Runs in-process, nothing leaves your machine. Recommended "
                "model: paraphrase-multilingual-MiniLM-L12-v2 (multilingual) — "
                "the default URLs below point at it. English-only notes can use "
                "a smaller English model for a speed-up."
            ),
        )
        page.add(local)

        # ONNX model + tokenizer: paste a URL and download into the model folder
        # with a live progress bar.  The download runs in a background thread;
        # the backend picks the files up from the folder on restart.
        model_btn = Gtk.Button(
            icon_name="folder-download-symbolic", valign=Gtk.Align.CENTER)
        model_btn.add_css_class("flat")
        model_btn.set_tooltip_text("Download model.onnx")
        model_btn.connect("clicked", self._on_download_onnx, "model")
        self._sem_model_url_row, self._sem_model_url_entry = self._entry_row(
            "Model URL", "semantic_onnx_model_url", trailing=model_btn)
        self._sem_model_dl_btn = model_btn
        local.add(self._sem_model_url_row)

        self._sem_model_progress = Gtk.ProgressBar(
            show_text=True, visible=False,
            margin_start=12, margin_end=12, margin_bottom=6)
        local.add(self._sem_model_progress)

        tok_btn = Gtk.Button(
            icon_name="folder-download-symbolic", valign=Gtk.Align.CENTER)
        tok_btn.add_css_class("flat")
        tok_btn.set_tooltip_text("Download tokenizer.json")
        tok_btn.connect("clicked", self._on_download_onnx, "tokenizer")
        self._sem_tok_url_row, self._sem_tok_url_entry = self._entry_row(
            "Tokenizer URL", "semantic_onnx_tokenizer_url", trailing=tok_btn)
        self._sem_tok_dl_btn = tok_btn
        local.add(self._sem_tok_url_row)

        self._sem_tok_progress = Gtk.ProgressBar(
            show_text=True, visible=False,
            margin_start=12, margin_end=12, margin_bottom=6)
        local.add(self._sem_tok_progress)

        # Folder the ONNX files live in — both the download target and the load
        # source. Display-only (no typing): pick a folder or reset to the app
        # data dir default. Changing it refreshes the presence indicator below.
        self._sem_onnx_dir_row = Adw.ActionRow(title="Model folder")
        pick_btn = Gtk.Button(icon_name="folder-open-symbolic",
                              valign=Gtk.Align.CENTER, tooltip_text="Choose folder…")
        pick_btn.add_css_class("flat")
        pick_btn.connect("clicked", lambda *_: self._choose_onnx_dir())
        reset_btn = Gtk.Button(icon_name="edit-clear-symbolic",
                               valign=Gtk.Align.CENTER, tooltip_text="Reset to default")
        reset_btn.add_css_class("flat")
        reset_btn.connect("clicked", lambda *_: self._on_onnx_dir_selected(""))
        self._sem_onnx_dir_row.add_suffix(reset_btn)
        self._sem_onnx_dir_row.add_suffix(pick_btn)
        self._sem_onnx_dir_row.set_activatable_widget(pick_btn)
        self._refresh_onnx_dir_row()
        local.add(self._sem_onnx_dir_row)

        # Presence indicator for the ONNX files + a real load/embed self-test.
        self._sem_onnx_status_row = Adw.ActionRow(title="Model files")
        self._sem_onnx_status_icon = Gtk.Image()
        self._sem_onnx_status_row.add_prefix(self._sem_onnx_status_icon)
        self._sem_onnx_test_btn = Gtk.Button(label="Test", valign=Gtk.Align.CENTER)
        self._sem_onnx_test_btn.connect("clicked", self._on_test_onnx)
        self._sem_onnx_status_row.add_suffix(self._sem_onnx_test_btn)
        self._sem_onnx_status_row.set_activatable_widget(self._sem_onnx_test_btn)
        local.add(self._sem_onnx_status_row)

        # Detected onnxruntime version + recommendation (probed off-thread so
        # importing the heavy library never blocks opening Preferences).
        self._sem_onnx_runtime_row = Adw.ActionRow(
            title="ONNX runtime", subtitle="Checking…")
        local.add(self._sem_onnx_runtime_row)

        # Collapsible guidance for choosing a model yourself.
        self._sem_onnx_help_row = Adw.ExpanderRow(
            title="How to pick your own ONNX model",
            subtitle="What to search for and which files you need",
        )
        help_label = Gtk.Label(
            label=(
                "Semantic search needs a sentence-embedding model exported to "
                "ONNX. Find one on Hugging Face and paste each file's URL above "
                "(use the file's “Copy download link”, i.e. the "
                ".../resolve/main/... address).\n\n"
                "What to look for:\n"
                "•  A feature-extraction / sentence-similarity model in ONNX "
                "format — e.g. the Xenova/ or sentence-transformers/ orgs. "
                "Xenova repos ship ready ONNX exports in an onnx/ folder.\n"
                "•  Two files: model.onnx (or model_quantized.onnx — smaller and "
                "faster, slightly lower quality) and tokenizer.json.\n"
                "•  Multilingual if your notes aren't English-only (e.g. "
                "paraphrase-multilingual-MiniLM-L12-v2); English-only models are "
                "smaller and faster.\n"
                "•  It must output token embeddings (the app mean-pools them). "
                "Classification or reranker models won't work.\n\n"
                "Good search terms: “sentence-transformers onnx”, “Xenova "
                "MiniLM”, “feature-extraction onnx”. Both BERT- and XLM-R-style "
                "models work — the app auto-detects the inputs a model needs."
            ),
            wrap=True, xalign=0.0,
            margin_top=6, margin_bottom=12, margin_start=12, margin_end=12,
        )
        help_label.add_css_class("dim-label")
        self._sem_onnx_help_row.add_row(help_label)
        local.add(self._sem_onnx_help_row)

        ollama = Adw.PreferencesGroup(
            title="Ollama (server)",
            description="Alternative backend if you already run an Ollama "
                        "server. Recommended embedding model: nomic-embed-text.")
        page.add(ollama)

        self._sem_url_row, self._sem_url_entry = self._entry_row(
            "Ollama URL", "semantic_ollama_url")
        ollama.add(self._sem_url_row)

        self._sem_model_row, self._sem_model_entry = self._entry_row(
            "Embedding model", "semantic_ollama_model")
        ollama.add(self._sem_model_row)

        self._sem_ollama_test_row = Adw.ActionRow(
            title="Test connection",
            subtitle="Embed a probe with the current URL + model")
        self._sem_ollama_test_btn = Gtk.Button(label="Test", valign=Gtk.Align.CENTER)
        self._sem_ollama_test_btn.connect("clicked", self._on_test_ollama)
        self._sem_ollama_test_row.add_suffix(self._sem_ollama_test_btn)
        self._sem_ollama_test_row.set_activatable_widget(self._sem_ollama_test_btn)
        ollama.add(self._sem_ollama_test_row)

        # Grey out the group the selected backend does not use.
        self._sem_onnx_widgets = [local]
        self._sem_ollama_widgets = [ollama]
        subpage = self._subpage("Embedding", page)
        # Don't auto-focus the first entry: an empty (default) field would open
        # with a focus ring + placeholder, which looks half-filled. Let the
        # resting state show the plain title; the hint appears on click.
        subpage.connect("shown", lambda *_: self.set_focus(None))
        return subpage

    def _build_ask_subpage(self):
        """Chat model that writes grounded answers, plus a link to its prompt."""
        page = Adw.PreferencesPage(title="Ask")
        group = Adw.PreferencesGroup(
            title="Ask (answers from your notes)",
            description="The quick-open 'ask' mode answers from your notes via a "
                        "local chat model. Backend: Ollama, or an OpenAI-compatible "
                        "server like llama.cpp. Recommended: llama3.2 — fast and "
                        "CPU-friendly; a strong model (e.g. Qwen3) answers far "
                        "better where the hardware allows.")
        page.add(group)

        self._ask_backends = ["ollama", "openai"]
        self._ask_backend_row = Adw.ComboRow(
            title="Backend",
            model=Gtk.StringList.new(
                ["Ollama (/api/chat)", "OpenAI-compatible — llama.cpp (/v1)"]))
        b = self._settings.get("ask_backend", "ollama")
        self._ask_backend_row.set_selected(
            self._ask_backends.index(b) if b in self._ask_backends else 0)
        self._ask_backend_row.connect("notify::selected", self._on_ask_backend_changed)
        group.add(self._ask_backend_row)

        self._ask_url_row, self._ask_url_entry = self._entry_row(
            "Server URL", "ask_ollama_url")
        # Hint the saved backend's port immediately (not only on a later switch).
        saved_url = self._ASK_BACKEND_URLS.get(self._settings.get("ask_backend"))
        if saved_url:
            self._ask_url_entry.set_placeholder_text(f"{saved_url} (default)")
        group.add(self._ask_url_row)

        # The model list is fetched from the server; a refresh icon sits in the
        # row next to the selected model, and the subtitle carries the status
        # (count, "Loading…", or an unreachable-server error).
        self._ask_model_combo = Adw.ComboRow(
            title="Model", subtitle="Fetched from the server")
        self._ask_model_list = Gtk.StringList()
        self._ask_model_combo.set_model(self._ask_model_list)
        self._ask_model_combo.connect("notify::selected", self._on_ask_model_selected)
        ask_refresh_btn = Gtk.Button(
            icon_name="view-refresh-symbolic", valign=Gtk.Align.CENTER,
            tooltip_text="Refresh model list")
        ask_refresh_btn.add_css_class("flat")
        ask_refresh_btn.connect("clicked", lambda *_: self._refresh_ask_models())
        self._ask_model_combo.add_suffix(ask_refresh_btn)
        group.add(self._ask_model_combo)

        self._ask_reasoning_row = Adw.SwitchRow(
            title="Reasoning",
            subtitle="Let a reasoning model (Qwen3, …) think before answering — "
                     "more accurate but much slower. Off is faster and usually "
                     "enough for grounded note answers.")
        self._ask_reasoning_row.set_active(self._settings.get("ask_reasoning", True))
        self._ask_reasoning_row.connect(
            "notify::active", self._on_toggle_setting, "ask_reasoning")
        group.add(self._ask_reasoning_row)

        self._ask_hybrid_row = Adw.SwitchRow(
            title="Hybrid retrieval",
            subtitle="Fuse a keyword (BM25) ranking into the semantic search so "
                     "exact tokens — names, config keys, shortcuts — that "
                     "embeddings blur still surface. Helps most on large vaults.")
        self._ask_hybrid_row.set_active(self._settings.get("ask_hybrid", True))
        self._ask_hybrid_row.connect(
            "notify::active", self._on_toggle_setting, "ask_hybrid")
        group.add(self._ask_hybrid_row)

        self._ask_topk_row = Adw.SpinRow(
            title="Context notes",
            subtitle="How many notes are sent to the model as context. On CPU "
                     "the model spends almost all its time reading them, so fewer "
                     "= much faster (roughly linear). Recommended: 10 on a GPU, "
                     "~5 on a slow CPU.",
            adjustment=Gtk.Adjustment.new(
                self._settings.get("ask_top_k", 10), 3, 20, 1, 5, 0.0),
            digits=0,
        )
        self._ask_topk_row.connect("notify::value", self._on_ask_top_k_changed)
        group.add(self._ask_topk_row)

        self._ask_ctx_row = Adw.SpinRow(
            title="Context window",
            subtitle="Tokens sent to Ollama. Its default (2048) truncates "
                     "multi-note answers; higher fits more/longer notes but uses "
                     "more memory. Not used by the llama.cpp backend.",
            adjustment=Gtk.Adjustment.new(
                self._settings.get("ask_num_ctx", 8192),
                2048, 32768, 1024, 4096, 0.0),
            digits=0,
        )
        self._ask_ctx_row.connect("notify::value", self._on_ask_num_ctx_changed)
        group.add(self._ask_ctx_row)

        group.add(self._nav_row(
            "System prompt", "Grounding instructions sent to the model",
            self._prompt_subpage))
        subpage = self._subpage("Ask", page)
        subpage.connect("shown", lambda *_: self.set_focus(None))  # see Embedding
        return subpage

    def _build_prompt_subpage(self, _ask):
        """The editable system prompt with a reset-to-default action."""
        page = Adw.PreferencesPage(title="System prompt")
        group = Adw.PreferencesGroup(
            title="System prompt",
            description="Grounding instructions sent to the model. {language} is "
                        "replaced with the answer language. Reset restores the "
                        "built-in default and keeps tracking future improvements.")
        reset_btn = Gtk.Button(label="Reset", valign=Gtk.Align.CENTER)
        reset_btn.set_tooltip_text("Reset to the built-in default prompt")
        reset_btn.connect(
            "clicked",
            lambda *_: self._ask_prompt_view.get_buffer().set_text(_ask.DEFAULT_SYSTEM_PROMPT))
        group.set_header_suffix(reset_btn)
        page.add(group)

        self._ask_prompt_view = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR)
        for m in (self._ask_prompt_view.set_top_margin, self._ask_prompt_view.set_bottom_margin,
                  self._ask_prompt_view.set_left_margin, self._ask_prompt_view.set_right_margin):
            m(6)
        self._ask_prompt_view.get_buffer().set_text(
            self._settings.get("ask_system_prompt") or _ask.DEFAULT_SYSTEM_PROMPT)
        self._ask_prompt_view.get_buffer().connect("changed", self._on_ask_prompt_changed)
        prompt_scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER, min_content_height=300,
            vexpand=True, margin_bottom=24)
        prompt_scroll.add_css_class("card")
        prompt_scroll.set_child(self._ask_prompt_view)
        group.add(prompt_scroll)
        return self._subpage("System prompt", page)

    # ── Handlers ────────────────────────────────────────────────────

    def _on_toggle_setting(self, row, _pspec, key) -> None:
        self._settings[key] = row.get_active()
        self._persist()

    def _on_entry_setting(self, row, key) -> None:
        self._settings[key] = row.get_text().strip()
        self._persist_debounced()

    def _on_ask_prompt_changed(self, buffer) -> None:
        from . import ask as _ask
        text = buffer.get_text(
            buffer.get_start_iter(), buffer.get_end_iter(), False)
        # Store empty when unchanged from the built-in default, so the prompt
        # keeps tracking future improvements instead of pinning this snapshot.
        self._settings["ask_system_prompt"] = (
            "" if text.strip() == _ask.DEFAULT_SYSTEM_PROMPT.strip() else text)
        self._persist_debounced()

    def _on_ask_model_selected(self, combo, _pspec) -> None:
        item = combo.get_selected_item()
        if item is not None:
            self._settings["ask_model"] = item.get_string()
            self._persist_debounced()

    # Typical server URL per backend — llama.cpp serves :8080, Ollama :11434.
    _ASK_BACKEND_URLS = {"ollama": "http://localhost:11434",
                         "openai": "http://localhost:8080"}

    def _on_ask_backend_changed(self, row, _pspec) -> None:
        backend = self._ask_backends[row.get_selected()]
        self._settings["ask_backend"] = backend
        # Point the URL hint (and an empty/other-default value) at the new
        # backend's port, so switching to llama.cpp doesn't silently keep :11434.
        default_url = self._ASK_BACKEND_URLS.get(backend)
        if default_url:
            self._ask_url_entry.set_placeholder_text(f"{default_url} (default)")
            cur = self._ask_url_entry.get_text().strip()
            if not cur or cur in self._ASK_BACKEND_URLS.values():
                self._ask_url_entry.set_text(default_url)  # fires changed → saves
        self._persist()
        self._refresh_ask_models()  # different endpoint per backend

    def _refresh_ask_models(self) -> None:
        """Fetch the model list off the main thread — from Ollama's /api/tags or
        the OpenAI-compatible /v1/models, depending on the selected backend."""
        import json
        import urllib.request
        url = (self._settings.get("ask_ollama_url")
               or config.default("ask_ollama_url")).rstrip("/")
        openai = self._settings.get("ask_backend") == "openai"
        endpoint = "/v1/models" if openai else "/api/tags"
        self._ask_model_combo.set_subtitle("Loading…")

        def worker():
            try:
                req = urllib.request.Request(url + endpoint)
                data = json.loads(urllib.request.urlopen(req, timeout=6).read())
                # Ollama: {"models":[{"name":…}]}; OpenAI/llama.cpp:
                # {"data":[{"id":…}]} or {"models":[{"name"/"id":…}]}.
                items = data.get("models") or data.get("data") or []
                models = [m.get("name") or m.get("id") for m in items
                          if (m.get("name") or m.get("id"))]
                GLib.idle_add(self._populate_ask_models, models, None)
            except Exception as exc:  # noqa: BLE001 — surface any failure inline
                GLib.idle_add(self._populate_ask_models, None, str(exc))

        threading.Thread(target=worker, daemon=True).start()

    def _populate_ask_models(self, models, error) -> bool:
        if error is not None:
            self._ask_model_combo.set_subtitle(f"Not reachable: {error}")
            self._ask_model_combo.add_css_class("error")
            return False
        self._ask_model_combo.remove_css_class("error")
        if not models:
            self._ask_model_combo.set_subtitle("No models on the server")
            return False
        current = self._settings.get("ask_model") or config.default("ask_model")
        if current and current not in models:
            models = [current] + models  # keep the saved choice selectable
        self._ask_model_list.splice(0, self._ask_model_list.get_n_items(), models)
        try:
            self._ask_model_combo.set_selected(models.index(current))
        except ValueError:
            self._ask_model_combo.set_selected(0)
        self._ask_model_combo.set_subtitle(f"{len(models)} models")
        return False

    def _refresh_onnx_dir_row(self) -> None:
        """Show the active ONNX folder (and mark when it's the default)."""
        d = self._onnx_dir()
        default = config.STATE_DIR / "onnx"
        suffix = "  (default)" if d == default else ""
        self._sem_onnx_dir_row.set_subtitle(str(d) + suffix)

    def _choose_onnx_dir(self) -> None:
        """Folder chooser for the ONNX directory, opening at the current one."""
        dialog = Gtk.FileDialog(title="Select ONNX model folder")
        start = self._onnx_dir()
        try:
            probe = start if start.exists() else start.parent
            if probe.exists():
                dialog.set_initial_folder(Gio.File.new_for_path(str(probe)))
        except Exception:  # noqa: BLE001
            pass

        def done(dlg, result):
            try:
                gfile = dlg.select_folder_finish(result)
            except GLib.Error:
                return  # cancelled or failed
            if gfile is not None and gfile.get_path():
                self._on_onnx_dir_selected(gfile.get_path())

        dialog.select_folder(self.get_root(), None, done)

    def _on_sem_backend_changed(self, row, _pspec) -> None:
        self._settings["semantic_backend"] = self._sem_backends[row.get_selected()]
        self._persist()
        self._update_sem_backend_sensitivity()

    def _sem_backend_index(self) -> int:
        """Selected-row index for the persisted backend, tolerant of an unknown
        value (hand-edited YAML, a future backend) so __init__ never raises and
        the whole dialog fails to open."""
        backend = self._settings.get("semantic_backend", self._SEM_BACKEND_DEFAULT)
        return self._sem_backends.index(backend) if backend in self._sem_backends else 0

    def _update_sem_backend_sensitivity(self) -> None:
        """Grey out the rows the selected backend does not use."""
        onnx = self._settings.get(
            "semantic_backend", self._SEM_BACKEND_DEFAULT) == "onnx"
        for w in self._sem_onnx_widgets:
            w.set_sensitive(onnx)
        for w in self._sem_ollama_widgets:
            w.set_sensitive(not onnx)

    def _on_rebuild_index(self, _button) -> None:
        if self._on_reindex is None:
            return
        if not self._settings.get("semantic_search_enabled"):
            msg = "Enable semantic search first"
        else:
            self._on_reindex()
            msg = "Rebuilding semantic index in the background…"
        try:
            self.add_toast(Adw.Toast.new(msg))
        except Exception:
            logger.info("%s", msg)

    # ── ONNX runtime probe ────────────────────────────────────────

    # Sentence-transformer exports (opset ~14) load on any recent onnxruntime.
    _ONNX_RUNTIME_RECOMMENDED = "1.16"

    def _probe_onnx_runtime(self) -> None:
        try:
            import onnxruntime
            ver = onnxruntime.__version__
            line = (f"onnxruntime {ver} detected — recommended "
                    f"≥ {self._ONNX_RUNTIME_RECOMMENDED} for current models")
        except Exception:
            line = ("onnxruntime not found — install it (openSUSE: "
                    "python313-onnxruntime) or use the Flatpak build")
        GLib.idle_add(self._sem_onnx_runtime_row.set_subtitle, line)

    # ── Backend connection / model self-tests ─────────────────────

    def _onnx_dir(self) -> Path:
        """The folder the backend loads model.onnx + tokenizer.json from (and the
        download writes to). Blank setting → the app data dir default."""
        return Path(self._settings.get("semantic_onnx_dir")
                    or str(config.STATE_DIR / "onnx"))

    def _onnx_paths(self):
        """Resolve the ONNX model + tokenizer file paths inside the folder."""
        d = self._onnx_dir()
        return d / "model.onnx", d / "tokenizer.json"

    def _on_onnx_dir_selected(self, path: str) -> None:
        self._settings["semantic_onnx_dir"] = path
        self._persist_debounced()
        self._refresh_onnx_dir_row()
        self._refresh_onnx_status()  # download target + presence follow the folder

    def _refresh_onnx_status(self) -> None:
        """Update the model-files indicator (present / missing + sizes)."""
        model_p, tok_p = self._onnx_paths()

        def describe(p):
            if p.exists():
                return True, f"{p.name} ({p.stat().st_size / 1024 / 1024:.0f} MB)"
            return False, f"{p.name} missing"

        m_ok, m_txt = describe(model_p)
        t_ok, t_txt = describe(tok_p)
        both = m_ok and t_ok
        self._sem_onnx_status_row.set_subtitle(f"{m_txt}  ·  {t_txt}")
        # object-select-symbolic (checkmark) is reliably present; emblem-ok is not.
        self._sem_onnx_status_icon.set_from_icon_name(
            "object-select-symbolic" if both else "dialog-warning-symbolic")
        self._sem_onnx_status_icon.remove_css_class("success")
        self._sem_onnx_status_icon.remove_css_class("warning")
        self._sem_onnx_status_icon.add_css_class("success" if both else "warning")
        self._sem_onnx_test_btn.set_sensitive(both)

    def _on_test_ollama(self, button) -> None:
        url = (self._sem_url_entry.get_text().strip()
               or config.default("semantic_ollama_url"))
        model = (self._sem_model_entry.get_text().strip()
                 or config.default("semantic_ollama_model"))
        button.set_sensitive(False)
        self._sem_ollama_test_row.set_subtitle("Testing…")
        threading.Thread(
            target=self._test_ollama_worker, args=(button, url, model),
            daemon=True).start()

    def _test_ollama_worker(self, button, url, model) -> None:
        try:
            from .semantic_search import OllamaEmbedder
            vec = OllamaEmbedder(model, url).embed(["connection test"], is_query=True)
            dim = len(vec[0]) if vec else 0
            ok, msg = True, f"Connected — {model} OK (dim {dim})"
        except Exception as exc:
            ok, msg = False, f"Failed: {exc}"
            logger.info("Ollama test failed: %s", exc)
        GLib.idle_add(self._test_done, button, self._sem_ollama_test_row, ok, msg)

    def _on_test_onnx(self, button) -> None:
        model_p, tok_p = self._onnx_paths()
        button.set_sensitive(False)
        self._sem_onnx_status_row.set_subtitle("Loading model + embedding a probe…")
        threading.Thread(
            target=self._test_onnx_worker,
            args=(button, str(model_p), str(tok_p)), daemon=True).start()

    def _test_onnx_worker(self, button, model_path, tok_path) -> None:
        try:
            from .semantic_search import OnnxEmbedder
            vec = OnnxEmbedder(model_path, tok_path).embed(["probe"])
            dim = len(vec[0]) if vec else 0
            ok, msg = True, f"Model loads and embeds OK (dim {dim})"
        except Exception as exc:
            ok, msg = False, f"Failed: {exc}"
            logger.info("ONNX test failed: %s", exc)
        GLib.idle_add(self._test_onnx_done, button, ok, msg)

    def _test_onnx_done(self, button, ok, msg) -> bool:
        button.set_sensitive(True)
        try:
            self.add_toast(Adw.Toast.new(msg))
        except Exception:
            logger.info("%s", msg)
        # Restore the presence line (the test message was transient).
        self._refresh_onnx_status()
        if not ok:  # keep the failure visible in the subtitle too
            self._sem_onnx_status_row.set_subtitle(msg)
        return False

    def _test_done(self, button, row, ok, msg) -> bool:
        button.set_sensitive(True)
        row.set_subtitle(msg)
        try:
            self.add_toast(Adw.Toast.new(msg))
        except Exception:
            logger.info("%s", msg)
        return False

    # ── ONNX model / tokenizer download ────────────────────────────

    def _on_download_onnx(self, button, which) -> None:
        model_p, tok_p = self._onnx_paths()
        if which == "model":
            url = (self._sem_model_url_entry.get_text().strip()
                   or config.default("semantic_onnx_model_url"))
            filename, bar, target = "model.onnx", self._sem_model_progress, model_p
        else:
            url = (self._sem_tok_url_entry.get_text().strip()
                   or config.default("semantic_onnx_tokenizer_url"))
            filename, bar, target = "tokenizer.json", self._sem_tok_progress, tok_p
        if not url:
            return
        button.set_sensitive(False)
        bar.set_visible(True)
        bar.set_fraction(0.0)
        bar.set_text("Starting…")
        # Download to the path the backend actually loads (_onnx_paths), not a
        # fixed dir — otherwise a custom configured path never sees the file.
        threading.Thread(
            target=self._download_worker,
            args=(button, url, target, filename, bar), daemon=True).start()

    def _download_worker(self, button, url, target, filename, bar) -> None:
        import urllib.request
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            req = urllib.request.Request(
                url, headers={"User-Agent": "markdown-vault"})
            tmp = target.with_name(target.name + ".part")
            with urllib.request.urlopen(req, timeout=30) as resp:
                total = int(resp.headers.get("Content-Length") or 0)
                done = last = 0
                with open(tmp, "wb") as fh:
                    while True:
                        buf = resp.read(65536)
                        if not buf:
                            break
                        fh.write(buf)
                        done += len(buf)
                        if done - last >= 1024 * 1024:  # throttle to ~1 MB
                            last = done
                            GLib.idle_add(self._download_progress, bar, done, total)
            tmp.replace(target)
            mb = target.stat().st_size / 1024 / 1024
            GLib.idle_add(
                self._download_done, button, bar, True,
                f"Downloaded {filename} ({mb:.0f} MB) — restart to use")
        except Exception as exc:  # network/IO/permission — report, don't crash
            logger.warning("ONNX download failed: %s", exc)
            GLib.idle_add(
                self._download_done, button, bar, False, f"Failed: {exc}")

    def _download_progress(self, bar, done, total) -> bool:
        mb = done / 1024 / 1024
        if total > 0:
            frac = done / total
            bar.set_fraction(min(frac, 1.0))
            bar.set_text(f"{mb:.0f} / {total / 1024 / 1024:.0f} MB ({frac * 100:.0f}%)")
        else:  # server sent no Content-Length → indeterminate
            bar.pulse()
            bar.set_text(f"{mb:.0f} MB")
        return False

    def _download_done(self, button, bar, ok, msg) -> bool:
        button.set_sensitive(True)
        if ok:
            bar.set_fraction(1.0)
        bar.set_text(msg)
        try:
            self.add_toast(Adw.Toast.new(msg))
        except Exception:
            logger.info("%s", msg)
        self._refresh_onnx_status()  # a fetched file flips the indicator
        return False

    def _on_min_score_changed(self, _row, _pspec) -> None:
        self._settings["semantic_min_score"] = round(
            self._sem_score_row.get_adjustment().get_value(), 2)
        self._persist()

    def _on_ask_num_ctx_changed(self, _row, _pspec) -> None:
        self._settings["ask_num_ctx"] = int(
            self._ask_ctx_row.get_adjustment().get_value())
        self._persist()

    def _on_ask_top_k_changed(self, _row, _pspec) -> None:
        self._settings["ask_top_k"] = int(
            self._ask_topk_row.get_adjustment().get_value())
        self._persist()

    _PERSIST_DEBOUNCE_MS = 600

    def _persist_debounced(self) -> None:
        """Coalesce rapid text edits into one disk write."""
        if self._persist_id is not None:
            GLib.source_remove(self._persist_id)
        self._persist_id = GLib.timeout_add(
            self._PERSIST_DEBOUNCE_MS, self._persist_now)

    def _persist_now(self) -> bool:
        self._persist_id = None
        self._persist()
        return False

    def _flush_persist(self, *_args) -> None:
        """Write any pending debounced change immediately (on dialog close)."""
        if self._persist_id is not None:
            GLib.source_remove(self._persist_id)
            self._persist_id = None
            self._persist()

    def _persist(self) -> None:
        try:
            config.save_settings(self._settings)
        except OSError as e:
            import logging
            logging.getLogger(__name__).error("Failed to save settings: %s", e)
            dialogs.show_error(self.get_root(), "Save Failed", str(e))
            return
        self.emit("settings-changed")

    def _on_autosave_changed(self, _row: Adw.SpinRow, _pspec) -> None:
        self._settings["autosave_interval"] = int(self._autosave_row.get_adjustment().get_value())
        self._persist()

    def _on_view_mode_changed(self, row: Adw.ComboRow, _pspec) -> None:
        modes = list(_VIEW_MODES.keys())
        idx = row.get_selected()
        if idx < len(modes):
            self._settings["default_view_mode"] = modes[idx]
            self._persist()

    def _on_font_size_changed(self, _row: Adw.SpinRow, _pspec) -> None:
        self._settings["editor_font_size"] = int(self._font_row.get_adjustment().get_value())
        self._persist()

    def _on_tab_width_changed(self, _row: Adw.SpinRow, _pspec) -> None:
        self._settings["editor_tab_width"] = int(self._tab_row.get_adjustment().get_value())
        self._persist()

    def _on_wrap_changed(self, switch: Gtk.Switch, _pspec) -> None:
        self._settings["editor_wrap_text"] = switch.get_active()
        self._persist()

    def _on_tab_min_width_changed(self, _row: Adw.SpinRow, _pspec) -> None:
        self._settings["tab_min_width"] = int(self._tab_width_row.get_adjustment().get_value())
        self._persist()

    def _on_tab_wrap_changed(self, switch: Gtk.Switch, _pspec) -> None:
        self._settings["tab_wrap"] = switch.get_active()
        self._persist()

    def _on_zoom_changed(self, _row: Adw.SpinRow, _pspec) -> None:
        self._settings["preview_zoom"] = round(self._zoom_row.get_adjustment().get_value(), 2)
        self._persist()

    def _on_remote_images_changed(self, switch: Adw.SwitchRow, _pspec) -> None:
        self._settings["preview_allow_remote_images"] = switch.get_active()
        self._persist()

    def _on_glib_loglevel_changed(self, row: Adw.ComboRow, _pspec) -> None:
        levels = list(_GLIB_LOGLEVELS.keys())
        idx = row.get_selected()
        if idx < len(levels):
            self._settings["glib_loglevel"] = levels[idx]
            self._persist()
            # Notify caller to reconfigure GLib logging (live).
            if self._glib_loglevel_callback:
                self._glib_loglevel_callback(levels[idx])

    # ── Keybinding capture ──────────────────────────────────────────

    def _setup_keybinding_button(
        self, button: Gtk.Button, setting_key: str,
    ) -> None:
        """Configure *button* to capture and display a keyboard shortcut."""
        button._setting_key = setting_key  # type: ignore[attr-defined]
        button._capturing = False  # type: ignore[attr-defined]
        button._key_controller = None  # type: ignore[attr-defined]
        self._update_keybinding_button(button)
        button.connect("clicked", self._on_keybinding_clicked)

    def _update_keybinding_button(self, button: Gtk.Button) -> None:
        accel = self._settings.get(button._setting_key, "")
        button.set_label(_accel_to_label(accel))

    def _on_keybinding_clicked(self, button: Gtk.Button) -> None:
        if button._capturing:
            return
        button._capturing = True
        button.set_label("Press shortcut...")
        ctrl = Gtk.EventControllerKey()
        ctrl.connect("key-pressed", self._on_keybinding_key_pressed, button)
        button.add_controller(ctrl)
        button._key_controller = ctrl

    def _on_keybinding_key_pressed(
        self, _ctrl, keyval: int, _keycode: int, state: int, button: Gtk.Button,
    ) -> bool:
        button._capturing = False
        if button._key_controller:
            button.remove_controller(button._key_controller)
            button._key_controller = None

        # Escape cancels.
        if keyval == Gdk.KEY_Escape:
            self._update_keybinding_button(button)
            return True

        # Ignore bare modifier presses.
        if keyval in (Gdk.KEY_Shift_L, Gdk.KEY_Shift_R,
                       Gdk.KEY_Control_L, Gdk.KEY_Control_R,
                       Gdk.KEY_Alt_L, Gdk.KEY_Alt_R,
                       Gdk.KEY_Super_L, Gdk.KEY_Super_R):
            self._update_keybinding_button(button)
            return True

        state &= _RELEVANT_MODS
        accel = Gtk.accelerator_name(keyval, state)
        self._settings[button._setting_key] = accel
        self._persist()
        self._update_keybinding_button(button)
        return True

    def _on_tab_switch_mode_changed(self, row: Adw.ComboRow, _pspec) -> None:
        self._settings["tab_switch_mode"] = "mru" if row.get_selected() == 0 else "cycle"
        self._persist()

    def _on_loglevel_changed(self, row: Adw.ComboRow, _pspec) -> None:
        levels = list(_LOGLEVELS.keys())
        idx = row.get_selected()
        if idx < len(levels):
            self._settings["loglevel"] = levels[idx]
            self._persist()
            level = _LOGLEVEL_MAP[levels[idx]]
            logging.getLogger().setLevel(level)
            logging.getLogger("markdown-vault").setLevel(level)

    def _on_tp_loglevel_changed(self, row: Adw.ComboRow, _pspec) -> None:
        from .logging_setup import set_third_party_loglevel
        levels = list(_LOGLEVELS.keys())
        idx = row.get_selected()
        if idx < len(levels):
            self._settings["third_party_loglevel"] = levels[idx]
            self._persist()
            set_third_party_loglevel(levels[idx])

    def _on_dump_toggle_changed(self, row: Adw.SwitchRow, _pspec, key: str) -> None:
        self._settings[f"debug_dump_{key}"] = row.get_active()
        self._persist()

    def _on_webkit_toggle_changed(
        self, row: Adw.SwitchRow, _pspec, key: str,
    ) -> None:
        self._settings[key] = row.get_active()
        self._persist()

    def _on_wikilink_toggle_changed(
        self, row: Adw.SwitchRow, _pspec, key: str,
    ) -> None:
        self._settings[key] = row.get_active()
        self._persist()
