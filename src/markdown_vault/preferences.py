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

from markdown_vault.core import config
from markdown_vault.uikit import dialogs


class _HttpsOnlyRedirect:
    """A urllib redirect handler that refuses to leave HTTPS. A model URL that
    redirects to http/ftp would deliver an unauthenticated file straight into a
    native parser (llama.cpp's GGUF loader, ONNX Runtime's protobuf) — a
    memory-safety surface, not a mere parse error. Instantiated lazily so the
    urllib import stays local to the download path."""

    def __new__(cls):
        import urllib.request
        import urllib.error
        from urllib.parse import urlparse

        class _Handler(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                if urlparse(newurl).scheme != "https":
                    raise urllib.error.HTTPError(
                        newurl, code, "refusing a non-HTTPS redirect", headers, fp)
                return super().redirect_request(req, fp, code, msg, headers, newurl)

        return _Handler()


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
        from markdown_vault.search import ask as _ask
        self._emb_subpage = self._build_embedding_subpage()
        self._prompt_subpage = self._build_prompt_subpage(_ask)
        self._runtime_subpage = self._build_runtime_subpage()
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

        # ── Audio transcription (used by the document importer's File tab) ──
        audio_group = Adw.PreferencesGroup(
            title="Audio transcription",
            description=("Turn audio files into text when you import them. Pick a "
                         "model size and download it once (bigger = more accurate, "
                         "slower, larger)."))
        # Model picker: the multilingual sizes (one model handles ~99 languages,
        # auto-detected). Bigger = more accurate but slower and a larger download.
        whisper_models = [
            ("tiny", "tiny (~75 MB)"),
            ("base", "base (~140 MB)"),
            ("small", "small (~460 MB)"),
            ("medium", "medium (~1.5 GB)"),
            ("large-v3", "large-v3 (~3 GB)"),
        ]
        self._whisper_values = [v for v, _ in whisper_models]
        self._whisper_model_row = Adw.ComboRow(
            title="Model",
            subtitle="Multilingual; bigger = more accurate, slower, larger download",
            model=Gtk.StringList.new([label for _, label in whisper_models]))
        current = (self._settings.get("document_whisper_model")
                   or config.default("document_whisper_model"))
        self._whisper_model_row.set_selected(
            self._whisper_values.index(current) if current in self._whisper_values
            else self._whisper_values.index("base"))
        self._whisper_model_row.connect("notify::selected",
                                        self._on_whisper_model_changed)
        audio_group.add(self._whisper_model_row)

        self._whisper_progress = Gtk.ProgressBar(
            show_text=True, visible=False,
            margin_start=12, margin_end=12, margin_bottom=6)
        audio_group.add(self._whisper_progress)

        self._whisper_row = Adw.ActionRow(title="Model files")
        self._whisper_status_icon = Gtk.Image()
        self._whisper_row.add_prefix(self._whisper_status_icon)
        self._whisper_dl_btn = Gtk.Button(
            icon_name="folder-download-symbolic", valign=Gtk.Align.CENTER,
            tooltip_text="Download the selected model")
        self._whisper_dl_btn.add_css_class("flat")
        self._whisper_dl_btn.connect("clicked", self._on_download_whisper)
        self._whisper_row.add_suffix(self._whisper_dl_btn)
        audio_group.add(self._whisper_row)
        search.add(audio_group)

        self._update_sem_backend_sensitivity()
        self._refresh_onnx_status()
        self._refresh_ask_models()
        self._refresh_whisper_status()
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

    def _caption_row(self, text):
        """A non-interactive row holding a dimmed, wrapping caption — used to note
        a privacy implication next to the control it applies to."""
        row = Adw.PreferencesRow(activatable=False, can_focus=False)
        label = Gtk.Label(label=text, xalign=0.0, wrap=True,
                          margin_start=12, margin_end=12,
                          margin_top=4, margin_bottom=8)
        label.add_css_class("dim-label")
        label.add_css_class("caption")
        row.set_child(label)
        return row

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

    def _build_runtime_subpage(self):
        """GPU/CPU/KV knobs for the in-process backend — on their own page so the
        Ask overview stays light. Only relevant to the Local backend."""
        page = Adw.PreferencesPage(title="Model runtime")
        group = Adw.PreferencesGroup(
            title="Local model runtime",
            description="How the in-process model uses the CPU, GPU and memory.")
        page.add(group)

        self._ask_gpu_row = Adw.SpinRow(
            title="GPU layers",
            subtitle="Layers offloaded to the GPU. 0 = pure CPU, 999 = all.",
            adjustment=Gtk.Adjustment.new(
                self._settings.get("ask_n_gpu_layers", 0), 0, 999, 1, 8, 0.0),
            digits=0)
        self._ask_gpu_row.connect("notify::value", self._on_ask_gpu_layers_changed)
        from markdown_vault.search import llama_runtime
        self._ask_gpu_row.set_visible(llama_runtime.supports_gpu())
        group.add(self._ask_gpu_row)

        self._ask_threads_row = Adw.SpinRow(
            title="CPU threads",
            subtitle="0 = half your physical cores, so the machine stays "
                     "responsive while an answer is generated. More = faster "
                     "answers but can slow the rest of the system; raise it "
                     "gradually.",
            adjustment=Gtk.Adjustment.new(
                self._settings.get("ask_n_threads", 0), 0, 128, 1, 4, 0.0),
            digits=0)
        self._ask_threads_row.connect("notify::value", self._on_ask_threads_changed)
        group.add(self._ask_threads_row)

        # Batch sizes as dropdowns of sensible powers of two (0 = llama.cpp
        # default). n_ubatch is the physical micro-batch — the real prefill-speed
        # lever on the GPU; n_batch only has to stay >= it.
        self._ask_batch_values = [0, 256, 512, 1024, 2048, 4096]

        def _batch_index(setting):
            v = int(self._settings.get(setting, 0) or 0)
            return (self._ask_batch_values.index(v)
                    if v in self._ask_batch_values else 0)

        self._ask_batch_row = Adw.ComboRow(
            title="Prompt batch size",
            subtitle="Logical batch (n_batch). Keep ≥ the micro-batch.",
            model=Gtk.StringList.new(
                ["Default (2048)", "256", "512", "1024", "2048", "4096"]))
        self._ask_batch_row.set_selected(_batch_index("ask_n_batch"))
        self._ask_batch_row.connect("notify::selected", self._on_ask_batch_changed)
        group.add(self._ask_batch_row)

        self._ask_ubatch_row = Adw.ComboRow(
            title="Prompt micro-batch size",
            subtitle="Physical micro-batch (n_ubatch): the GPU prefill-speed "
                     "lever. Must stay ≤ the batch size — larger values are "
                     "greyed out.",
            model=Gtk.StringList.new(
                ["Default (512)", "256", "512", "1024", "2048", "4096"]))
        # Grey (and block) micro-batch values above the chosen batch size, so the
        # dropdown can't produce an n_ubatch > n_batch that llama.cpp would clamp.
        self._refresh_ubatch_factory()
        self._ask_ubatch_row.set_selected(_batch_index("ask_n_ubatch"))
        self._ask_ubatch_row.connect("notify::selected",
                                     self._on_ask_ubatch_changed)
        group.add(self._ask_ubatch_row)

        # Separate K and V cache precision. Quantizing K is free; quantizing V
        # needs flash attention.
        self._ask_kv_types = ["f16", "q8_0", "q4_0"]
        kv_labels = ["f16 — full (default)", "q8_0 — half", "q4_0 — quarter"]
        self._ask_kv_k_row = Adw.ComboRow(
            title="K cache",
            subtitle="Key-cache precision. Quantizing K saves memory without "
                     "needing flash attention.",
            model=Gtk.StringList.new(kv_labels))
        self._ask_kv_k_row.set_selected(self._kv_index("ask_kv_type_k"))
        self._ask_kv_k_row.connect("notify::selected", self._on_ask_kv_k_changed)
        group.add(self._ask_kv_k_row)

        self._ask_kv_v_row = Adw.ComboRow(
            title="V cache", model=Gtk.StringList.new(kv_labels))
        self._ask_kv_v_row.set_selected(self._kv_index("ask_kv_type_v"))
        self._ask_kv_v_row.connect("notify::selected", self._on_ask_kv_v_changed)
        group.add(self._ask_kv_v_row)

        self._ask_flash_row = Adw.SwitchRow(
            title="Flash attention",
            subtitle="Faster attention, less memory; required for quantizing the "
                     "V cache. Needed when the V cache is q8_0/q4_0.")
        self._ask_flash_row.set_active(self._settings.get("ask_flash_attn", False))
        self._ask_flash_row.connect("notify::active", self._on_ask_flash_changed)
        group.add(self._ask_flash_row)

        self._ask_mmap_row = Adw.SwitchRow(
            title="Memory-map model",
            subtitle="On (default) maps the model file lazily. Off loads it fully "
                     "into RAM — a longer 'Loading model…' but no page-faults "
                     "during the answer; needs enough free RAM.")
        self._ask_mmap_row.set_active(self._settings.get("ask_use_mmap", True))
        self._ask_mmap_row.connect("notify::active", self._on_toggle_setting,
                                   "ask_use_mmap")
        group.add(self._ask_mmap_row)

        self._ask_maxtok_row = Adw.SpinRow(
            title="Max answer length",
            subtitle="Hard cap on generated tokens. Bounds the answer and stops a "
                     "model that gets stuck repeating itself (~1.5 words/token).",
            adjustment=Gtk.Adjustment.new(
                self._settings.get("ask_max_tokens", 1024), 128, 8192, 128, 512,
                0.0),
            digits=0)
        self._ask_maxtok_row.connect("notify::value", self._on_ask_maxtok_changed)
        group.add(self._ask_maxtok_row)
        self._refresh_kv_hint()
        return self._subpage("Model runtime", page)

    def _on_ask_maxtok_changed(self, _row, _pspec) -> None:
        self._settings["ask_max_tokens"] = int(
            self._ask_maxtok_row.get_adjustment().get_value())
        self._persist()

    def _kv_index(self, key: str) -> int:
        v = self._settings.get(key, "f16")
        return self._ask_kv_types.index(v) if v in self._ask_kv_types else 0

    def _refresh_kv_hint(self) -> None:
        """V-cache subtitle: warn when it's quantized but flash attention (which
        it needs) is off — the user's decision basis."""
        from markdown_vault.search import llama_runtime
        text = ("Value-cache precision. Quantizing V (below f16) needs flash "
                "attention.")
        if llama_runtime.kv_needs_flash(self._settings.get("ask_kv_type_v", "f16")) \
                and not self._settings.get("ask_flash_attn"):
            text += " ⚠ Turn on Flash attention below."
        self._ask_kv_v_row.set_subtitle(text)

    def _on_ask_kv_k_changed(self, row, _pspec) -> None:
        self._settings["ask_kv_type_k"] = self._ask_kv_types[row.get_selected()]
        self._persist()

    def _on_ask_kv_v_changed(self, row, _pspec) -> None:
        self._settings["ask_kv_type_v"] = self._ask_kv_types[row.get_selected()]
        self._persist()
        self._refresh_kv_hint()

    def _on_ask_flash_changed(self, row, _pspec) -> None:
        self._settings["ask_flash_attn"] = row.get_active()
        self._persist()
        self._refresh_kv_hint()

    def _build_ask_subpage(self):
        """Chat model that writes grounded answers, plus a link to its prompt."""
        page = Adw.PreferencesPage(title="Ask")
        group = Adw.PreferencesGroup(
            title="Ask (answers from your notes)",
            description="The quick-open 'ask' mode answers from your notes with a "
                        "local model. 'Automatic' sets everything up for you; only "
                        "the model download needs a click. Choose 'Manual' to pick "
                        "the backend and tune it yourself.")
        page.add(group)

        self._ask_engines = ["auto", "manual", "off"]
        self._ask_engine_row = Adw.ComboRow(
            title="Answer engine",
            model=Gtk.StringList.new(
                ["Automatic — recommended", "Manual (advanced)", "Off"]))
        e = self._settings.get("ask_engine", "auto")
        self._ask_engine_row.set_selected(
            self._ask_engines.index(e) if e in self._ask_engines else 0)
        self._ask_engine_row.connect("notify::selected", self._on_ask_engine_changed)
        group.add(self._ask_engine_row)

        self._ask_backends = ["local", "ollama", "openai"]
        self._ask_backend_row = Adw.ComboRow(
            title="Backend",
            model=Gtk.StringList.new(
                ["Local — in-process, no server (recommended)",
                 "Ollama (/api/chat)", "OpenAI-compatible — llama.cpp (/v1)"]))
        b = self._settings.get("ask_backend", "local")
        self._ask_backend_row.set_selected(
            self._ask_backends.index(b) if b in self._ask_backends else 0)
        self._ask_backend_row.connect("notify::selected", self._on_ask_backend_changed)
        group.add(self._ask_backend_row)

        # --- Local (in-process GGUF) rows ---------------------------------
        # Model selector: pick among the GGUFs already in the models folder.
        self._ask_gguf_paths = []
        self._ask_gguf_updating = False
        self._ask_gguf_combo = Adw.ComboRow(title="Model",
                                            subtitle="Downloaded models")
        self._ask_gguf_list = Gtk.StringList()
        self._ask_gguf_combo.set_model(self._ask_gguf_list)
        self._ask_gguf_combo.connect("notify::selected", self._on_ask_gguf_selected)
        gguf_rescan = Gtk.Button(icon_name="view-refresh-symbolic",
                                 valign=Gtk.Align.CENTER,
                                 tooltip_text="Rescan the models folder")
        gguf_rescan.add_css_class("flat")
        gguf_rescan.connect("clicked", lambda *_: self._refresh_gguf_models())
        self._ask_gguf_combo.add_suffix(gguf_rescan)
        group.add(self._ask_gguf_combo)

        gguf_btn = Gtk.Button(icon_name="folder-download-symbolic",
                              valign=Gtk.Align.CENTER)
        gguf_btn.add_css_class("flat")
        gguf_btn.set_tooltip_text("Download the model")
        gguf_btn.connect("clicked", self._on_download_gguf)
        self._ask_gguf_dl_btn = gguf_btn
        self._ask_gguf_url_row, self._ask_gguf_url_entry = self._entry_row(
            "Model URL", "ask_gguf_url", trailing=gguf_btn)
        group.add(self._ask_gguf_url_row)

        self._ask_gguf_progress = Gtk.ProgressBar(
            show_text=True, visible=False,
            margin_start=12, margin_end=12, margin_bottom=6)
        group.add(self._ask_gguf_progress)

        self._ask_gguf_file_row = Adw.ActionRow(title="Model file")
        gguf_pick = Gtk.Button(icon_name="document-open-symbolic",
                               valign=Gtk.Align.CENTER,
                               tooltip_text="Choose a .gguf file…")
        gguf_pick.add_css_class("flat")
        gguf_pick.connect("clicked", lambda *_: self._choose_gguf_file())
        gguf_reset = Gtk.Button(icon_name="edit-clear-symbolic",
                                valign=Gtk.Align.CENTER,
                                tooltip_text="Reset to the default location")
        gguf_reset.add_css_class("flat")
        gguf_reset.connect("clicked", lambda *_: self._reset_gguf_path())
        self._ask_gguf_file_row.add_suffix(gguf_pick)
        self._ask_gguf_file_row.add_suffix(gguf_reset)
        group.add(self._ask_gguf_file_row)

        # GPU layers, CPU threads and the KV-cache knobs live on their own
        # subpage (built before this one), reached via this row.
        self._ask_runtime_row = self._nav_row(
            "Model runtime", "GPU layers, CPU threads, KV cache…",
            self._runtime_subpage)
        group.add(self._ask_runtime_row)
        # Model (download) rows: needed by the local backend in auto and manual.
        self._ask_model_rows = [self._ask_gguf_url_row, self._ask_gguf_file_row]

        # --- Server (Ollama / OpenAI-compatible) rows ---------------------
        self._ask_url_row, self._ask_url_entry = self._entry_row(
            "Server URL", "ask_ollama_url")
        # Hint the saved backend's port immediately (not only on a later switch).
        saved_url = self._ASK_BACKEND_URLS.get(self._settings.get("ask_backend"))
        if saved_url:
            self._ask_url_entry.set_placeholder_text(f"{saved_url} (default)")
        group.add(self._ask_url_row)
        # Privacy: unlike the in-process backend ("nothing leaves your machine"),
        # a server backend ships note content out — say so where the leak is.
        group.add(self._caption_row(
            "A non-local server receives the full text of every retrieved note "
            "with each question."))

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
        self._ask_server_rows = [self._ask_url_row, self._ask_model_combo]

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
            subtitle="Tokens of context. Used by the Local and Ollama backends "
                     "(higher fits more/longer notes but uses more memory); the "
                     "OpenAI-compatible server sizes its own context.",
            adjustment=Gtk.Adjustment.new(
                self._settings.get("ask_num_ctx", 8192),
                2048, 32768, 1024, 4096, 0.0),
            digits=0,
        )
        self._ask_ctx_row.connect("notify::value", self._on_ask_num_ctx_changed)
        group.add(self._ask_ctx_row)

        self._ask_prompt_row = self._nav_row(
            "System prompt", "Grounding instructions sent to the model",
            self._prompt_subpage)
        group.add(self._ask_prompt_row)
        # Rows that only make sense in Manual mode (Automatic configures them).
        self._ask_manual_rows = [self._ask_backend_row, self._ask_reasoning_row,
                                 self._ask_hybrid_row, self._ask_topk_row,
                                 self._ask_ctx_row, self._ask_prompt_row]
        self._refresh_gguf_models()
        self._refresh_gguf_status()
        self._update_ask_rows()          # show only the rows the engine/backend use
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
        from markdown_vault.search import ask as _ask
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

    def _on_ask_engine_changed(self, row, _pspec) -> None:
        self._settings["ask_engine"] = self._ask_engines[row.get_selected()]
        self._persist()
        self._update_ask_rows()

    def _on_ask_backend_changed(self, row, _pspec) -> None:
        backend = self._ask_backends[row.get_selected()]
        self._settings["ask_backend"] = backend
        self._update_ask_rows()
        # Point the URL hint (and an empty/other-default value) at the new
        # backend's port, so switching to llama.cpp doesn't silently keep :11434.
        default_url = self._ASK_BACKEND_URLS.get(backend)
        if default_url:
            self._ask_url_entry.set_placeholder_text(f"{default_url} (default)")
            cur = self._ask_url_entry.get_text().strip()
            if not cur or cur in self._ASK_BACKEND_URLS.values():
                self._ask_url_entry.set_text(default_url)  # fires changed → saves
        self._persist()
        if backend != "local":
            self._refresh_ask_models()  # different endpoint per server backend

    def _ask_effective_backend(self) -> str:
        """The backend the current engine will actually use: Automatic is always
        the in-process 'local' backend; Manual uses the chosen ask_backend."""
        engine = self._settings.get("ask_engine") or config.default("ask_engine")
        if engine == "auto":
            return "local"
        return self._settings.get("ask_backend") or config.default("ask_backend")

    def _update_ask_rows(self) -> None:
        """Show only the rows the current engine + backend actually use, so a
        non-technical user in Automatic sees just the model download, and the GPU
        row appears only when the installed build can offload."""
        engine = self._settings.get("ask_engine") or config.default("ask_engine")
        off = engine == "off"
        manual = engine == "manual"
        backend = self._ask_effective_backend()
        local = (not off) and backend == "local"
        for w in self._ask_model_rows:        # model download: auto + manual/local
            w.set_visible(local)
        # Model selector + the runtime subpage link: only in Manual, local.
        self._ask_gguf_combo.set_visible(manual and backend == "local")
        self._ask_runtime_row.set_visible(manual and backend == "local")
        for w in self._ask_server_rows:       # server URL + model list: manual only
            w.set_visible(manual and backend in ("ollama", "openai"))
        for w in self._ask_manual_rows:       # advanced tuning: manual only
            w.set_visible(manual)

    # -- Local (in-process GGUF) model management --------------------------

    def _gguf_path(self):
        from pathlib import Path
        resolved = config.resolve_model_path(self._settings)
        return Path(resolved) if resolved else config.models_dir() / "model.gguf"

    def _refresh_gguf_models(self) -> None:
        """Rescan the models folder into the selector, preselecting the active
        model. Guarded so rebuilding the list doesn't fire a spurious change."""
        from pathlib import Path
        self._ask_gguf_updating = True
        self._ask_gguf_paths = [str(p) for p in config.list_models()]
        self._ask_gguf_list.splice(0, self._ask_gguf_list.get_n_items(),
                                   [Path(p).name for p in self._ask_gguf_paths])
        current = config.resolve_model_path(self._settings)
        if current in self._ask_gguf_paths:
            self._ask_gguf_combo.set_selected(self._ask_gguf_paths.index(current))
        self._ask_gguf_updating = False
        n = len(self._ask_gguf_paths)
        self._ask_gguf_combo.set_subtitle(
            "No models downloaded yet" if not n
            else f"{n} in the models folder")

    def _on_ask_gguf_selected(self, combo, _pspec) -> None:
        if self._ask_gguf_updating:
            return
        i = combo.get_selected()
        if 0 <= i < len(self._ask_gguf_paths):
            self._settings["ask_gguf_path"] = self._ask_gguf_paths[i]
            self._persist()
            self._refresh_gguf_status()

    def _after_gguf_download(self, target) -> None:
        """A finished, valid download becomes the selected model. A rejected one
        (not a GGUF) is not selected — just rescan so it doesn't linger."""
        from pathlib import Path
        if Path(target).exists() and config.is_gguf(target):
            self._settings["ask_gguf_path"] = str(target)
            self._persist()
        self._refresh_gguf_models()
        self._refresh_gguf_status()

    # ── Audio transcription model (document import) ─────────────────
    def _refresh_whisper_status(self) -> None:
        """Show whether the configured transcription model is downloaded. Both checks
        are cheap (a find_spec and a file test), so no background thread is needed."""
        from . import document_import
        if document_import.is_available(".mp3"):          # faster_whisper absent
            self._whisper_status_icon.set_from_icon_name("dialog-warning-symbolic")
            self._whisper_row.set_subtitle("Needs the optional AI stack — make install-ai")
            self._whisper_dl_btn.set_sensitive(False)
            return
        self._apply_whisper_status(document_import.whisper_model_ready())

    def _on_whisper_model_changed(self, row, _pspec) -> None:
        self._settings["document_whisper_model"] = self._whisper_values[
            row.get_selected()]
        self._persist()
        self._refresh_whisper_status()          # different size → its own folder

    def _apply_whisper_status(self, ready: bool) -> bool:
        if ready:
            self._whisper_status_icon.set_from_icon_name("emblem-ok-symbolic")
            self._whisper_row.set_subtitle("Downloaded — audio import ready")
        else:
            self._whisper_status_icon.set_from_icon_name("folder-download-symbolic")
            self._whisper_row.set_subtitle("Not downloaded")
        self._whisper_dl_btn.set_sensitive(True)
        return False

    def _make_whisper_tqdm(self):
        """A tqdm subclass that drives the progress bar from the byte-download of the
        model file (the dominant part); the tiny config files just flash past."""
        bar = self._whisper_progress
        from tqdm.auto import tqdm as _base

        class _Bar(_base):
            def update(self_inner, n=1):
                r = super().update(n)
                if self_inner.total and getattr(self_inner, "unit", "") == "B":
                    GLib.idle_add(bar.set_fraction,
                                  min(1.0, self_inner.n / self_inner.total))
                return r
        return _Bar

    def _on_download_whisper(self, _btn) -> None:
        self._whisper_dl_btn.set_sensitive(False)
        self._whisper_status_icon.set_from_icon_name("folder-download-symbolic")
        self._whisper_row.set_subtitle("Downloading…")
        self._whisper_progress.set_fraction(0.0)
        self._whisper_progress.set_visible(True)
        model = self._whisper_values[self._whisper_model_row.get_selected()]
        threading.Thread(target=self._whisper_download_worker, args=(model,),
                         daemon=True).start()

    def _whisper_download_worker(self, model) -> None:
        from . import document_import
        try:
            document_import.download_whisper_model(
                model, tqdm_class=self._make_whisper_tqdm())
            GLib.idle_add(self._after_whisper_download, True, None)
        except Exception as exc:
            logger.warning("Whisper model download failed: %s", exc, exc_info=True)
            GLib.idle_add(self._after_whisper_download, False, str(exc))

    def _after_whisper_download(self, ok: bool, msg: str | None) -> bool:
        self._whisper_progress.set_visible(False)
        if ok:
            self._apply_whisper_status(True)
        else:
            self._whisper_status_icon.set_from_icon_name("dialog-error-symbolic")
            self._whisper_row.set_subtitle(f"Download failed: {msg[:70]}")
            self._whisper_dl_btn.set_sensitive(True)
        return False

    def _on_ask_gpu_layers_changed(self, _row, _pspec) -> None:
        self._settings["ask_n_gpu_layers"] = int(
            self._ask_gpu_row.get_adjustment().get_value())
        self._persist()

    def _on_ask_threads_changed(self, _row, _pspec) -> None:
        self._settings["ask_n_threads"] = int(
            self._ask_threads_row.get_adjustment().get_value())
        self._persist()

    def _setup_combo_item(self, _factory, item) -> None:
        item.set_child(Gtk.Label(xalign=0))

    def _refresh_ubatch_factory(self) -> None:
        # A fresh factory forces the popup to re-bind every item, so the greying
        # reflects the *current* batch size. GtkListView caches bound items and
        # won't re-run bind on its own when the batch changes.
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._setup_combo_item)
        factory.connect("bind", self._bind_ubatch_item)
        self._ask_ubatch_row.set_list_factory(factory)

    def _bind_ubatch_item(self, _factory, item) -> None:
        # n_ubatch ≤ n_batch: grey and block micro-batch values above the chosen
        # batch. "Default" is llama.cpp's own 512 (micro) / 2048 (batch).
        label = item.get_child()
        label.set_text(item.get_item().get_string())
        ubatch = self._ask_batch_values[item.get_position()] or 512
        batch = self._ask_batch_values[self._ask_batch_row.get_selected()] or 2048
        ok = ubatch <= batch
        label.set_sensitive(ok)
        item.set_selectable(ok)
        item.set_activatable(ok)

    def _on_ask_batch_changed(self, row, _pspec) -> None:
        self._settings["ask_n_batch"] = self._ask_batch_values[row.get_selected()]
        # Keep n_ubatch ≤ n_batch: if the batch dropped below the current
        # micro-batch, lower the micro-batch to the highest value that still fits.
        batch = self._settings["ask_n_batch"] or 2048
        current = self._ask_batch_values[self._ask_ubatch_row.get_selected()] or 512
        if current > batch:
            fits = [i for i, v in enumerate(self._ask_batch_values)
                    if (v or 512) <= batch]
            self._ask_ubatch_row.set_selected(fits[-1] if fits else 0)
        self._refresh_ubatch_factory()   # re-grey against the new batch limit
        self._persist()

    def _on_ask_ubatch_changed(self, row, _pspec) -> None:
        self._settings["ask_n_ubatch"] = self._ask_batch_values[row.get_selected()]
        self._persist()

    def _on_download_gguf(self, button) -> None:
        url = (self._ask_gguf_url_entry.get_text().strip()
               or config.default("ask_gguf_url"))
        if not url:
            return
        url = config.normalize_gguf_url(url)   # HF file page → raw-file link
        # Save under the URL's own filename so several models coexist instead of
        # overwriting one file; the finished download becomes the selection.
        target = config.models_dir() / config.model_filename_from_url(url)
        button.set_sensitive(False)
        bar = self._ask_gguf_progress
        bar.set_visible(True)
        bar.set_fraction(0.0)
        bar.set_text("Starting…")
        gguf_check = lambda p: (None if config.is_gguf(p) else
                                "That URL isn't a GGUF model file — use the "
                                "download (\"resolve\") link, not the web page.")
        threading.Thread(
            target=self._download_worker,
            args=(button, url, target, target.name, bar),
            kwargs={"refresh": lambda t=target: self._after_gguf_download(t),
                    "validate": gguf_check},
            daemon=True).start()

    def _choose_gguf_file(self) -> None:
        dialog = Gtk.FileDialog(title="Select a GGUF model file")
        cur = self._gguf_path()
        try:
            probe = cur.parent if cur.parent.exists() else None
            if probe is not None:
                dialog.set_initial_folder(Gio.File.new_for_path(str(probe)))
        except Exception:  # noqa: BLE001
            pass

        def done(dlg, result):
            try:
                gfile = dlg.open_finish(result)
            except GLib.Error:
                return  # cancelled or failed
            if gfile is not None and gfile.get_path():
                self._settings["ask_gguf_path"] = gfile.get_path()
                self._persist()
                self._refresh_gguf_models()
                self._refresh_gguf_status()

        dialog.open(self.get_root(), None, done)

    def _reset_gguf_path(self) -> None:
        self._settings["ask_gguf_path"] = ""   # empty → auto-pick the newest
        self._persist()
        self._refresh_gguf_models()
        self._refresh_gguf_status()

    def _refresh_gguf_status(self) -> None:
        p = self._gguf_path()
        if p.exists():
            mb = p.stat().st_size / 1024 / 1024
            self._ask_gguf_file_row.set_subtitle(f"{p}  ·  {mb:.0f} MB")
        else:
            self._ask_gguf_file_row.set_subtitle(f"{p}  ·  not downloaded")
        self._refresh_gpu_recommendation()

    def _refresh_gpu_recommendation(self) -> None:
        """Set the GPU-layers subtitle to a hardware-aware recommendation for the
        selected model (updates when the model changes)."""
        base = "Layers offloaded to the GPU. 0 = pure CPU, 999 = all."
        from markdown_vault.search import llama_runtime
        advice = llama_runtime.gpu_layers_advice(
            config.resolve_model_path(self._settings))
        self._ask_gpu_row.set_subtitle(f"{base} {advice}" if advice else base)

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
            from markdown_vault.search.semantic_search import OllamaEmbedder
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
            from markdown_vault.search.semantic_search import OnnxEmbedder
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

    def _download_worker(self, button, url, target, filename, bar,
                         refresh=None, validate=None) -> None:
        import urllib.request
        from urllib.parse import urlparse
        try:
            if urlparse(url).scheme != "https":
                GLib.idle_add(self._download_done, button, bar, False,
                              "Refusing a non-HTTPS download URL.", refresh)
                return
            target.parent.mkdir(parents=True, exist_ok=True)
            req = urllib.request.Request(
                url, headers={"User-Agent": "markdown-vault"})
            tmp = target.with_name(target.name + ".part")
            # Custom opener: refuse a redirect that downgrades off HTTPS.
            opener = urllib.request.build_opener(_HttpsOnlyRedirect())
            with opener.open(req, timeout=30) as resp:
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
            problem = validate(tmp) if validate is not None else None
            if problem:                        # wrong content (e.g. an HTML page)
                tmp.unlink(missing_ok=True)
                GLib.idle_add(self._download_done, button, bar, False,
                              problem, refresh)
                return
            tmp.replace(target)
            mb = target.stat().st_size / 1024 / 1024
            GLib.idle_add(
                self._download_done, button, bar, True,
                f"Downloaded {filename} ({mb:.0f} MB) — restart to use", refresh)
        except Exception as exc:  # network/IO/permission — report, don't crash
            logger.warning("model download failed: %s", exc)
            GLib.idle_add(
                self._download_done, button, bar, False, f"Failed: {exc}", refresh)

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

    def _download_done(self, button, bar, ok, msg, refresh=None) -> bool:
        button.set_sensitive(True)
        if ok:
            bar.set_fraction(1.0)
        bar.set_text(msg)
        try:
            self.add_toast(Adw.Toast.new(msg))
        except Exception:
            logger.info("%s", msg)
        (refresh or self._refresh_onnx_status)()  # a fetched file flips the state
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
        from markdown_vault.core.logging_setup import set_third_party_loglevel
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
