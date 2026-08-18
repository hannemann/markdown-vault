"""Markdown Vault — preferences dialog.

Provides an ``Adw.PreferencesDialog`` for editing application settings
such as autosave interval, editor appearance, and default view mode.
Changes are applied immediately and persisted to ``vaults.yaml``.
"""

import logging

import gi

logger = logging.getLogger(__name__)

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw, GObject, GLib

from markdown_vault.core import config
from markdown_vault.uikit import dialogs

from markdown_vault.ui.preferences.debug_page import DebugPageMixin
from markdown_vault.ui.preferences.editor_page import EditorPageMixin
from markdown_vault.ui.preferences.embedding_subpage import EmbeddingSubpageMixin
from markdown_vault.ui.preferences.general_page import GeneralPageMixin
from markdown_vault.ui.preferences.keyboard_page import KeyboardPageMixin
from markdown_vault.ui.preferences.preview_page import PreviewPageMixin
from markdown_vault.ui.preferences.prompt_subpage import PromptSubpageMixin
from markdown_vault.ui.preferences.runtime_subpage import RuntimeSubpageMixin
from markdown_vault.ui.preferences.search_page import SearchPageMixin
from markdown_vault.ui.preferences.web_page import WebPageMixin
from markdown_vault.ui.preferences.ask_subpage import AskSubpageMixin


class PreferencesDialog(
    GeneralPageMixin, EditorPageMixin, PreviewPageMixin, WebPageMixin,
    SearchPageMixin, KeyboardPageMixin, DebugPageMixin,
    EmbeddingSubpageMixin, RuntimeSubpageMixin, AskSubpageMixin,
    PromptSubpageMixin,
    Adw.PreferencesDialog,
):
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

        # The one owned settings object, not a copy: the dialog applies instantly
        # (no OK/Cancel), so a private snapshot bought nothing and was one of the
        # two writers that could undo each other.
        self._settings = config.settings()
        self._glib_loglevel_callback = glib_loglevel_callback
        self._on_reindex = on_reindex
        # Debounced disk writes for text entries (R22.11): typing a URL by hand
        # must not rewrite vaults.yaml on every keystroke.
        self._persist_id = None
        self.connect("closed", self._flush_persist)
        # Same debounce for the keyring-backed API key (secret_store), kept
        # separate so a secret never lands in self._settings / vaults.yaml.
        self._secret_persist_id = None
        self._pending_secret = None
        self._secret_updating = False
        # Keyring names we know hold a value — only those may be cleared by an
        # empty field (see _persist_secret_now).
        self._known_secrets: set = set()
        self.connect("closed", self._flush_secret)
        # Debounced model-list refresh: the list belongs to the server URL, so
        # editing that URL re-fetches — but not on every keystroke.
        self._ask_models_id = None
        self.connect("closed", self._cancel_ask_models_refresh)

        # Order is behaviour: it is the tab order the user sees, and the Search
        # page links to subpages that must exist by then.
        self._build_general_page()
        self._build_editor_page()
        self._build_preview_page()
        self._build_web_page()
        self._build_search_page()
        self._build_keyboard_page()
        self._build_debug_page()

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

    @staticmethod
    def _secret_name_of(secret_key) -> str:
        """*secret_key* may be a fixed name or a callable resolving one — the Ask
        key's name depends on the configured endpoint and changes with it."""
        return secret_key() if callable(secret_key) else secret_key

    def _key_row(self, title, secret_key):
        """Like :meth:`_entry_row`, but the value is a **secret in the OS keyring**
        (libsecret), never in settings — so it stays out of vaults.yaml and the
        logs. Masked, and written **debounced** on a background flush, because a
        keyring write can prompt/unlock and must not fire per keystroke."""
        from markdown_vault.core import secret_store
        row = Adw.PreferencesRow(activatable=False)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12,
                      margin_start=12, margin_end=12, margin_top=10, margin_bottom=10)
        box.append(Gtk.Label(label=title, xalign=0.0, valign=Gtk.Align.CENTER,
                             width_request=self._LABEL_WIDTH))
        entry = Gtk.Entry(hexpand=True, valign=Gtk.Align.CENTER, visibility=False)
        if secret_store.available():
            name = self._secret_name_of(secret_key)
            stored = secret_store.get_secret(name)
            entry.set_text(stored)
            if stored:
                self._known_secrets.add(name)   # so clearing it may delete it
        else:
            entry.set_placeholder_text("no keyring available — key won't be saved")
            entry.set_sensitive(False)
        entry.connect("changed", self._on_secret_changed, secret_key)
        box.append(entry)
        row.set_child(box)
        return row, entry

    def _on_secret_changed(self, entry, secret_key):
        if self._secret_updating:      # reloading the field, not a user edit
            return
        # Resolve the name NOW: if the endpoint changes before the debounce fires,
        # the key must still land on the server it was typed for.
        self._pending_secret = (self._secret_name_of(secret_key), entry.get_text())
        if self._secret_persist_id is not None:
            GLib.source_remove(self._secret_persist_id)
        self._secret_persist_id = GLib.timeout_add(
            self._PERSIST_DEBOUNCE_MS, self._persist_secret_now)

    def _persist_secret_now(self) -> bool:
        self._secret_persist_id = None
        if self._pending_secret is not None:
            from markdown_vault.core import secret_store
            name, value = self._pending_secret
            self._pending_secret = None
            if not value and name not in self._known_secrets:
                # An empty field is only an instruction to delete when it once
                # held something. The keyring can answer "available" and still
                # return nothing (locked between probe and read) — the field then
                # looks empty although a key is stored, and touching it would
                # destroy a credential the user never saw. Logged rather than
                # swallowed: if this ever fires, that hiccup is real and worth
                # knowing about.
                logger.warning("not clearing %s: the field never showed a value, "
                               "so an empty write is not a deletion", name)
                return False
            # A write can fail even when the service was reachable at open (the store
            # call is what triggers the unlock prompt; the user may cancel it). Surface
            # it like a failed settings save, so it isn't a silent no-op that only
            # shows up later as a 401 pointing at the server instead of the save.
            if not secret_store.set_secret(name, value):
                dialogs.show_error(self.get_root(), "Keyring",
                                   "Could not store the API key in the keyring.")
                return False
            # Track what we know is stored: a key set here may be cleared here.
            if value:
                self._known_secrets.add(name)
            else:
                self._known_secrets.discard(name)
        return False

    def _flush_secret(self, *_args) -> None:
        """Write a pending keyring change immediately (on dialog close)."""
        if self._secret_persist_id is not None:
            GLib.source_remove(self._secret_persist_id)
        self._persist_secret_now()

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











    # ── Handlers ────────────────────────────────────────────────────

    def _on_toggle_setting(self, row, _pspec, key) -> None:
        self._settings[key] = row.get_active()
        self._persist()
        if key == "semantic_search_enabled":
            # Takes effect right away: leaving the dependent rows live until the
            # next restart would invite changes that do nothing.
            self._update_semantic_sensitivity()

    def _on_entry_setting(self, row, key) -> None:
        self._settings[key] = row.get_text().strip()
        self._persist_debounced()


















    # -- Local (in-process GGUF) model management --------------------------





    # ── Audio transcription model (document import) ─────────────────




























    # ── ONNX runtime probe ────────────────────────────────────────

    # Sentence-transformer exports (opset ~14) load on any recent onnxruntime.
    _ONNX_RUNTIME_RECOMMENDED = "1.16"


    # ── Backend connection / model self-tests ─────────────────────











    # ── ONNX model / tokenizer download ────────────────────────────








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











    # ── Keybinding capture ──────────────────────────────────────────










