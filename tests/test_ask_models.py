"""Tests for markdown_vault.search.ask_models — the model list per Ask backend.

Shared by Preferences and the Quick-Open footer picker. Network is mocked; the point
of the module is that *which* models exist, *which* setting selects one, and *whether
a lookup may block* all depend on the active backend.
"""
import io
import json
import unittest
from unittest.mock import patch

from markdown_vault.search import ask_models


def _response(payload):
    return io.BytesIO(json.dumps(payload).encode())


class TestBackendMapping(unittest.TestCase):
    """Each backend has its own endpoint and its own setting key — mixing them up is
    the bug this module exists to prevent (a picker writing ask_gguf_path while the
    server backend reads ask_model)."""

    def test_setting_key(self):
        self.assertEqual(ask_models.setting_key("local"), "ask_gguf_path")
        self.assertEqual(ask_models.setting_key("ollama"), "ask_model")
        self.assertEqual(ask_models.setting_key("openai"), "ask_model")

    def test_auto_engine_is_always_local(self):
        # "auto" configures everything and uses the in-process backend, whatever
        # ask_backend says — offering server models there would be a lie.
        self.assertEqual(
            ask_models.effective_backend({"ask_engine": "auto",
                                          "ask_backend": "openai"}), "local")
        self.assertEqual(
            ask_models.effective_backend({"ask_engine": "manual",
                                          "ask_backend": "openai"}), "openai")

    def test_endpoint(self):
        self.assertEqual(ask_models.endpoint("ollama"), "/api/tags")
        self.assertEqual(ask_models.endpoint("openai"), "/v1/models")
        self.assertIsNone(ask_models.endpoint("local"))


class TestFetch(unittest.TestCase):
    """Both server shapes, and the bearer token when one is configured."""

    def setUp(self):
        ask_models.clear_cache()

    def test_parses_ollama_tags(self):
        with patch("urllib.request.urlopen",
                   return_value=_response({"models": [{"name": "llama3.2"},
                                                      {"name": "qwen3"}]})):
            self.assertEqual(ask_models.fetch("ollama", "http://h:11434"),
                             ["llama3.2", "qwen3"])

    def test_parses_openai_models(self):
        with patch("urllib.request.urlopen",
                   return_value=_response({"data": [{"id": "Qwen3.5-122B"}]})):
            self.assertEqual(ask_models.fetch("openai", "http://h:8080"),
                             ["Qwen3.5-122B"])

    def test_openai_url_with_v1_is_not_doubled(self):
        captured = {}

        def fake_request(url, **kw):
            captured["url"] = url
            captured["headers"] = kw.get("headers", {})
            return object()

        with patch("urllib.request.Request", side_effect=fake_request), \
                patch("urllib.request.urlopen", return_value=_response({"data": []})):
            ask_models.fetch("openai", "https://host/v1", api_key="sk-x")
        self.assertEqual(captured["url"], "https://host/v1/models")
        self.assertEqual(captured["headers"].get("Authorization"), "Bearer sk-x")

    def test_no_auth_header_without_key(self):
        captured = {}

        def fake_request(url, **kw):
            captured["headers"] = kw.get("headers", {})
            return object()

        with patch("urllib.request.Request", side_effect=fake_request), \
                patch("urllib.request.urlopen", return_value=_response({"models": []})):
            ask_models.fetch("ollama", "http://h:11434")
        self.assertNotIn("Authorization", captured["headers"])


class TestListFor(unittest.TestCase):
    """What the picker gets — and, for a server backend, that asking never blocks."""

    def setUp(self):
        ask_models.clear_cache()

    def test_local_lists_downloaded_gguf(self):
        with patch("markdown_vault.core.config.list_models",
                   return_value=["/models/a.gguf", "/models/b.gguf"]):
            models = ask_models.list_for({"ask_backend": "local"})
        self.assertEqual(models, [("a.gguf", "/models/a.gguf"),
                                  ("b.gguf", "/models/b.gguf")])

    def test_server_without_cache_does_not_block(self):
        # Must not touch the network on this call — the palette opens on Ctrl+Space.
        started = []
        with patch("urllib.request.urlopen",
                   side_effect=AssertionError("must not fetch synchronously")), \
                patch.object(ask_models, "prime", side_effect=lambda *a, **k: started.append(a)):
            models = ask_models.list_for({"ask_engine": "manual", "ask_backend": "openai",
                                          "ask_ollama_url": "http://h:8080",
                                          "ask_model": "Qwen"})
        self.assertEqual(models, [("Qwen", "Qwen")])   # the configured one, nothing invented
        self.assertTrue(started, "a background refresh should have been kicked off")

    def test_server_uses_cache_when_present(self):
        ask_models.cache_put("openai", "http://h:8080", ["m1", "m2"])
        with patch("urllib.request.urlopen",
                   side_effect=AssertionError("must not fetch synchronously")):
            models = ask_models.list_for({"ask_engine": "manual", "ask_backend": "openai",
                                          "ask_ollama_url": "http://h:8080"})
        self.assertEqual(models, [("m1", "m1"), ("m2", "m2")])

    def test_local_never_lists_server_models_and_vice_versa(self):
        # The "lying control": a server backend must not offer local .gguf files,
        # because selecting one would write ask_gguf_path and change nothing.
        ask_models.cache_put("ollama", "http://h:11434", ["llama3.2"])
        with patch("markdown_vault.core.config.list_models",
                   return_value=["/models/a.gguf", "/models/b.gguf"]):
            server = ask_models.list_for({"ask_engine": "manual", "ask_backend": "ollama",
                                          "ask_ollama_url": "http://h:11434"})
        self.assertEqual(server, [("llama3.2", "llama3.2")])
        self.assertNotIn("a.gguf", [n for n, _ in server])


class TestPerEndpointMemory(unittest.TestCase):
    """The chosen model belongs to the endpoint, not to the app: switching provider
    must not leave the other provider's model selected (it would be sent to a server
    that does not have it), and switching back should restore the earlier choice."""

    def test_remember_and_recall_per_endpoint(self):
        s = {}
        ask_models.remember(s, "ollama", "http://localhost:11434", "llama3.2")
        ask_models.remember(s, "openai", "https://llm.example.com/v1", "Qwen3.5-122B")
        self.assertEqual(ask_models.recall(s, "ollama", "http://localhost:11434"),
                         "llama3.2")
        self.assertEqual(ask_models.recall(s, "openai", "https://llm.example.com/v1"),
                         "Qwen3.5-122B")

    def test_recall_is_empty_for_an_unknown_endpoint(self):
        self.assertEqual(ask_models.recall({}, "openai", "https://new-host"), "")

    def test_openai_url_variants_are_the_same_endpoint(self):
        # host and host/v1 address the same server — remembering under both would
        # silently lose the choice when the URL is written the other way.
        s = {}
        ask_models.remember(s, "openai", "https://h/v1", "Qwen")
        self.assertEqual(ask_models.recall(s, "openai", "https://h"), "Qwen")

    def test_switching_backend_replaces_the_active_model(self):
        s = {"ask_backend": "ollama", "ask_ollama_url": "http://localhost:11434",
             "ask_model": "llama3.2"}
        ask_models.remember(s, "ollama", "http://localhost:11434", "llama3.2")
        ask_models.activate(s, "openai", "https://llm.example.com")
        self.assertEqual(s["ask_model"], "")        # nothing pretends to be selected
        ask_models.remember(s, "openai", "https://llm.example.com", "Qwen")
        ask_models.activate(s, "ollama", "http://localhost:11434")
        self.assertEqual(s["ask_model"], "llama3.2")   # the old choice comes back


class TestPerBackendUrl(unittest.TestCase):
    """The server URL belongs to the provider too: one shared ask_ollama_url means
    switching backend either loses a hand-typed URL or — worse — points the new
    backend at the other one's host."""

    def test_recall_falls_back_to_the_backends_default_port(self):
        self.assertEqual(ask_models.recall_url({}, "ollama"),
                         "http://localhost:11434")
        self.assertEqual(ask_models.recall_url({}, "openai"),
                         "http://localhost:8080")

    def test_remembered_url_wins_over_the_default(self):
        s = {}
        ask_models.remember_url(s, "openai", "https://llm.example.com")
        self.assertEqual(ask_models.recall_url(s, "openai"),
                         "https://llm.example.com")
        self.assertEqual(ask_models.recall_url(s, "ollama"),
                         "http://localhost:11434")   # untouched

    def test_switch_backend_keeps_each_providers_url(self):
        s = {"ask_backend": "openai", "ask_ollama_url": "https://llm.example.com"}
        ask_models.remember(s, "openai", "https://llm.example.com", "Qwen")
        ask_models.switch_backend(s, "openai", "ollama")
        self.assertEqual(s["ask_backend"], "ollama")
        self.assertEqual(s["ask_ollama_url"], "http://localhost:11434")
        self.assertEqual(s["ask_model"], "")          # nothing chosen there yet
        ask_models.switch_backend(s, "ollama", "openai")
        self.assertEqual(s["ask_ollama_url"], "https://llm.example.com")
        self.assertEqual(s["ask_model"], "Qwen")      # and its model comes back

    def test_local_in_between_does_not_clobber_a_remembered_url(self):
        s = {"ask_backend": "openai", "ask_ollama_url": "https://llm.example.com"}
        ask_models.switch_backend(s, "openai", "local")
        ask_models.switch_backend(s, "local", "openai")
        self.assertEqual(s["ask_ollama_url"], "https://llm.example.com")


class TestPerEndpointKey(unittest.TestCase):
    """And the API key: one shared key would be sent to whatever server is
    configured next — a key for a paid external provider must not travel to the
    next endpoint the user types in."""

    def setUp(self):
        self.store = {}
        self._p = patch.multiple(
            "markdown_vault.core.secret_store",
            get_secret=lambda k: self.store.get(k, ""),
            set_secret=lambda k, v: (self.store.__setitem__(k, v) if v
                                     else self.store.pop(k, None), True)[1])
        self._p.start()
        self.addCleanup(self._p.stop)

    def test_name_is_per_endpoint_and_stable(self):
        a = ask_models.secret_name("openai", "https://h/v1")
        self.assertEqual(a, ask_models.secret_name("openai", "https://h"))
        self.assertNotEqual(a, ask_models.secret_name("ollama", "https://h"))

    def test_key_is_read_for_the_active_endpoint(self):
        settings = {"ask_engine": "manual", "ask_backend": "openai",
                    "ask_ollama_url": "https://llm.example.com"}
        self.store[ask_models.secret_name("openai", "https://llm.example.com")] = "sk-x"
        self.assertEqual(ask_models.api_key(settings), "sk-x")
        settings["ask_ollama_url"] = "http://localhost:8080"
        self.assertEqual(ask_models.api_key(settings), "")   # other server, no key

    def test_legacy_key_is_adopted_once_for_the_configured_endpoint(self):
        settings = {"ask_engine": "manual", "ask_backend": "openai",
                    "ask_ollama_url": "https://llm.example.com"}
        self.store["ask_api_key"] = "sk-old"
        self.assertTrue(ask_models.adopt_legacy_key(settings))
        self.assertEqual(ask_models.api_key(settings), "sk-old")
        self.assertNotIn("ask_api_key", self.store)   # not left lying around
        self.assertFalse(ask_models.adopt_legacy_key(settings))   # nothing to do

    def test_adoption_never_overwrites_an_endpoint_key(self):
        settings = {"ask_engine": "manual", "ask_backend": "openai",
                    "ask_ollama_url": "https://llm.example.com"}
        self.store[ask_models.secret_name("openai", "https://llm.example.com")] = "sk-new"
        self.store["ask_api_key"] = "sk-old"
        self.assertFalse(ask_models.adopt_legacy_key(settings))
        self.assertEqual(ask_models.api_key(settings), "sk-new")


class TestCurrent(unittest.TestCase):
    """Which value the picker should preselect — per backend."""

    def test_server_uses_ask_model(self):
        self.assertEqual(
            ask_models.current({"ask_engine": "manual", "ask_backend": "openai",
                                "ask_model": "Qwen"}), "Qwen")

    def test_local_uses_resolved_gguf_path(self):
        with patch("markdown_vault.core.config.resolve_model_path",
                   return_value="/models/a.gguf"):
            self.assertEqual(ask_models.current({"ask_backend": "local"}),
                             "/models/a.gguf")


if __name__ == "__main__":
    unittest.main()
