"""Preferences — Embedding subpage: the vector backend, its model files, downloads and the reachability self-tests."""

import logging
import threading
from pathlib import Path

import gi

logger = logging.getLogger(__name__)

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw, GLib, Gio

from markdown_vault.core import config

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
            "Model Download", "semantic_onnx_model_url", trailing=model_btn)
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
            "Tokenizer Download", "semantic_onnx_tokenizer_url", trailing=tok_btn)
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

    def _onnx_dir(self) -> Path:
        """The folder the backend loads model.onnx + tokenizer.json from (and the
        download writes to). Blank setting → the app data dir default."""
        return Path(self._settings.get("semantic_onnx_dir")
                    or str(config.DATA_DIR / "onnx"))

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
                f"Downloaded {filename} ({mb:.0f} MB)", refresh)
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
