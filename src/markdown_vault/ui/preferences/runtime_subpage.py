"""Preferences — Ask runtime subpage: threads, GPU offload, KV cache, batch sizes and the answer-length cap of the in-process backend."""

import logging

import gi

logger = logging.getLogger(__name__)

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw

from markdown_vault.core import config


class RuntimeSubpageMixin:
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

    def _refresh_gpu_recommendation(self) -> None:
        """Set the GPU-layers subtitle to a hardware-aware recommendation for the
        selected model (updates when the model changes)."""
        base = "Layers offloaded to the GPU. 0 = pure CPU, 999 = all."
        from markdown_vault.search import llama_runtime
        advice = llama_runtime.gpu_layers_advice(
            config.resolve_model_path(self._settings))
        self._ask_gpu_row.set_subtitle(f"{base} {advice}" if advice else base)
