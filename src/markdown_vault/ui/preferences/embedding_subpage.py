"""Preferences — Embedding subpage: the vector backend, its model files, downloads
and the reachability self-tests."""

import importlib
import logging
import threading
from pathlib import Path

import gi

logger = logging.getLogger(__name__)

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw, GLib, Gio

from markdown_vault.core import config
from markdown_vault.uikit import dialogs

from markdown_vault.ui.preferences.constants import _HttpsOnlyRedirect


class EmbeddingSubpageMixin:
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
            "Model Download", "semantic.onnx.model_url", trailing=model_btn)
        self._sem_model_dl_btn = model_btn

        self._sem_model_progress = Gtk.ProgressBar(
            show_text=True, visible=False,
            margin_start=12, margin_end=12, margin_bottom=6)

        tok_btn = Gtk.Button(
            icon_name="folder-download-symbolic", valign=Gtk.Align.CENTER)
        tok_btn.add_css_class("flat")
        tok_btn.set_tooltip_text("Download tokenizer.json")
        tok_btn.connect("clicked", self._on_download_onnx, "tokenizer")
        self._sem_tok_url_row, self._sem_tok_url_entry = self._entry_row(
            "Tokenizer Download", "semantic.onnx.tokenizer_url", trailing=tok_btn)
        self._sem_tok_dl_btn = tok_btn

        self._sem_tok_progress = Gtk.ProgressBar(
            show_text=True, visible=False,
            margin_start=12, margin_end=12, margin_bottom=6)

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

        # Downloads into the folder above — placed under it so the target folder
        # is chosen first.
        local.add(self._sem_model_url_row)
        local.add(self._sem_model_progress)
        local.add(self._sem_tok_url_row)
        local.add(self._sem_tok_progress)

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
            "Ollama URL", "semantic.ollama.url")
        ollama.add(self._sem_url_row)

        self._sem_model_row, self._sem_model_entry = self._entry_row(
            "Embedding model", "semantic.ollama.model")
        ollama.add(self._sem_model_row)

        self._sem_ollama_test_row = Adw.ActionRow(
            title="Test connection",
            subtitle="Embed a probe with the current URL + model")
        self._sem_ollama_test_btn = Gtk.Button(label="Test", valign=Gtk.Align.CENTER)
        self._sem_ollama_test_btn.connect("clicked", self._on_test_ollama)
        self._sem_ollama_test_row.add_suffix(self._sem_ollama_test_btn)
        self._sem_ollama_test_row.set_activatable_widget(self._sem_ollama_test_btn)
        ollama.add(self._sem_ollama_test_row)

        openai = Adw.PreferencesGroup(
            title="OpenAI-compatible (server)",
            description="A server exposing POST /v1/embeddings (llama.cpp, vLLM, "
                        "LocalAI, or a hosted endpoint). The API key is stored in "
                        "the OS keyring, never in the config or logs.")
        page.add(openai)

        self._sem_oai_url_row, self._sem_oai_url_entry = self._entry_row(
            "Server URL", "semantic.openai.url")
        self._sem_oai_url_entry.set_placeholder_text(
            f"{config.default('semantic.openai.url')} (default)")
        self._sem_oai_url_entry.connect(
            "changed", lambda *_: self._on_sem_openai_url_changed())
        openai.add(self._sem_oai_url_row)

        # API key per endpoint, in the keyring under its OWN name
        # (semantic_api_key:openai|<url>), never the Ask entry (D2).
        self._sem_oai_key_row, self._sem_oai_key_entry = self._key_row(
            "API key", self._sem_openai_secret_name)
        openai.add(self._sem_oai_key_row)

        self._sem_oai_external_row = self._caption_row(
            "⚠ This server is not local — the text of every note is sent to it "
            "while the index builds.")
        self._sem_oai_external_row.set_visible(False)
        openai.add(self._sem_oai_external_row)

        # Model list fetched from the server (D4), via the explicit ask_models
        # level with THIS endpoint's url/key — never list_for (that reads the Ask
        # settings). A server without a list endpoint (llama.cpp) is handled, not
        # treated as an error.
        self._sem_oai_model_combo = Adw.ComboRow(
            title="Model", subtitle="Fetched from the server")
        self._sem_oai_model_list = Gtk.StringList()
        self._sem_oai_model_combo.set_model(self._sem_oai_model_list)
        self._sem_oai_model_combo.connect(
            "notify::selected", self._on_sem_openai_model_selected)
        oai_refresh_btn = Gtk.Button(
            icon_name="view-refresh-symbolic", valign=Gtk.Align.CENTER,
            tooltip_text="Refresh model list")
        oai_refresh_btn.add_css_class("flat")
        oai_refresh_btn.connect("clicked", lambda *_: self._refresh_sem_openai_models())
        self._sem_oai_model_combo.add_suffix(oai_refresh_btn)
        openai.add(self._sem_oai_model_combo)

        # D6: openai with no model picked is configured-but-unusable — name it
        # here instead of letting the build fail silently. (A no-list server
        # serves its one model regardless, so this stays hidden for it.)
        self._sem_oai_no_list = False
        self._sem_oai_unusable_row = self._caption_row(
            "No model selected — semantic search won't run. Pick a model above "
            "(Refresh to load the list).")
        openai.add(self._sem_oai_unusable_row)

        self._sem_oai_test_row = Adw.ActionRow(
            title="Test connection",
            subtitle="Embed a probe with the current URL + model")
        self._sem_oai_test_btn = Gtk.Button(label="Test", valign=Gtk.Align.CENTER)
        self._sem_oai_test_btn.connect("clicked", self._on_test_openai)
        self._sem_oai_test_row.add_suffix(self._sem_oai_test_btn)
        self._sem_oai_test_row.set_activatable_widget(self._sem_oai_test_btn)
        openai.add(self._sem_oai_test_row)

        self._sem_oai_models_id = None
        self._reload_sem_openai_key()
        self._refresh_sem_openai_state()
        self._update_sem_openai_external_warning()

        # Grey out the group the selected backend does not use.
        self._sem_onnx_widgets = [local]
        self._sem_ollama_widgets = [ollama]
        self._sem_openai_widgets = [openai]
        subpage = self._subpage("Embedding", page)
        # Don't auto-focus the first entry: an empty (default) field would open
        # with a focus ring + placeholder, which looks half-filled. Let the
        # resting state show the plain title; the hint appears on click.
        subpage.connect("shown", lambda *_: self.set_focus(None))
        return subpage

    def _refresh_onnx_dir_row(self) -> None:
        """Show the active ONNX folder (and mark when it's the default)."""
        d = self._onnx_dir()
        default = config.DATA_DIR / "onnx"
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
        except Exception:
            logger.debug("could not preset the ONNX-dir initial folder", exc_info=True)

        dialog.select_folder(self.get_root(), None, self._on_onnx_dir_chosen)

    def _on_onnx_dir_chosen(self, dlg, result) -> None:
        try:
            gfile = dlg.select_folder_finish(result)
        except GLib.Error as exc:
            # A cancel and a real portal/backend failure raise the same type; stay
            # silent on cancel, surface a genuine failure instead of dropping it.
            if not dialogs.dialog_cancelled(exc):
                logger.warning("ONNX-folder chooser failed", exc_info=True)
                dialogs.show_error(self.get_root(), "Folder Selection Failed",
                                   "Could not open the folder chooser.")
            return
        if gfile is not None and gfile.get_path():
            self._on_onnx_dir_selected(gfile.get_path())

    def _probe_onnx_runtime(self) -> None:
        GLib.idle_add(self._sem_onnx_runtime_row.set_subtitle,
                      self._onnxruntime_status())

    def _onnxruntime_status(self) -> str:
        try:
            onnxruntime = importlib.import_module("onnxruntime")
            return (f"onnxruntime {onnxruntime.__version__} detected — recommended "
                    f"≥ {self._ONNX_RUNTIME_RECOMMENDED} for current models")
        except ModuleNotFoundError:
            # genuinely absent (the package itself isn't there) → install it
            return ("onnxruntime not found — install it (openSUSE: "
                    "python313-onnxruntime) or use the Flatpak build")
        except Exception:
            # onnxruntime is present but unloadable (bad native lib / wrong glibc-CUDA),
            # NOT absent — the common case
            logger.warning("onnxruntime is installed but failed to load", exc_info=True)
            return "onnxruntime is installed but failed to load — see the log."

    def _onnx_dir(self) -> Path:
        """The folder the backend loads model.onnx + tokenizer.json from (and the
        download writes to). Blank setting → the app data dir default."""
        return Path(config.get_setting(self._settings, "semantic.onnx.dir")
                    or str(config.DATA_DIR / "onnx"))

    def _onnx_paths(self):
        """Resolve the ONNX model + tokenizer file paths inside the folder."""
        d = self._onnx_dir()
        return d / "model.onnx", d / "tokenizer.json"

    def _on_onnx_dir_selected(self, path: str) -> None:
        config.set_setting(self._settings, "semantic.onnx.dir", path)
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
               or config.default("semantic.ollama.url"))
        model = (self._sem_model_entry.get_text().strip()
                 or config.default("semantic.ollama.model"))
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
        except Exception as exc:  # noqa: BLE001 — connection test surfaces any failure to the UI
            ok, msg = False, f"Failed: {exc}"
            logger.info("Ollama test failed: %s", exc)
        GLib.idle_add(self._test_done, button, self._sem_ollama_test_row, ok, msg)

    # ── OpenAI-compatible embedding backend ────────────────────────────

    def _sem_openai_url(self) -> str:
        return (config.get_setting(self._settings, "semantic.openai.url")
                or config.default("semantic.openai.url"))

    def _sem_openai_secret_name(self) -> str:
        """Keyring name of this endpoint's key — its own prefix, never the Ask
        entry, so a key stays with the server it was entered for (D2)."""
        from markdown_vault.search.semantic_search import semantic_secret_name
        return semantic_secret_name(self._sem_openai_url())

    def _reload_sem_openai_key(self) -> None:
        """Show the key of the currently configured embedding server (flushing a
        pending edit first, so it still reaches the server it was typed for)."""
        from markdown_vault.core import secret_store
        if not self._sem_oai_key_entry.get_sensitive():
            return                      # no keyring — the field is disabled
        self._flush_secret()
        name = self._sem_openai_secret_name()
        stored = secret_store.get_secret(name)
        self._secret_updating = True
        try:
            self._sem_oai_key_entry.set_text(stored)
        finally:
            self._secret_updating = False
        if stored:
            self._known_secrets.add(name)
        else:
            self._known_secrets.discard(name)

    def _on_sem_openai_url_changed(self) -> None:
        self._update_sem_openai_external_warning()
        self._schedule_sem_openai_refresh()

    def _schedule_sem_openai_refresh(self, delay_ms: int = 700) -> None:
        if config.get_setting(self._settings, "semantic.backend") != "openai":
            return
        if self._sem_oai_models_id is not None:
            GLib.source_remove(self._sem_oai_models_id)
        self._sem_oai_models_id = GLib.timeout_add(
            delay_ms, self._sem_openai_models_timeout)

    def _sem_openai_models_timeout(self) -> bool:
        """The URL has settled — it now identifies a (possibly different) server,
        so re-load that server's key and fetch its models."""
        self._sem_oai_models_id = None
        self._reload_sem_openai_key()
        self._refresh_sem_openai_models()
        return False

    def _on_sem_openai_model_selected(self, combo, _pspec) -> None:
        item = combo.get_selected_item()
        if item is not None:
            config.set_setting(self._settings, "semantic.openai.model", item.get_string())
            self._persist_debounced()
            self._refresh_sem_openai_state()

    def _refresh_sem_openai_models(self) -> None:
        """Fetch the model list off the main thread via the explicit ask_models
        level (D4), with this endpoint's own url + key. `probe` also *writes* the
        shared per-endpoint status/cache, so it is called with `record=False`
        (below) — the embedding verdict must not mute Ask on the same server."""
        from markdown_vault.core import secret_store
        from markdown_vault.search import ask_models
        url = self._sem_openai_url()
        key = secret_store.get_secret(self._sem_openai_secret_name())
        self._sem_oai_model_combo.set_subtitle("Loading…")

        def worker():
            # record=False: this is a *second* consumer of the shared endpoint
            # keying — a failed embedding probe must not write the shared status
            # and mute Ask on the same server (D4/ZB1).
            GLib.idle_add(self._populate_sem_openai_models,
                          ask_models.probe("openai", url, key, record=False))

        threading.Thread(target=worker, daemon=True).start()

    def _populate_sem_openai_models(self, status) -> bool:
        """Render what the server answered — *status* is an ``ask_models``
        :class:`EndpointStatus`; only the LISTING half is used (``can_ask`` is
        chat-shaped and meaningless here)."""
        from markdown_vault.search import ask_models
        self._sem_oai_no_list = (status.state == ask_models.NO_LIST)
        if status.state in (ask_models.UNREACHABLE, ask_models.UNAUTHORIZED,
                            ask_models.LIST_ERROR):
            self._sem_oai_model_combo.set_subtitle(f"Not reachable: {status.error}")
            self._sem_oai_model_combo.add_css_class("error")
            self._refresh_sem_openai_state()
            return False
        self._sem_oai_model_combo.remove_css_class("error")
        if status.state == ask_models.NO_LIST:
            self._sem_oai_model_combo.set_subtitle(
                "This server does not list models — it serves a fixed one")
            self._refresh_sem_openai_state()
            return False
        models = status.models
        if not models:
            self._sem_oai_model_combo.set_subtitle("No models on the server")
            self._refresh_sem_openai_state()
            return False
        current = config.get_setting(self._settings, "semantic.openai.model")
        self._sem_oai_model_list.splice(
            0, self._sem_oai_model_list.get_n_items(), models)
        if current in models:
            self._sem_oai_model_combo.set_selected(models.index(current))
        else:
            self._sem_oai_model_combo.set_selected(0)
            config.set_setting(self._settings, "semantic.openai.model", models[0])
            self._persist_debounced()
        self._sem_oai_model_combo.set_subtitle(f"{len(models)} models")
        self._refresh_sem_openai_state()
        return False

    def _refresh_sem_openai_state(self) -> None:
        """D6: reveal the 'no model → unusable' caption only when the server does
        list models and none is picked. A no-list server serves its one model
        regardless, so an empty name is fine there."""
        model = (config.get_setting(self._settings, "semantic.openai.model") or "").strip()
        self._sem_oai_unusable_row.set_visible(not model and not self._sem_oai_no_list)

    def _update_sem_openai_external_warning(self) -> None:
        """Reveal the 'notes leave the device' warning for a non-local server."""
        url = (self._sem_oai_url_entry.get_text().strip()
               or config.get_setting(self._settings, "semantic.openai.url", ""))
        self._sem_oai_external_row.set_visible(
            config.get_setting(self._settings, "semantic.backend") == "openai"
            and not self._is_local_url(url))

    def _on_test_openai(self, button) -> None:
        from markdown_vault.core import secret_store
        url = (self._sem_oai_url_entry.get_text().strip()
               or config.default("semantic.openai.url"))
        model = (config.get_setting(self._settings, "semantic.openai.model") or "").strip()
        key = secret_store.get_secret(self._sem_openai_secret_name())
        button.set_sensitive(False)
        self._sem_oai_test_row.set_subtitle("Testing…")
        threading.Thread(target=self._test_openai_worker,
                         args=(button, url, model, key), daemon=True).start()

    def _test_openai_worker(self, button, url, model, key) -> None:
        try:
            from markdown_vault.search.semantic_search import OpenAIEmbedder
            vec = OpenAIEmbedder(model, url, key).embed(["connection test"])
            dim = len(vec[0]) if vec else 0
            ok, msg = True, f"Connected — embeds OK (dim {dim})"
        except Exception as exc:  # noqa: BLE001 — connection test surfaces any failure to the UI
            ok, msg = False, f"Failed: {exc}"
            logger.info("OpenAI embedding test failed: %s", exc)
        GLib.idle_add(self._test_done, button, self._sem_oai_test_row, ok, msg)

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
        except Exception as exc:  # noqa: BLE001 — connection test surfaces any failure to the UI
            ok, msg = False, f"Failed: {exc}"
            logger.info("ONNX test failed: %s", exc)
        GLib.idle_add(self._test_onnx_done, button, ok, msg)

    def _test_onnx_done(self, button, ok, msg) -> bool:
        button.set_sensitive(True)
        try:
            self.add_toast(Adw.Toast.new(msg))
        except Exception:  # noqa: BLE001 — toast is cosmetic; fall back to a log line
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
        except Exception:  # noqa: BLE001 — toast is cosmetic; fall back to a log line
            logger.info("%s", msg)
        return False

    def _on_download_onnx(self, button, which) -> None:
        model_p, tok_p = self._onnx_paths()
        if which == "model":
            url = (self._sem_model_url_entry.get_text().strip()
                   or config.default("semantic.onnx.model_url"))
            filename, bar, target = "model.onnx", self._sem_model_progress, model_p
        else:
            url = (self._sem_tok_url_entry.get_text().strip()
                   or config.default("semantic.onnx.tokenizer_url"))
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
                f"Downloaded {filename} ({mb:.0f} MB)", refresh)
        except Exception as exc:  # noqa: BLE001 — network/IO/permission — report, don't crash
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
        except Exception:  # noqa: BLE001 — toast is cosmetic; fall back to a log line
            logger.info("%s", msg)
        (refresh or self._refresh_onnx_status)()  # a fetched file flips the state
        return False
