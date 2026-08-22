"""Tests for markdown_vault.search.ask_models — the model list per Ask backend.

Shared by Preferences and the Quick-Open footer picker. Network is mocked; the point
of the module is that *which* models exist, *which* setting selects one, and *whether
a lookup may block* all depend on the active backend.
"""
import io
import json
import threading
import unittest
import urllib.error
from unittest.mock import patch

from markdown_vault.core import config
from markdown_vault.search import ask_models


def _response(payload):
    return io.BytesIO(json.dumps(payload).encode())


class TestBackendMapping(unittest.TestCase):
    """Each backend has its own endpoint and its own setting key — mixing them up is
    the bug this module exists to prevent (a picker writing ask_gguf_path while the
    server backend reads ask_model)."""

    def test_setting_key(self):
        self.assertEqual(ask_models.setting_key("local"), "ask.gguf.path")
        self.assertEqual(ask_models.setting_key("ollama"), "ask.server.model")
        self.assertEqual(ask_models.setting_key("openai"), "ask.server.model")

    def test_auto_engine_is_always_local(self):
        # "auto" configures everything and uses the in-process backend, whatever
        # ask_backend says — offering server models there would be a lie.
        self.assertEqual(
            ask_models.effective_backend({"ask": {"engine": "auto",
                                                  "backend": "openai"}}), "local")
        self.assertEqual(
            ask_models.effective_backend({"ask": {"engine": "manual",
                                                  "backend": "openai"}}), "openai")

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
            models = ask_models.list_for({"ask": {"backend": "local"}})
        self.assertEqual(models, [("a.gguf", "/models/a.gguf"),
                                  ("b.gguf", "/models/b.gguf")])

    def test_server_without_cache_does_not_block(self):
        # Must not touch the network on this call — the palette opens on Ctrl+Space.
        started = []
        with patch("urllib.request.urlopen",
                   side_effect=AssertionError("must not fetch synchronously")), \
                patch.object(ask_models, "refresh_async",
                             side_effect=lambda *a, **k: started.append(a)):
            models = ask_models.list_for({"ask": {"engine": "manual", "backend": "openai",
                                                  "server": {"url": "http://h:8080",
                                                             "model": "Qwen"}}})
        self.assertEqual(models, [("Qwen", "Qwen")])   # the configured one, nothing invented
        self.assertTrue(started, "a background refresh should have been kicked off")

    def test_server_uses_cache_when_present(self):
        ask_models.cache_put("openai", "http://h:8080", ["m1", "m2"])
        with patch("urllib.request.urlopen",
                   side_effect=AssertionError("must not fetch synchronously")), \
                patch.object(ask_models, "refresh_async"):  # the probe is a thread
            models = ask_models.list_for({"ask": {"engine": "manual", "backend": "openai",
                                                  "server": {"url": "http://h:8080"}}})
        self.assertEqual(models, [("m1", "m1"), ("m2", "m2")])

    def test_local_never_lists_server_models_and_vice_versa(self):
        # The "lying control": a server backend must not offer local .gguf files,
        # because selecting one would write ask_gguf_path and change nothing.
        ask_models.cache_put("ollama", "http://h:11434", ["llama3.2"])
        with patch("markdown_vault.core.config.list_models",
                   return_value=["/models/a.gguf", "/models/b.gguf"]):
            server = ask_models.list_for({"ask": {"engine": "manual", "backend": "ollama",
                                                  "server": {"url": "http://h:11434"}}})
        self.assertEqual(server, [("llama3.2", "llama3.2")])
        self.assertNotIn("a.gguf", [n for n, _ in server])


class TestEndpointStatus(unittest.TestCase):
    """The server's answer to the model-list request is classified once, here, and
    both surfaces read the result. The distinction that matters: a server that is
    *unreachable* or *rejects the key* cannot answer a question either — anything
    else it says may warn, but must never take asking away (llama.cpp lists no
    models at all and answers perfectly well).
    """

    def setUp(self):
        ask_models.clear_cache()

    def _settle(self, side_effect):
        done = threading.Event()
        with patch("urllib.request.urlopen", side_effect=side_effect):
            ask_models.refresh_async("openai", "http://h:8080",
                                     on_settled=lambda st: done.set())
            self.assertTrue(done.wait(3), "must report back in every path")
        return ask_models.status("openai", "http://h:8080")

    def _http_error(self, code):
        return urllib.error.HTTPError("http://h:8080/v1/models", code, "nope", {}, None)

    def test_unknown_before_anything_happened(self):
        st = ask_models.status("openai", "http://nothing-probed-yet")
        self.assertEqual(st.state, ask_models.UNKNOWN)
        self.assertTrue(st.can_ask)          # never block on an unprobed endpoint
        self.assertEqual(st.message, "")

    def test_models_listed_is_ok_and_silent(self):
        st = self._settle(lambda *a, **k: _response({"data": [{"id": "m1"}]}))
        self.assertEqual(st.state, ask_models.OK)
        self.assertEqual(st.models, ["m1"])
        self.assertEqual(st.message, "")
        self.assertTrue(st.can_ask)
        self.assertTrue(st.models_usable)

    def test_empty_list_warns_but_still_allows_asking(self):
        # An OpenAI-compatible gateway may serve a fixed model and list nothing.
        st = self._settle(lambda *a, **k: _response({"data": []}))
        self.assertEqual(st.state, ask_models.EMPTY)
        self.assertTrue(st.can_ask)
        self.assertFalse(st.models_usable)
        self.assertTrue(st.message)

    def test_missing_list_endpoint_is_not_an_error(self):
        # llama.cpp loads one model at startup and has no list — asking must work,
        # and there is nothing to warn about.
        st = self._settle(self._http_error(404))
        self.assertEqual(st.state, ask_models.NO_LIST)
        self.assertTrue(st.can_ask)
        self.assertFalse(st.models_usable)
        self.assertEqual(st.message, "")

    def test_unreadable_answer_is_treated_as_no_list(self):
        st = self._settle(lambda *a, **k: io.BytesIO(b"<html>nope</html>"))
        self.assertEqual(st.state, ask_models.NO_LIST)
        self.assertTrue(st.can_ask)

    def test_rejected_key_blocks_asking(self):
        # The same auth guards the chat endpoint, so this is certain to fail.
        st = self._settle(self._http_error(401))
        self.assertEqual(st.state, ask_models.UNAUTHORIZED)
        self.assertFalse(st.can_ask)
        self.assertIn("key", st.message.lower())

    def test_forbidden_is_also_unauthorized(self):
        self.assertEqual(self._settle(self._http_error(403)).state,
                         ask_models.UNAUTHORIZED)

    def test_no_answer_at_all_blocks_asking(self):
        st = self._settle(urllib.error.URLError("Connection refused"))
        self.assertEqual(st.state, ask_models.UNREACHABLE)
        self.assertFalse(st.can_ask)
        self.assertIn("http://h:8080", st.message)

    def test_other_http_errors_warn_but_do_not_block(self):
        # 500/429/redirect loop: the server answered, but said nothing about the
        # chat endpoint. Warning is right, taking asking away is not.
        for code in (429, 500):
            st = self._settle(self._http_error(code))
            self.assertEqual(st.state, ask_models.LIST_ERROR, code)
            self.assertTrue(st.can_ask, code)
            self.assertTrue(st.message, code)

    def test_probing_while_the_request_is_out(self):
        started, release, settled = (threading.Event(), threading.Event(),
                                     threading.Event())

        def slow(*a, **k):
            started.set()
            release.wait(3)
            return _response({"data": [{"id": "m1"}]})

        with patch("urllib.request.urlopen", side_effect=slow):
            ask_models.refresh_async("openai", "http://h:8080",
                                     on_settled=lambda st: settled.set())
            self.assertTrue(started.wait(3))
            st = ask_models.status("openai", "http://h:8080")
            self.assertEqual(st.state, ask_models.PROBING)
            self.assertTrue(st.can_ask)     # not decided yet — the palette waits
            self.assertEqual(st.message, "")
            release.set()
            # Wait for the worker before leaving: the status store is module state,
            # and a thread finishing after the next test's setUp would write into
            # that test's endpoint. (It did — one run failed on a leaked "ok".)
            self.assertTrue(settled.wait(3))

    def test_a_failure_supersedes_a_previously_cached_list(self):
        # Otherwise a server that died keeps looking healthy for the whole session.
        ask_models.cache_put("openai", "http://h:8080", ["m1"])
        st = self._settle(urllib.error.URLError("refused"))
        self.assertEqual(st.state, ask_models.UNREACHABLE)
        self.assertEqual(ask_models.cache_get("openai", "http://h:8080"), [])

    def test_a_later_success_clears_the_failure(self):
        self._settle(urllib.error.URLError("refused"))
        st = self._settle(lambda *a, **k: _response({"data": [{"id": "m1"}]}))
        self.assertEqual(st.state, ask_models.OK)
        self.assertTrue(st.can_ask)
        self.assertEqual(st.message, "")

    def test_a_settled_verdict_does_not_trigger_another_probe(self):
        # The settle callback refreshes the picker, which calls list_for again. If
        # that probed whenever the cache is empty, a failed endpoint would restart
        # itself every ~5 s for as long as the app runs. Only an endpoint nobody has
        # asked yet gets probed from here; a re-check is an explicit act (open the
        # palette again, or "Try again").
        settings = {"ask": {"engine": "manual", "backend": "openai",
                            "server": {"url": "http://h:8080"}}}
        self._settle(urllib.error.URLError("refused"))
        with patch.object(ask_models, "refresh_async") as again:
            ask_models.list_for(settings)
        again.assert_not_called()

    def test_only_verdicts_that_can_change_are_worth_rechecking(self):
        # A server without a list endpoint will not grow one while the app runs, so
        # re-probing it on every palette open is a wasted round trip on the hot
        # path — for the configuration the design calls healthy.
        stable = (ask_models.OK, ask_models.NO_LIST)
        changeable = (ask_models.EMPTY, ask_models.UNREACHABLE,
                      ask_models.UNAUTHORIZED, ask_models.LIST_ERROR,
                      ask_models.UNKNOWN)
        for state in stable:
            self.assertFalse(ask_models.EndpointStatus(state).transient, state)
        for state in changeable:
            self.assertTrue(ask_models.EndpointStatus(state).transient, state)

    def test_a_failed_chat_request_updates_the_verdict(self):
        # The chat call failing is the most authoritative evidence there is — more
        # than the model list, which is only a proxy. Without recording it, the
        # palette stays cheerful while every question fails.
        ask_models.cache_put("openai", "http://h:8080", ["m1"])
        message = ask_models.note_chat_failure(
            "openai", "http://h:8080", urllib.error.URLError("refused"))
        st = ask_models.status("openai", "http://h:8080")
        self.assertEqual(st.state, ask_models.UNREACHABLE)
        self.assertFalse(st.can_ask)
        self.assertIn("not reachable", message)
        self.assertEqual(ask_models.cache_get("openai", "http://h:8080"), [])

    def test_a_rejected_key_during_chat_updates_the_verdict(self):
        ask_models.note_chat_failure("openai", "http://h:8080",
                                     self._http_error(401))
        self.assertEqual(ask_models.status("openai", "http://h:8080").state,
                         ask_models.UNAUTHORIZED)

    def test_a_chat_specific_failure_does_not_rewrite_the_endpoint_verdict(self):
        # A chat 404 means "no such model", not "no list endpoint"; a 500 is the
        # request, not the server's reachability. Recording those through the
        # list vocabulary would put a wrong label on the endpoint.
        with patch("urllib.request.urlopen",
                   return_value=_response({"data": [{"id": "m1"}]})):
            ask_models.probe("openai", "http://h:8080")
        for code in (404, 500):
            ask_models.note_chat_failure("openai", "http://h:8080",
                                         self._http_error(code))
            self.assertEqual(ask_models.status("openai", "http://h:8080").state,
                             ask_models.OK, code)

    def test_status_is_per_endpoint(self):
        self._settle(urllib.error.URLError("refused"))
        self.assertEqual(ask_models.status("ollama", "http://h:11434").state,
                         ask_models.UNKNOWN)

    def test_probe_record_false_does_not_write_the_shared_verdict(self):
        # ZE1/ZB1: the embedding side probes the same endpoint; a failure there
        # must not mute Ask. record=False classifies without writing the shared
        # status/cache, so the Ask verdict for the endpoint stays untouched.
        with patch("urllib.request.urlopen",
                   side_effect=urllib.error.URLError("refused")):
            st = ask_models.probe("openai", "http://h:8080", record=False)
        self.assertFalse(st.can_ask)                                   # still classified
        self.assertTrue(ask_models.status("openai", "http://h:8080").can_ask)  # untouched

    def test_probe_record_true_writes_the_shared_verdict(self):
        with patch("urllib.request.urlopen",
                   side_effect=urllib.error.URLError("refused")):
            ask_models.probe("openai", "http://h:8080", record=True)
        self.assertFalse(ask_models.status("openai", "http://h:8080").can_ask)  # recorded


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

    def test_local_unavailable_is_a_blocking_verdict(self):
        # Shaped like a server verdict so the palette blocks and banners it, with
        # the availability() reason as the message.
        st = ask_models.local_unavailable("No local model file at /x/m.gguf.")
        self.assertFalse(st.can_ask)
        self.assertFalse(st.pending)
        self.assertEqual(st.message, "No local model file at /x/m.gguf.")

    def test_remember_local_stores_only_the_filename(self):
        # The footer picker's value is a full path; the stored ask_gguf_path must
        # be just the filename (a name in ask_models_dir), so the choice survives
        # the models folder moving.
        s = {}
        ask_models.remember(s, "local", "", "/some/models/Llama-3.2-3B.gguf")
        self.assertEqual(config.get_setting(s, "ask.gguf.path"), "Llama-3.2-3B.gguf")

    def test_openai_url_variants_are_the_same_endpoint(self):
        # host and host/v1 address the same server — remembering under both would
        # silently lose the choice when the URL is written the other way.
        s = {}
        ask_models.remember(s, "openai", "https://h/v1", "Qwen")
        self.assertEqual(ask_models.recall(s, "openai", "https://h"), "Qwen")

    def test_switching_backend_replaces_the_active_model(self):
        s = {"ask": {"backend": "ollama",
                     "server": {"url": "http://localhost:11434", "model": "llama3.2"}}}
        ask_models.remember(s, "ollama", "http://localhost:11434", "llama3.2")
        ask_models.activate(s, "openai", "https://llm.example.com")
        self.assertEqual(config.get_setting(s, "ask.server.model"), "")  # nothing selected
        ask_models.remember(s, "openai", "https://llm.example.com", "Qwen")
        ask_models.activate(s, "ollama", "http://localhost:11434")
        self.assertEqual(config.get_setting(s, "ask.server.model"),
                         "llama3.2")   # the old choice comes back


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
        s = {"ask": {"backend": "openai", "server": {"url": "https://llm.example.com"}}}
        ask_models.remember(s, "openai", "https://llm.example.com", "Qwen")
        ask_models.switch_backend(s, "openai", "ollama")
        self.assertEqual(config.get_setting(s, "ask.backend"), "ollama")
        self.assertEqual(config.get_setting(s, "ask.server.url"), "http://localhost:11434")
        self.assertEqual(config.get_setting(s, "ask.server.model"), "")  # nothing chosen yet
        ask_models.switch_backend(s, "ollama", "openai")
        self.assertEqual(config.get_setting(s, "ask.server.url"), "https://llm.example.com")
        self.assertEqual(config.get_setting(s, "ask.server.model"),
                         "Qwen")      # and its model comes back

    def test_local_in_between_does_not_clobber_a_remembered_url(self):
        s = {"ask": {"backend": "openai", "server": {"url": "https://llm.example.com"}}}
        ask_models.switch_backend(s, "openai", "local")
        ask_models.switch_backend(s, "local", "openai")
        self.assertEqual(config.get_setting(s, "ask.server.url"), "https://llm.example.com")


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
        settings = {"ask": {"engine": "manual", "backend": "openai",
                            "server": {"url": "https://llm.example.com"}}}
        self.store[ask_models.secret_name("openai", "https://llm.example.com")] = "sk-x"
        self.assertEqual(ask_models.api_key(settings), "sk-x")
        config.set_setting(settings, "ask.server.url", "http://localhost:8080")
        self.assertEqual(ask_models.api_key(settings), "")   # other server, no key

    def test_legacy_key_is_adopted_once_for_the_configured_endpoint(self):
        settings = {"ask": {"engine": "manual", "backend": "openai",
                            "server": {"url": "https://llm.example.com"}}}
        self.store["ask_api_key"] = "sk-old"
        self.assertTrue(ask_models.adopt_legacy_key(settings))
        self.assertEqual(ask_models.api_key(settings), "sk-old")
        self.assertNotIn("ask_api_key", self.store)   # not left lying around
        self.assertFalse(ask_models.adopt_legacy_key(settings))   # nothing to do

    def test_adoption_never_overwrites_an_endpoint_key(self):
        settings = {"ask": {"engine": "manual", "backend": "openai",
                            "server": {"url": "https://llm.example.com"}}}
        self.store[ask_models.secret_name("openai", "https://llm.example.com")] = "sk-new"
        self.store["ask_api_key"] = "sk-old"
        self.assertFalse(ask_models.adopt_legacy_key(settings))
        self.assertEqual(ask_models.api_key(settings), "sk-new")


class TestCurrent(unittest.TestCase):
    """Which value the picker should preselect — per backend."""

    def test_server_uses_ask_model(self):
        self.assertEqual(
            ask_models.current({"ask": {"engine": "manual", "backend": "openai",
                                        "server": {"model": "Qwen"}}}), "Qwen")

    def test_local_uses_resolved_gguf_path(self):
        with patch("markdown_vault.core.config.resolve_model_path",
                   return_value="/models/a.gguf"):
            self.assertEqual(ask_models.current({"ask": {"backend": "local"}}),
                             "/models/a.gguf")


if __name__ == "__main__":
    unittest.main()
