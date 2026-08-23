"""Preferences — Search page: the semantic-search master switch, the links to the
Embedding/Ask subpages, and audio transcription for the importer."""

import logging
import threading

import gi

logger = logging.getLogger(__name__)

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw, GLib

from markdown_vault.core.i18n import _

from markdown_vault.core import config


class SearchPageMixin:
    def _build_search_page(self) -> None:
        # ── Search page (overview → Embedding / Ask / Prompt subpages) ──
        from markdown_vault.search import ask as _ask
        self._emb_subpage = self._build_embedding_subpage()
        self._prompt_subpage = self._build_prompt_subpage(_ask)
        self._runtime_subpage = self._build_runtime_subpage()
        self._ask_subpage = self._build_ask_subpage()
        # Addressable subpages for open_page(page, subpage=…).
        self._subpages.update(embedding=self._emb_subpage, ask=self._ask_subpage,
                              prompt=self._prompt_subpage, runtime=self._runtime_subpage)

        search = Adw.PreferencesPage(title="Search", icon_name="edit-find-symbolic")
        # Addressable so a banner can navigate here by name — see open_page.
        search.set_name("search")
        sem_group = Adw.PreferencesGroup(
            title=_("Semantic search"),
            description=(
                "Find notes by meaning. Off by default; nothing is downloaded "
                "or contacted while disabled. Recommended backend: Local (ONNX) "
                "— runs in-process, no server, nothing leaves your machine. "
                "Ollama is an alternative if you already run a server (e.g. with "
                "a GPU). Changes take effect after restart."
            ),
        )
        search.add(sem_group)

        self._sem_enabled_row = Adw.SwitchRow(title=_("Enable semantic search"))
        self._sem_enabled_row.set_active(
            config.get_setting(self._settings, "semantic.enabled", False))
        self._sem_enabled_row.connect(
            "notify::active", self._on_toggle_setting, "semantic.enabled")
        sem_group.add(self._sem_enabled_row)

        self._sem_backends = list(self._SEM_BACKENDS)
        self._sem_backend_row = Adw.ComboRow(
            title=_("Backend"),
            subtitle=_("Local runs in-process (recommended); the servers need one running"),
            model=Gtk.StringList.new([_("Local (ONNX) — recommended"), _("Ollama (server)"),
                                      _("OpenAI-compatible (server)")]),
        )
        self._sem_backend_row.set_selected(self._sem_backend_index())
        self._sem_backend_row.connect("notify::selected", self._on_sem_backend_changed)
        sem_group.add(self._sem_backend_row)

        self._sem_score_row = Adw.SpinRow(
            title=_("Minimum similarity"),
            subtitle=_("Higher = stricter (fewer, closer matches)"),
            adjustment=Gtk.Adjustment.new(
                config.get_setting(self._settings, "semantic.min_score", 0.35),
                0.0, 1.0, 0.05, 0.1, 0.0,
            ),
            digits=2,
        )
        self._sem_score_row.connect("notify::value", self._on_min_score_changed)
        sem_group.add(self._sem_score_row)

        # Rebuild: discard the cache and re-embed everything against the
        # currently selected backend, live — the way to apply a backend switch
        # (or a freshly downloaded model) without restarting.
        self._sem_rebuild_row = Adw.ActionRow(
            title=_("Rebuild index now"),
            subtitle=_("Clear the cache and re-embed all notes with the selected backend"),
        )
        self._sem_rebuild_btn = Gtk.Button(
            label=_("Rebuild"), valign=Gtk.Align.CENTER)
        self._sem_rebuild_btn.add_css_class("destructive-action")
        self._sem_rebuild_btn.set_sensitive(self._on_reindex is not None)
        self._sem_rebuild_btn.connect("clicked", self._on_rebuild_index)
        self._sem_rebuild_row.add_suffix(self._sem_rebuild_btn)
        self._sem_rebuild_row.set_activatable_widget(self._sem_rebuild_btn)
        sem_group.add(self._sem_rebuild_row)

        # Navigation into the detailed configuration subpages.
        cfg_group = Adw.PreferencesGroup(title=_("Configuration"))
        self._emb_nav_row = self._nav_row(
            _("Embedding"), _("Model that turns notes into vectors"), self._emb_subpage)
        cfg_group.add(self._emb_nav_row)
        self._ask_nav_row = self._nav_row(
            _("Ask (answers from your notes)"),
            _("Chat model + prompt for synthesized answers"), self._ask_subpage)
        cfg_group.add(self._ask_nav_row)
        search.add(cfg_group)

        # ── Audio transcription (used by the document importer's File tab) ──
        audio_group = Adw.PreferencesGroup(
            title=_("Audio transcription"),
            description=_("Turn audio files into text when you import them. Pick a "
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
        self._whisper_values = [v for v, _lbl in whisper_models]
        self._whisper_model_row = Adw.ComboRow(
            title=_("Model"),
            subtitle=_("Multilingual; bigger = more accurate, slower, larger download"),
            model=Gtk.StringList.new([label for _v, label in whisper_models]))
        current = (config.get_setting(self._settings, "document.whisper_model")
                   or config.default("document.whisper_model"))
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

        self._whisper_row = Adw.ActionRow(title=_("Model files"))
        self._whisper_status_icon = Gtk.Image()
        self._whisper_row.add_prefix(self._whisper_status_icon)
        self._whisper_dl_btn = Gtk.Button(
            icon_name="folder-download-symbolic", valign=Gtk.Align.CENTER,
            tooltip_text=_("Download the selected model"))
        self._whisper_dl_btn.add_css_class("flat")
        self._whisper_dl_btn.connect("clicked", self._on_download_whisper)
        self._whisper_row.add_suffix(self._whisper_dl_btn)
        audio_group.add(self._whisper_row)
        search.add(audio_group)

        self._update_semantic_sensitivity()
        self._update_sem_backend_sensitivity()
        self._refresh_onnx_status()
        self._refresh_ask_models()
        self._refresh_whisper_status()
        threading.Thread(target=self._probe_onnx_runtime, daemon=True).start()

        self.add(search)

    def _on_min_score_changed(self, _row, _pspec) -> None:
        config.set_setting(self._settings, "semantic.min_score", round(
            self._sem_score_row.get_adjustment().get_value(), 2))
        self._persist()

    def _on_sem_backend_changed(self, row, _pspec) -> None:
        backend = self._sem_backends[row.get_selected()]
        config.set_setting(self._settings, "semantic.backend", backend)
        self._persist()
        self._update_sem_backend_sensitivity()
        if backend == "openai":
            # Pick up this endpoint's key, models and warnings now that it's live.
            self._reload_sem_openai_key()
            self._update_sem_openai_external_warning()
            self._refresh_sem_openai_state()
            self._refresh_sem_openai_models()

    def _sem_backend_index(self) -> int:
        """Selected-row index for the persisted backend, tolerant of an unknown
        value (hand-edited YAML, a future backend) so __init__ never raises and
        the whole dialog fails to open."""
        backend = config.get_setting(self._settings, "semantic.backend", self._SEM_BACKEND_DEFAULT)
        return self._sem_backends.index(backend) if backend in self._sem_backends else 0

    def _update_semantic_sensitivity(self) -> None:
        """Grey out everything that only works with semantic search on.

        Nothing below the master switch has any effect while it is off — down to
        the two subpages, which is why the nav rows go dead too (their content is
        embedding and Ask configuration). The switch itself stays live, or there
        would be no way back.
        """
        on = bool(config.get_setting(self._settings, "semantic.enabled"))
        for row in (self._sem_backend_row, self._sem_score_row,
                    self._sem_rebuild_row, self._emb_nav_row, self._ask_nav_row):
            row.set_sensitive(on)

    def _update_sem_backend_sensitivity(self) -> None:
        """Grey out the groups the selected backend does not use (one live at a
        time, three-way)."""
        backend = config.get_setting(self._settings, "semantic.backend", self._SEM_BACKEND_DEFAULT)
        for widgets, name in ((self._sem_onnx_widgets, "onnx"),
                              (self._sem_ollama_widgets, "ollama"),
                              (self._sem_openai_widgets, "openai")):
            for w in widgets:
                w.set_sensitive(backend == name)

    def _on_rebuild_index(self, _button) -> None:
        if self._on_reindex is None:
            return
        if not config.get_setting(self._settings, "semantic.enabled"):
            msg = _("Enable semantic search first")
        else:
            self._on_reindex()
            msg = _("Rebuilding semantic index in the background…")
        try:
            self.add_toast(Adw.Toast.new(msg))
        except Exception:  # noqa: BLE001 — toast is cosmetic; fall back to a log line
            logger.info("%s", msg)

    def _refresh_whisper_status(self) -> None:
        """Show whether the configured transcription model is downloaded. Both checks
        are cheap (a find_spec and a file test), so no background thread is needed."""
        from markdown_vault.importers import document_import
        if document_import.is_available(".mp3"):          # faster_whisper absent
            self._whisper_status_icon.set_from_icon_name("dialog-warning-symbolic")
            self._whisper_row.set_subtitle(_("Needs the optional AI stack — make install-ai"))
            self._whisper_dl_btn.set_sensitive(False)
            return
        self._apply_whisper_status(document_import.whisper_model_ready())

    def _on_whisper_model_changed(self, row, _pspec) -> None:
        config.set_setting(self._settings, "document.whisper_model",
                           self._whisper_values[row.get_selected()])
        self._persist()
        self._refresh_whisper_status()          # different size → its own folder

    def _apply_whisper_status(self, ready: bool) -> bool:
        if ready:
            self._whisper_status_icon.set_from_icon_name("emblem-ok-symbolic")
            self._whisper_row.set_subtitle(_("Downloaded — audio import ready"))
        else:
            self._whisper_status_icon.set_from_icon_name("folder-download-symbolic")
            self._whisper_row.set_subtitle(_("Not downloaded"))
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
        self._whisper_row.set_subtitle(_("Downloading…"))
        self._whisper_progress.set_fraction(0.0)
        self._whisper_progress.set_visible(True)
        model = self._whisper_values[self._whisper_model_row.get_selected()]
        threading.Thread(target=self._whisper_download_worker, args=(model,),
                         daemon=True).start()

    def _whisper_download_worker(self, model) -> None:
        from markdown_vault.importers import document_import
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
            self._whisper_row.set_subtitle(_("Download failed: {error}").format(error=msg[:70]))
            self._whisper_dl_btn.set_sensitive(True)
        return False
