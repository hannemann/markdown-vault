"""Tests for markdown_vault.core.paths — XDG base-directory resolution.

The constants are computed once at import time, so the *logic* is tested through the
pure :func:`resolve` function with a patched environment (patching the constants
afterwards, as test_config does, cannot exercise the resolution itself).
"""
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from markdown_vault.core import paths


class TestResolve(unittest.TestCase):
    """`resolve(env_var, default)` → <env or ~/default>/de.hannemann.markdown-vault."""

    def test_env_var_wins(self):
        with patch.dict(os.environ, {"XDG_STATE_HOME": "/tmp/xdg-state"}):
            self.assertEqual(paths.resolve("XDG_STATE_HOME", ".local/state"),
                             Path("/tmp/xdg-state/de.hannemann.markdown-vault"))

    def test_falls_back_to_home_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(paths.resolve("XDG_STATE_HOME", ".local/state"),
                             Path.home() / ".local/state" / "de.hannemann.markdown-vault")

    def test_empty_env_var_falls_back(self):
        with patch.dict(os.environ, {"XDG_STATE_HOME": ""}):
            self.assertEqual(paths.resolve("XDG_STATE_HOME", ".local/state"),
                             Path.home() / ".local/state" / "de.hannemann.markdown-vault")

    def test_relative_env_var_is_ignored(self):
        # XDG spec: a relative path in one of these variables is invalid and ignored.
        with patch.dict(os.environ, {"XDG_STATE_HOME": "relative/dir"}):
            self.assertEqual(paths.resolve("XDG_STATE_HOME", ".local/state"),
                             Path.home() / ".local/state" / "de.hannemann.markdown-vault")


class TestDefaults(unittest.TestCase):
    """Without any XDG_* set, the resolver yields today's paths — a regression guard
    against the base silently moving (independent of which data kind lives where)."""

    def test_spec_defaults(self):
        with patch.dict(os.environ, {}, clear=True):
            home = Path.home()
            self.assertEqual(paths.resolve("XDG_STATE_HOME", ".local/state"),
                             home / ".local/state/de.hannemann.markdown-vault")
            self.assertEqual(paths.resolve("XDG_CONFIG_HOME", ".config"),
                             home / ".config/de.hannemann.markdown-vault")
            self.assertEqual(paths.resolve("XDG_CACHE_HOME", ".cache"),
                             home / ".cache/de.hannemann.markdown-vault")
            self.assertEqual(paths.resolve("XDG_DATA_HOME", ".local/share"),
                             home / ".local/share/de.hannemann.markdown-vault")


class TestConfigDirOverride(unittest.TestCase):
    """MDV_CONFIG_DIR (isolated runs / E2E) beats XDG_CONFIG_HOME."""

    def test_override_wins_over_xdg(self):
        with patch.dict(os.environ, {"MDV_CONFIG_DIR": "/tmp/mdv-cfg",
                                     "XDG_CONFIG_HOME": "/tmp/xdg-cfg"}):
            self.assertEqual(paths.config_dir(), Path("/tmp/mdv-cfg"))

    def test_xdg_used_without_override(self):
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": "/tmp/xdg-cfg"}, clear=True):
            self.assertEqual(paths.config_dir(), Path("/tmp/xdg-cfg/de.hannemann.markdown-vault"))

    def test_override_is_used_verbatim(self):
        # It names the directory itself, so no "de.hannemann.markdown-vault" is appended.
        with patch.dict(os.environ, {"MDV_CONFIG_DIR": "/tmp/throwaway"}):
            self.assertEqual(paths.config_dir(), Path("/tmp/throwaway"))


class TestConstants(unittest.TestCase):
    """The module-level constants are what the rest of the app imports."""

    def test_all_four_present_and_named(self):
        for name in ("STATE_DIR", "CONFIG_DIR", "CACHE_DIR", "DATA_DIR"):
            self.assertIsInstance(getattr(paths, name), Path, name)

    def test_each_ends_in_the_app_id(self):
        # One name for the app everywhere: the XDG dirs carry the application ID,
        # the same string as `main.py`'s application_id and the .desktop file.
        for name in ("STATE_DIR", "CACHE_DIR", "DATA_DIR"):
            self.assertEqual(getattr(paths, name).name, "de.hannemann.markdown-vault", name)


class TestRunIsolation(unittest.TestCase):
    """The suite must not be able to reach the developer's real directories.

    A leaked debounced write once replaced the whole settings block in the real
    ``vaults.yaml``; the trigger was fixed, but the *exposure* is what this asserts.
    `make test` / `test-one` / `coverage` pin all four base dirs at ./tmp/test-home,
    so a forgotten mock, thread or timer writes there instead of into the real config.
    """

    def test_not_running_against_the_real_dirs(self):
        home = Path.home()
        real = {
            "config": home / ".config" / "de.hannemann.markdown-vault",
            "state": home / ".local" / "state" / "de.hannemann.markdown-vault",
            "cache": home / ".cache" / "de.hannemann.markdown-vault",
            "data": home / ".local" / "share" / "de.hannemann.markdown-vault",
        }
        actual = {"config": paths.CONFIG_DIR, "state": paths.STATE_DIR,
                  "cache": paths.CACHE_DIR, "data": paths.DATA_DIR}
        for kind, path in actual.items():
            self.assertNotEqual(
                path, real[kind],
                f"the test run resolves {kind} to the real {real[kind]} — run the suite "
                f"via `make test` (it pins MDV_CONFIG_DIR and the XDG_*_HOME vars), or "
                f"set those yourself before running unittest directly")


class TestPlacement(unittest.TestCase):
    """Each data kind sits in its XDG directory — the point of the whole change, so
    pinned where the owning module resolves it (not just in the docstring table)."""

    def test_config_re_exports_all_four(self):
        from markdown_vault.core import config
        self.assertEqual(config.CONFIG_DIR, paths.CONFIG_DIR)
        self.assertEqual(config.STATE_DIR, paths.STATE_DIR)
        self.assertEqual(config.CACHE_DIR, paths.CACHE_DIR)
        self.assertEqual(config.DATA_DIR, paths.DATA_DIR)

    def test_vaults_yaml_in_config(self):
        from markdown_vault.core import config
        self.assertEqual(config.CONFIG_FILE.parent, paths.CONFIG_DIR)

    def test_session_in_state(self):
        from markdown_vault.core import session
        self.assertEqual(session.SESSION_FILE.parent, paths.STATE_DIR)
        self.assertEqual(session.SESSION_FILE.name, "session.json")

    def test_logs_in_state(self):
        from markdown_vault.core import config
        self.assertEqual(config.LOG_FILE.parent, paths.STATE_DIR)

    def test_logging_setup_uses_the_same_state_dir(self):
        # The duplicate definition here was the original bug: logging honoured the
        # host path while config honoured XDG, so a sandbox split the two apart.
        from markdown_vault.core import logging_setup
        self.assertEqual(logging_setup._STATE_DIR, str(paths.STATE_DIR))

    def test_models_in_data(self):
        from markdown_vault.core import config
        self.assertEqual(config.models_dir(), paths.DATA_DIR / "models")


if __name__ == "__main__":
    unittest.main()
