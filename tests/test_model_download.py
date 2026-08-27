"""Tests for markdown_vault.search.model_download — the UI-agnostic model downloader.

The download owns the network fetch and the FS writes; the UI keeps only widgets. So the
whole thing is unit-testable here without GTK: the happy path runs against a faked urllib
opener (no network, no HTTPS cert), and the error mapper is pure.
"""

import unittest
import urllib.error
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from markdown_vault.search import model_download as md


class _FakeResp:
    """A minimal stand-in for a urllib response: streams `data`, is a context manager."""

    def __init__(self, data: bytes, total=None):
        self._data = data
        self._pos = 0
        self.headers = {"Content-Length": str(len(data) if total is None else total)}

    def read(self, n):
        chunk = self._data[self._pos:self._pos + n]
        self._pos += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


def _fake_opener(resp):
    opener = mock.Mock()
    opener.open.return_value = resp
    return mock.patch(
        "markdown_vault.search.model_download.urllib.request.build_opener",
        return_value=opener)


class TestDownloadTo(unittest.TestCase):
    def test_refuses_a_non_https_url_before_any_network(self):
        with TemporaryDirectory() as d:
            with self.assertRaises(md.NonHttpsUrl):
                md.download_to("http://example/model.onnx", Path(d) / "m.onnx")

    def test_streams_to_the_target_and_returns_the_size(self):
        with TemporaryDirectory() as d:
            target = Path(d) / "sub" / "model.bin"
            with _fake_opener(_FakeResp(b"GGUF" + b"x" * 100)):
                size = md.download_to("https://h/model.bin", target)
            self.assertTrue(target.exists())
            self.assertEqual(target.read_bytes(), b"GGUF" + b"x" * 100)
            self.assertEqual(size, 104)
            self.assertFalse(target.with_name("model.bin.part").exists())  # tmp cleaned up

    def test_reports_progress(self):
        with TemporaryDirectory() as d:
            seen = []
            with _fake_opener(_FakeResp(b"y" * (3 * 1024 * 1024), total=3 * 1024 * 1024)):
                md.download_to("https://h/big.bin", Path(d) / "big.bin",
                               progress=lambda done, total: seen.append((done, total)))
            self.assertTrue(seen)                       # progress was called at least once
            self.assertEqual(seen[-1][1], 3 * 1024 * 1024)   # total forwarded

    def test_a_short_download_is_rejected_not_installed(self):
        # http.client returns b"" (no exception) when a Content-Length response drops, so
        # a truncated model would otherwise be replace()d into place and called complete.
        with TemporaryDirectory() as d:
            target = Path(d) / "model.gguf"
            with _fake_opener(_FakeResp(b"GGUFshort", total=1000)):   # claims 1000, gives 9
                with self.assertRaises(md.IncompleteDownload):
                    md.download_to("https://h/model.gguf", target)
            self.assertFalse(target.exists())                        # never promoted
            self.assertFalse(target.with_name("model.gguf.part").exists())

    def test_a_mid_stream_failure_leaves_no_part_file(self):
        class _Boom(_FakeResp):
            def read(self, n):
                if self._pos == 0:
                    return super().read(n)          # first read yields the data...
                raise ConnectionResetError("dropped")   # ...then the connection dies

        with TemporaryDirectory() as d:
            target = Path(d) / "m.bin"
            with _fake_opener(_Boom(b"y" * 10, total=1000)):
                with self.assertRaises(ConnectionResetError):
                    md.download_to("https://h/m.bin", target)
            self.assertFalse(target.with_name("m.bin.part").exists())   # tmp cleaned up

    def test_rejected_content_raises_and_cleans_up(self):
        with TemporaryDirectory() as d:
            target = Path(d) / "model.gguf"
            with _fake_opener(_FakeResp(b"<html>not a model</html>")):
                with self.assertRaises(md.ContentRejected):
                    md.download_to("https://h/model.gguf", target,
                                   validate=lambda p: "not a GGUF")
            self.assertFalse(target.exists())                       # never promoted
            self.assertFalse(target.with_name("model.gguf.part").exists())  # tmp removed


class TestDescribeError(unittest.TestCase):
    def test_maps_each_type(self):
        self.assertIn("HTTPS", md.describe_error(md.NonHttpsUrl("u")))
        self.assertEqual(md.describe_error(md.ContentRejected("our own message")),
                         "our own message")
        self.assertIn("404", md.describe_error(
            urllib.error.HTTPError("u", 404, "nf", {}, None)))
        self.assertIn("incomplete", md.describe_error(md.IncompleteDownload("x")).lower())
        self.assertTrue(md.describe_error(urllib.error.URLError("dns")))
        self.assertTrue(md.describe_error(RuntimeError("boom")))   # generic default

    def test_insecure_redirect_is_checked_before_the_generic_httperror(self):
        # InsecureRedirect subclasses HTTPError; most-specific-first, so it must NOT fall
        # through to the "server returned HTTP {code}" branch.
        exc = md.InsecureRedirect("u", 302, "refusing a non-HTTPS redirect", {}, None)
        msg = md.describe_error(exc)
        self.assertNotIn("302", msg)


if __name__ == "__main__":
    unittest.main()
