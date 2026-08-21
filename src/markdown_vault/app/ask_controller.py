"""Everything the Ask palette needs from the application.

Eleven of the fourteen callbacks the palette used to receive from ``MainWindow``
were about asking questions of the vault — answering, offering candidate notes,
picking a model, and saying why asking is unavailable. That is a surface, not a
window concern, and it now lives in one object the palette can be handed whole.

What it needs from the window is deliberately three things it cannot own: the
settings, the semantic index (which comes and goes at runtime, hence a getter
rather than a reference), and the current search scope. The fourth wire runs the
other way — when a background probe of the Ask server settles, the palette has to
be told, so it is registered once via :meth:`bind_palette`.
"""

import logging

import gi

logger = logging.getLogger(__name__)

gi.require_version("Gtk", "4.0")

from gi.repository import GLib

from markdown_vault.core import config

#: Locale code → English language name for the "answer in {language}" prompt.
LANG_NAMES = {
    "de": "German", "en": "English", "fr": "French", "es": "Spanish",
    "it": "Italian", "pt": "Portuguese", "nl": "Dutch", "pl": "Polish",
    "ru": "Russian", "tr": "Turkish", "cs": "Czech", "sv": "Swedish",
    "da": "Danish", "fi": "Finnish", "no": "Norwegian", "uk": "Ukrainian",
    "ja": "Japanese", "zh": "Chinese", "ko": "Korean",
}


class AskController:
    """The Ask surface: answers, candidates, models, and availability."""

    def __init__(self, settings: dict, *, get_semantic_index, get_scope_paths) -> None:
        self._settings = settings
        self._get_index = get_semantic_index
        self._get_scope_paths = get_scope_paths
        self._palette = None
        self._logged_local_reason = None   # debounce: log a local model fault once

    def bind_palette(self, palette) -> None:
        """Register the palette to refresh when a server probe settles."""
        self._palette = palette

    # ── answering ──────────────────────────────────────────────────

    def answer(self, question: str, note_paths=None, on_phase=None,
               on_token=None, should_cancel=None):
        """RAG: retrieve passages and let the configured model write a grounded
        answer. Runs off the main thread (the palette's worker) and returns an
        :class:`ask.Answer`. *note_paths*, if given, uses exactly those
        user-picked notes as context instead of retrieving. *on_phase* is the UI
        status hook (loading/thinking).
        """
        from markdown_vault.search import ask
        return ask.answer_question(
            question, self._get_index(), self._settings,
            self._get_scope_paths(), self.answer_language(),
            note_paths=note_paths, on_phase=on_phase, on_token=on_token,
            should_cancel=should_cancel)

    def answer_from(self, question: str, note_paths, on_phase=None,
                    on_token=None, should_cancel=None):
        """Answer from exactly *note_paths* — the "pick your own sources" flow."""
        return self.answer(question, note_paths=note_paths, on_phase=on_phase,
                           on_token=on_token, should_cancel=should_cancel)

    def candidates(self, question: str):
        """Top-20 candidate notes ``(path, score)`` for the source picker — the
        same scoped, hybrid retrieval, just wider and without the model."""
        index = self._get_index()
        if index is None:
            return []
        hits = index.retrieve(question, top_k=20, vaults=self._get_scope_paths(),
                              hybrid=bool(self._settings.get("ask_hybrid")))
        return [(c.path, s) for c, s in hits]

    def answer_language(self) -> str:
        """The language the answer is written in — the user's OS UI language,
        falling back to English."""
        for loc in GLib.get_language_names():
            code = loc.split(".")[0].split("_")[0].lower()
            if code and code not in ("c", "posix"):
                return LANG_NAMES.get(code, code)
        return "English"

    def top_k(self) -> int:
        return int(self._settings.get("ask_top_k") or config.default("ask_top_k"))

    # ── availability ───────────────────────────────────────────────

    def unavailable_reason(self) -> str:
        """Why Ask cannot answer right now — ``""`` when it can. One source for
        both the toggle's state and its tooltip, so they cannot disagree."""
        if not self._settings.get("semantic_search_enabled"):
            return "Semantic search is off — turn it on in Preferences → Search."
        if self._get_index() is None:
            return "The semantic index is not ready yet."
        engine = self._settings.get("ask_engine") or config.default("ask_engine")
        if engine == "off":
            return ("The answer engine is off — turn it on in "
                    "Preferences → Search → Ask.")
        return ""

    def can_ask(self) -> bool:
        return not self.unavailable_reason()

    # ── the server and its models ──────────────────────────────────

    def _server_url(self) -> str:
        return (self._settings.get("ask_ollama_url")
                or config.default("ask_ollama_url"))

    def endpoint_status(self):
        """The verdict the palette blocks and banners on: the Ask server's last
        word, or — for the **local** backend — a blocking verdict when the chosen
        GGUF cannot load (``None`` when it can, read as "nothing to check").

        The local reason is fed the *wanted* path (folder + stored filename), not
        ``resolve_model_path``'s result: that is ``""`` for a gone choice and would
        make the banner read "(unset)" instead of naming the chosen model."""
        from markdown_vault.core import config
        from markdown_vault.search import ask_models, llama_runtime
        backend = ask_models.effective_backend(self._settings)
        if backend not in ask_models.SERVER_BACKENDS:
            reason = llama_runtime.availability(
                config.ask_gguf_wanted_path(self._settings))
            if reason:
                if reason != self._logged_local_reason:   # debounced: resolve runs
                    logger.warning("Ask: local model unavailable — %s", reason)
                    self._logged_local_reason = reason     # per answer, not once
                return ask_models.local_unavailable(reason)
            self._logged_local_reason = None
            return None
        return ask_models.status(backend, self._server_url())

    def recheck_endpoint(self) -> None:
        """Probe the server again — the palette's "Try again", so a server started
        in the meantime becomes usable without restarting the app."""
        from markdown_vault.search import ask_models
        backend = ask_models.effective_backend(self._settings)
        if backend not in ask_models.SERVER_BACKENDS:
            return
        ask_models.refresh_async(backend, self._server_url(),
                                 ask_models.api_key(self._settings),
                                 on_settled=self._probe_settled)
        self._refresh_palette()                  # show the check is running

    def _probe_settled(self, _status) -> None:
        """A background probe finished (worker thread) — update the palette on the
        main loop: picker, warning banner, submit lock, and a held question."""
        GLib.idle_add(self._refresh_palette)

    def _refresh_palette(self) -> bool:
        if self._palette is not None:
            self._palette.refresh_endpoint_status()
        return False                             # usable as an idle callback

    def list_models(self):
        """``(label, value)`` for the footer picker — downloaded GGUFs, or the
        models the configured server offers."""
        from markdown_vault.search import ask_models
        return ask_models.list_for(self._settings, on_refresh=self._probe_settled)

    def current_model(self) -> str:
        """The model the next answer would use — a GGUF path or a server model."""
        from markdown_vault.search import ask_models
        return ask_models.current(self._settings)

    def select_model(self, value: str) -> None:
        """Remember *value* for the active endpoint; the next answer uses it.
        Which setting that writes depends on the backend, so ask_models decides."""
        from markdown_vault.search import ask_models
        backend = ask_models.effective_backend(self._settings)
        ask_models.remember(self._settings, backend, self._server_url(), value)
        config.save_settings(self._settings)
