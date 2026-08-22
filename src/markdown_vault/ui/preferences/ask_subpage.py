"""Preferences — Ask subpage: the answer engine, the chat backend and its endpoint (URL, API key, model list), plus the local GGUF model."""

import logging
import threading
from pathlib import Path

import gi

logger = logging.getLogger(__name__)

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw, GLib, Gio

from markdown_vault.core import config


class AskSubpageMixin:
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
        e = config.get_setting(self._settings, "ask.engine", "auto")
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
        b = config.get_setting(self._settings, "ask.backend", "local")
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
        gguf_model_reset = Gtk.Button(icon_name="edit-clear-symbolic",
                                      valign=Gtk.Align.CENTER,
                                      tooltip_text="Reset to the newest model")
        gguf_model_reset.add_css_class("flat")
        gguf_model_reset.connect("clicked", lambda *_: self._reset_gguf_path())
        self._ask_gguf_combo.add_suffix(gguf_model_reset)
        self._ask_gguf_combo.add_suffix(gguf_rescan)
        group.add(self._ask_gguf_combo)

        gguf_btn = Gtk.Button(icon_name="folder-download-symbolic",
                              valign=Gtk.Align.CENTER)
        gguf_btn.add_css_class("flat")
        gguf_btn.set_tooltip_text("Download the model")
        gguf_btn.connect("clicked", self._on_download_gguf)
        self._ask_gguf_dl_btn = gguf_btn
        self._ask_gguf_url_row, self._ask_gguf_url_entry = self._entry_row(
            "Model Download", "ask.gguf.url", trailing=gguf_btn)

        self._ask_gguf_progress = Gtk.ProgressBar(
            show_text=True, visible=False,
            margin_start=12, margin_end=12, margin_bottom=6)

        # Folder the GGUFs live in — the search folder and the download target.
        # Display-only (no typing): pick a folder or reset to the shared default.
        # Kept apart from the Whisper models under the same default (ask_models_dir).
        self._ask_models_dir_row = Adw.ActionRow(title="Model folder")
        dir_pick = Gtk.Button(icon_name="folder-open-symbolic",
                              valign=Gtk.Align.CENTER, tooltip_text="Choose folder…")
        dir_pick.add_css_class("flat")
        dir_pick.connect("clicked", lambda *_: self._choose_models_dir())
        dir_reset = Gtk.Button(icon_name="edit-clear-symbolic",
                               valign=Gtk.Align.CENTER, tooltip_text="Reset to default")
        dir_reset.add_css_class("flat")
        dir_reset.connect("clicked", lambda *_: self._on_models_dir_selected(""))
        self._ask_models_dir_row.add_suffix(dir_reset)
        self._ask_models_dir_row.add_suffix(dir_pick)
        self._ask_models_dir_row.set_activatable_widget(dir_pick)
        group.add(self._ask_models_dir_row)

        # Download a model into the folder above — placed under it so the target
        # folder is chosen first.
        group.add(self._ask_gguf_url_row)
        group.add(self._ask_gguf_progress)

        # A second group starts after the model download: the runtime, the manual
        # backend/server settings and the answer tuning.
        group = Adw.PreferencesGroup()
        page.add(group)

        # GPU layers, CPU threads and the KV-cache knobs live on their own
        # subpage (built before this one), reached via this row.
        self._ask_runtime_row = self._nav_row(
            "Model runtime", "GPU layers, CPU threads, KV cache…",
            self._runtime_subpage)
        group.add(self._ask_runtime_row)
        # Model (download) rows: needed by the local backend in auto and manual.
        self._ask_model_rows = [self._ask_gguf_url_row, self._ask_models_dir_row]

        # --- Server (Ollama / OpenAI-compatible) rows ---------------------
        self._ask_url_row, self._ask_url_entry = self._entry_row(
            "Server URL", "ask.server.url")
        # Hint the saved backend's port immediately (not only on a later switch).
        from markdown_vault.search import ask_models
        saved_url = ask_models.DEFAULT_URLS.get(
            config.get_setting(self._settings, "ask.backend"))
        if saved_url:
            self._ask_url_entry.set_placeholder_text(f"{saved_url} (default)")
        # A non-local URL means note content leaves the device — re-check the warning
        # as the URL is typed (localhost llama.cpp/ollama stay local).
        self._ask_url_entry.connect("changed", lambda *_: self._on_ask_url_changed())
        group.add(self._ask_url_row)
        # API key (OpenAI-compatible servers that require auth). Stored in the OS
        # keyring, never in settings.yaml or the logs — see _key_row.
        # The key belongs to the server it was entered for, so the keyring name is
        # resolved per endpoint at read/write time, not fixed at build time.
        ask_models.adopt_legacy_key(self._settings)
        self._ask_key_row, self._ask_key_entry = self._key_row(
            "API key", self._ask_secret_name)
        group.add(self._ask_key_row)
        # Privacy: shown ONLY when the server is non-local (any server backend) —
        # a local llama.cpp/ollama sends nothing out; a remote one ships note text.
        self._ask_external_row = self._caption_row(
            "⚠ This server is not local — the full text of every retrieved note "
            "is sent to it with each question.")
        self._ask_external_row.set_visible(False)
        group.add(self._ask_external_row)

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
        self._ask_server_rows = [self._ask_url_row, self._ask_key_row,
                                 self._ask_model_combo]

        self._ask_reasoning_row = Adw.SwitchRow(
            title="Reasoning",
            subtitle="Let a reasoning model (Qwen3, …) think before answering — "
                     "more accurate but much slower. Off is faster and usually "
                     "enough for grounded note answers.")
        self._ask_reasoning_row.set_active(
            config.get_setting(self._settings, "ask.reasoning", True))
        self._ask_reasoning_row.connect(
            "notify::active", self._on_toggle_setting, "ask.reasoning")
        group.add(self._ask_reasoning_row)

        self._ask_hybrid_row = Adw.SwitchRow(
            title="Hybrid retrieval",
            subtitle="Fuse a keyword (BM25) ranking into the semantic search so "
                     "exact tokens — names, config keys, shortcuts — that "
                     "embeddings blur still surface. Helps most on large vaults.")
        self._ask_hybrid_row.set_active(
            config.get_setting(self._settings, "ask.hybrid", True))
        self._ask_hybrid_row.connect(
            "notify::active", self._on_toggle_setting, "ask.hybrid")
        group.add(self._ask_hybrid_row)

        self._ask_topk_row = Adw.SpinRow(
            title="Context notes",
            subtitle="How many notes are sent to the model as context. On CPU "
                     "the model spends almost all its time reading them, so fewer "
                     "= much faster (roughly linear). Recommended: 10 on a GPU, "
                     "~5 on a slow CPU.",
            adjustment=Gtk.Adjustment.new(
                config.get_setting(self._settings, "ask.top_k", 10), 3, 20, 1, 5, 0.0),
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
                config.get_setting(self._settings, "ask.num_ctx", 8192),
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
        self._update_external_warning()  # reveal the leak warning iff URL is non-local
        subpage = self._subpage("Ask", page)
        subpage.connect("shown", lambda *_: self.set_focus(None))  # see Embedding
        return subpage

    def _on_ask_model_selected(self, combo, _pspec) -> None:
        item = combo.get_selected_item()
        if item is not None:
            self._remember_ask_model(item.get_string())
            self._persist_debounced()

    def _remember_ask_model(self, model: str) -> None:
        """Record the choice for the *current* server, so switching provider and
        back restores it instead of sending it to a server that lacks it."""
        from markdown_vault.search import ask_models
        ask_models.remember(self._settings, self._ask_backend(), self._ask_url(), model)

    def _ask_backend(self) -> str:
        return (config.get_setting(self._settings, "ask.backend")
                or config.default("ask.backend"))

    def _ask_url(self) -> str:
        return (config.get_setting(self._settings, "ask.server.url")
                or config.default("ask.server.url"))

    def _ask_secret_name(self) -> str:
        """Keyring name of the API key for the currently configured server."""
        from markdown_vault.search import ask_models
        return ask_models.secret_name(self._ask_backend(), self._ask_url())

    def _reload_ask_key(self) -> None:
        """Show the key of the server that is configured now. A pending edit is
        flushed first, so it still reaches the server it was typed for."""
        from markdown_vault.core import secret_store
        if not self._ask_key_entry.get_sensitive():
            return                      # no keyring — the field is disabled
        self._flush_secret()
        name = self._ask_secret_name()
        stored = secret_store.get_secret(name)
        self._secret_updating = True
        try:
            self._ask_key_entry.set_text(stored)
        finally:
            self._secret_updating = False
        # Same bookkeeping as when the row was built: only a key we have actually
        # seen may later be deleted by clearing the field.
        if stored:
            self._known_secrets.add(name)
        else:
            self._known_secrets.discard(name)

    def _on_ask_url_changed(self) -> None:
        """The URL identifies the server: warn (or stop warning) about notes
        leaving the device, and re-fetch that server's models once typing stops."""
        self._update_external_warning()
        self._schedule_ask_models_refresh()

    def _schedule_ask_models_refresh(self, delay_ms: int = 700) -> None:
        if self._ask_backend() not in ("ollama", "openai"):
            return
        self._cancel_ask_models_refresh()
        self._ask_models_id = GLib.timeout_add(delay_ms, self._ask_models_timeout)

    def _ask_models_timeout(self) -> bool:
        """The URL has settled — it now identifies a (possibly different) server,
        so file it under this backend and pick up that server's model and key."""
        self._ask_models_id = None
        from markdown_vault.search import ask_models
        backend, url = self._ask_backend(), self._ask_url()
        ask_models.remember_url(self._settings, backend, url)
        ask_models.activate(self._settings, backend, url)
        self._persist_debounced()
        self._reload_ask_key()
        self._refresh_ask_models()
        return False

    def _cancel_ask_models_refresh(self, *_args) -> None:
        if self._ask_models_id is not None:
            GLib.source_remove(self._ask_models_id)
            self._ask_models_id = None

    def _on_ask_engine_changed(self, row, _pspec) -> None:
        config.set_setting(self._settings, "ask.engine",
                           self._ask_engines[row.get_selected()])
        self._persist()
        self._update_ask_rows()

    def _on_ask_backend_changed(self, row, _pspec) -> None:
        from markdown_vault.search import ask_models
        backend = self._ask_backends[row.get_selected()]
        previous = (config.get_setting(self._settings, "ask.backend")
                    or config.default("ask.backend"))
        self._flush_secret()          # the pending key still belongs to `previous`
        # URL, model and key belong to the provider: file the old one's, restore
        # the new one's. Without this, a hand-typed URL is carried over and the
        # new backend talks to the previous one's host.
        url = ask_models.switch_backend(self._settings, previous, backend)
        self._update_ask_rows()
        default_url = ask_models.DEFAULT_URLS.get(backend)
        if default_url:
            self._ask_url_entry.set_placeholder_text(f"{default_url} (default)")
            self._ask_url_entry.set_text(url)          # fires changed → saves
        self._reload_ask_key()
        self._update_external_warning()
        self._persist()
        if backend != "local":
            self._cancel_ask_models_refresh()   # the URL change scheduled one
            self._refresh_ask_models()          # different endpoint per backend

    @staticmethod
    def _is_local_url(url: str) -> bool:
        """True if *url* points at this machine (or is empty/unset)."""
        from urllib.parse import urlparse
        host = (urlparse(url).hostname or "").lower() if url else ""
        return host in ("", "localhost", "127.0.0.1", "::1", "0.0.0.0")

    def _update_external_warning(self) -> None:
        """Reveal the 'notes leave the device' warning only for a server backend
        with a **non-local** URL — a local llama.cpp/ollama sends nothing out."""
        row = getattr(self, "_ask_external_row", None)
        if row is None:
            return
        backend = config.get_setting(self._settings, "ask.backend")
        url = (self._ask_url_entry.get_text().strip()
               or config.get_setting(self._settings, "ask.server.url", ""))
        row.set_visible(backend in ("ollama", "openai") and not self._is_local_url(url))

    def _ask_effective_backend(self) -> str:
        """The backend the current engine will actually use: Automatic is always
        the in-process 'local' backend; Manual uses the chosen ask_backend."""
        engine = (config.get_setting(self._settings, "ask.engine")
                  or config.default("ask.engine"))
        if engine == "auto":
            return "local"
        return (config.get_setting(self._settings, "ask.backend")
                or config.default("ask.backend"))

    def _update_ask_rows(self) -> None:
        """Show only the rows the current engine + backend actually use, so a
        non-technical user in Automatic sees just the model download, and the GPU
        row appears only when the installed build can offload."""
        engine = (config.get_setting(self._settings, "ask.engine")
                  or config.default("ask.engine"))
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

    def _refresh_gguf_models(self) -> None:
        """Rescan the models folder into the selector, preselecting the active
        model, then refresh the Model-row status and the folder row. Guarded so
        rebuilding the list doesn't fire a spurious change."""
        from pathlib import Path
        self._ask_gguf_updating = True
        self._ask_gguf_paths = [str(p) for p in config.list_models(self._settings)]
        self._ask_gguf_list.splice(0, self._ask_gguf_list.get_n_items(),
                                   [Path(p).name for p in self._ask_gguf_paths])
        current = config.resolve_model_path(self._settings)
        if current in self._ask_gguf_paths:
            self._ask_gguf_combo.set_selected(self._ask_gguf_paths.index(current))
        self._ask_gguf_updating = False
        self._refresh_models_dir_row()
        self._refresh_gguf_status()

    def _refresh_models_dir_row(self) -> None:
        """The folder row's subtitle: the models folder and how many GGUFs it holds."""
        d = config.ask_models_dir(self._settings)
        n = len(self._ask_gguf_paths)
        self._ask_models_dir_row.set_subtitle(
            f"{d}  ·  {n} model{'' if n == 1 else 's'}")

    def _on_ask_gguf_selected(self, combo, _pspec) -> None:
        if self._ask_gguf_updating:
            return
        i = combo.get_selected()
        if 0 <= i < len(self._ask_gguf_paths):
            from pathlib import Path
            # Store the filename, not the absolute path — it is a name in
            # ask_models_dir and must survive the folder moving.
            config.set_setting(self._settings, "ask.gguf.path",
                               Path(self._ask_gguf_paths[i]).name)
            self._persist()
            self._refresh_gguf_status()

    def _after_gguf_download(self, target) -> None:
        """A finished, valid download becomes the selected model. A rejected one
        (not a GGUF) is not selected — just rescan so it doesn't linger."""
        from pathlib import Path
        if Path(target).exists() and config.is_gguf(target):
            # Store the filename — the download lands in ask_models_dir (see the
            # download target below), where list_models and resolve_model_path look.
            config.set_setting(self._settings, "ask.gguf.path", Path(target).name)
            self._persist()
        self._refresh_gguf_models()
        self._refresh_gguf_status()

    def _on_download_gguf(self, button) -> None:
        url = (self._ask_gguf_url_entry.get_text().strip()
               or config.default("ask.gguf.url"))
        if not url:
            return
        url = config.normalize_gguf_url(url)   # HF file page → raw-file link
        # Save under the URL's own filename so several models coexist instead of
        # overwriting one file; the finished download becomes the selection.
        target = config.ask_models_dir(self._settings) / config.model_filename_from_url(url)
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

    def _choose_models_dir(self) -> None:
        dialog = Gtk.FileDialog(title="Select the models folder")
        cur = config.ask_models_dir(self._settings)
        try:
            if cur.exists():
                dialog.set_initial_folder(Gio.File.new_for_path(str(cur)))
        except Exception:  # noqa: BLE001
            pass

        def done(dlg, result):
            try:
                gfile = dlg.select_folder_finish(result)
            except GLib.Error:
                return  # cancelled or failed
            if gfile is not None and gfile.get_path():
                self._on_models_dir_selected(gfile.get_path())

        dialog.select_folder(self.get_root(), None, done)

    def _on_models_dir_selected(self, path: str) -> None:
        """Set the models folder (empty → the shared default), then rescan — the
        dropdown, the size subtitle and the folder row all follow."""
        config.set_setting(self._settings, "ask.gguf.dir", path)
        self._persist()
        self._refresh_gguf_models()

    def _reset_gguf_path(self) -> None:
        config.set_setting(self._settings, "ask.gguf.path", "")  # empty → auto-pick newest
        self._persist()
        self._refresh_gguf_models()
        self._refresh_gguf_status()

    def _refresh_gguf_status(self) -> None:
        """The Model row's subtitle: the chosen model's name + size when it loads,
        an explicit "not found" when a *set* choice is gone (the Quick Open banner
        blocks it too), or a hint when nothing is chosen yet."""
        from pathlib import Path
        chosen = config.get_setting(self._settings, "ask.gguf.path") or ""
        resolved = config.resolve_model_path(self._settings)
        if resolved:
            p = Path(resolved)
            mb = p.stat().st_size / 1024 / 1024 if p.exists() else 0
            via = "" if chosen else "  ·  newest"
            self._ask_gguf_combo.set_subtitle(f"{p.name}  ·  {mb:.0f} MB{via}")
        elif chosen:
            wanted = Path(config.ask_gguf_wanted_path(self._settings)).name
            self._ask_gguf_combo.set_subtitle(f"{wanted} — not found")
        else:
            self._ask_gguf_combo.set_subtitle("No models downloaded yet")
        self._refresh_gpu_recommendation()

    def _refresh_ask_models(self) -> None:
        """Fetch the model list off the main thread. Endpoint and parsing live in
        ask_models (shared with the Ask footer picker); the result also fills that
        module's cache, so the palette shows the same list without a second fetch.
        Errors are surfaced inline here — unlike the background refresh, this is a
        thing the user asked for and wants an answer to."""
        from markdown_vault.core import secret_store
        from markdown_vault.search import ask_models
        backend, url = self._ask_backend(), self._ask_url()
        if backend not in ask_models.SERVER_BACKENDS:
            return
        key = secret_store.get_secret(self._ask_secret_name())  # also proxied Ollama
        self._ask_model_combo.set_subtitle("Loading…")

        def worker():
            # probe() classifies and records the status, so the palette sees the
            # same verdict as this dialog — including a failure.
            GLib.idle_add(self._populate_ask_models,
                          ask_models.probe(backend, url, key))

        threading.Thread(target=worker, daemon=True).start()

    def _populate_ask_models(self, status) -> bool:
        """Show what the server answered — *status* is an ``ask_models``
        :class:`EndpointStatus`, so the wording here and the palette's warning come
        from the same verdict."""
        from markdown_vault.search import ask_models
        models = status.models
        if status.state in (ask_models.UNREACHABLE, ask_models.UNAUTHORIZED,
                            ask_models.LIST_ERROR):
            self._ask_model_combo.set_subtitle(f"Not reachable: {status.error}")
            self._ask_model_combo.add_css_class("error")
            return False
        self._ask_model_combo.remove_css_class("error")
        if status.state == ask_models.NO_LIST:
            self._ask_model_combo.set_subtitle(
                "This server does not list models — it serves a fixed one")
            return False
        if not models:
            self._ask_model_combo.set_subtitle("No models on the server")
            return False
        # Only ever offer what this server actually has: a model kept from another
        # provider (or one that has since been removed) would be sent to a server
        # that does not know it. If the stored choice is gone, adopt a real one.
        current = config.get_setting(self._settings, "ask.server.model")
        self._ask_model_list.splice(0, self._ask_model_list.get_n_items(), models)
        if current in models:
            self._ask_model_combo.set_selected(models.index(current))
        else:
            self._ask_model_combo.set_selected(0)
            self._remember_ask_model(models[0])
            self._persist_debounced()
        self._ask_model_combo.set_subtitle(f"{len(models)} models")
        return False

    def _on_ask_num_ctx_changed(self, _row, _pspec) -> None:
        config.set_setting(self._settings, "ask.num_ctx",
                           int(self._ask_ctx_row.get_adjustment().get_value()))
        self._persist()

    def _on_ask_top_k_changed(self, _row, _pspec) -> None:
        config.set_setting(self._settings, "ask.top_k",
                           int(self._ask_topk_row.get_adjustment().get_value()))
        self._persist()
