"""E2E harness: spawn the app headless on an isolated session bus and drive it
over the D-Bus debug interface (debug_control, gated by MDV_DEBUG_CONTROL).

Isolation matters twice: an isolated session bus (dbus-run-session) keeps the
GApplication single-instance from just activating the developer's running app
instead of a fresh one, and MDV_CONFIG_DIR / XDG_STATE_HOME / XDG_CACHE_HOME /
XDG_DATA_HOME point config, state, cache, data and the vault at throwaway dirs so a
test never touches real notes, logs, index or models.

Run via ``make test-e2e`` (which sets up the bus and, if available, xvfb).
Directly-run without a display or session bus, the tests skip.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib

DEST = "de.hannemann.markdown-vault"
OBJ = "/de/hannemann/markdown_vault/debug"
IFACE = "de.hannemann.markdown_vault.Debug"

# A tiny fixture vault. "planet" appears in erde/mars/old but not notes; old is
# deprecated — enough for search + open + filter smoke tests.
SAMPLE = {
    "erde.md": "---\ntitle: Die Erde\ntags: [planet]\n---\n# Erde\nDer dritte Planet im System.\n",
    "mars.md": "---\ntags: [planet]\n---\n# Mars\nDer rote Planet.\n",
    "notes.md": "# Notes\nEin Merkzettel ohne Bezug.\n",
    "old.md": "---\nstatus: deprecated\ntags: [planet]\n---\n# Old\nEin veralteter Planet.\n",
}


def _has_display():
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


@unittest.skipUnless(_has_display(), "no display (run under xvfb-run or a session)")
@unittest.skipUnless(os.environ.get("DBUS_SESSION_BUS_ADDRESS"),
                     "no isolated session bus (run under dbus-run-session)")
class AppSession(unittest.TestCase):
    """Base class: one app process per test class, driven over D-Bus."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp(prefix="mdv-e2e-")
        cls._cfg = os.path.join(cls._tmp, "config")
        cls._vault = os.path.join(cls._tmp, "vault")
        for d in (cls._cfg, os.path.join(cls._tmp, "state"), cls._vault):
            os.makedirs(d)
        for name, text in SAMPLE.items():
            with open(os.path.join(cls._vault, name), "w", encoding="utf-8") as fh:
                fh.write(text)
        with open(os.path.join(cls._cfg, "settings.yaml"), "w", encoding="utf-8") as fh:
            fh.write(
                "vaults:\n- name: Test\n  path: %s\n"
                "settings:\n"
                "  semantic:\n    enabled: false\n"    # keep it light: no ONNX build
                "  view:\n    default_mode: edit\n"    # avoid the WebKit preview
                "  autosave:\n    interval: 3600\n" % cls._vault)

        env = dict(os.environ)
        # All four base dirs, or the run reaches into the developer's real ones:
        # config (settings.yaml), state (logs, session), cache (semantic index),
        # data (downloaded models). Pinning only some is how the log leak happened.
        env["MDV_CONFIG_DIR"] = cls._cfg
        env["XDG_STATE_HOME"] = os.path.join(cls._tmp, "state")
        env["XDG_CACHE_HOME"] = os.path.join(cls._tmp, "cache")
        env["XDG_DATA_HOME"] = os.path.join(cls._tmp, "data")
        env["MDV_DEBUG_CONTROL"] = "1"
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env["PYTHONPATH"] = (os.path.join(repo, "src") + os.pathsep
                             + env.get("PYTHONPATH", ""))
        cls._proc = subprocess.Popen(
            [sys.executable, "-m", "markdown_vault.main"], env=env)
        cls._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        cls._wait_ready(30)

    @classmethod
    def tearDownClass(cls):
        proc = getattr(cls, "_proc", None)
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        shutil.rmtree(getattr(cls, "_tmp", "") or "", ignore_errors=True)

    # ── D-Bus plumbing ────────────────────────────────────────────────

    @classmethod
    def _call(cls, method, params, reply, timeout=10000):
        v = cls._bus.call_sync(DEST, OBJ, IFACE, method, params,
                               GLib.VariantType(reply), Gio.DBusCallFlags.NONE,
                               timeout, None)
        return v.unpack()

    @classmethod
    def _wait_ready(cls, timeout):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if cls._proc.poll() is not None:
                raise RuntimeError(
                    "app exited early (code %s)" % cls._proc.returncode)
            try:
                cls._call("ActiveFile", None, "(s)", timeout=1000)
                return
            except GLib.Error:
                time.sleep(0.3)
        raise RuntimeError("debug interface did not come up in time")

    # ── Convenience wrappers ──────────────────────────────────────────

    def path(self, name):
        return os.path.join(self._vault, name)

    def open_file(self, name):
        ok = self._call("OpenFile", GLib.Variant("(s)", (self.path(name),)), "(b)")[0]
        self.wait_idle()
        return ok

    def close_tab(self, name):
        return self._call("CloseTab", GLib.Variant("(s)", (self.path(name),)), "(b)")[0]

    def search(self, query):
        self._call("Search", GLib.Variant("(s)", (query,)), "(b)")
        self.wait_idle()

    def wait_idle(self, timeout_ms=5000):
        return self._call("WaitIdle", GLib.Variant("(i)", (timeout_ms,)), "(b)")[0]

    def search_results(self):
        return self._call("SearchResults", None, "(as)")[0]

    def active_file(self):
        return self._call("ActiveFile", None, "(s)")[0]

    def list_tabs(self):
        return self._call("ListTabs", None, "(as)")[0]
