"""Preferences — Ask prompt subpage: the editable system prompt."""

import logging

import gi

logger = logging.getLogger(__name__)

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw


class PromptSubpageMixin:
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

    def _on_ask_prompt_changed(self, buffer) -> None:
        from markdown_vault.search import ask as _ask
        text = buffer.get_text(
            buffer.get_start_iter(), buffer.get_end_iter(), False)
        # Store empty when unchanged from the built-in default, so the prompt
        # keeps tracking future improvements instead of pinning this snapshot.
        self._settings["ask_system_prompt"] = (
            "" if text.strip() == _ask.DEFAULT_SYSTEM_PROMPT.strip() else text)
        self._persist_debounced()
