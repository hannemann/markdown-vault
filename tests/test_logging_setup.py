"""Tests for markdown_vault.core.logging_setup.

Covers the standard-logging routing (normal messages -> stdout,
errors -> stderr), the two rotated log files, and the fd redirect
that captures native/child-process output.
"""

import faulthandler
import logging
import logging.handlers
import os
import shutil
import tempfile
import unittest

from markdown_vault.core import logging_setup

_ROOT = logging.getLogger()


def _flush(handlers):
    for handler in handlers:
        try:
            handler.flush()
        except Exception:  # noqa: BLE001 — teardown flush is best-effort
            pass


class MaxMinFilterTest(unittest.TestCase):
    """Level filters used to route stdout vs. stderr."""

    def _record(self, level):
        return logging.LogRecord("test", level, "", 0, "msg", (), None)

    def test_max_level_filter_allows_lower_or_equal(self):
        filt = logging_setup._MaxLevelFilter(logging.INFO)
        self.assertTrue(filt.filter(self._record(logging.DEBUG)))
        self.assertTrue(filt.filter(self._record(logging.INFO)))
        self.assertFalse(filt.filter(self._record(logging.WARNING)))
        self.assertFalse(filt.filter(self._record(logging.ERROR)))

    def test_min_level_filter_allows_higher_or_equal(self):
        filt = logging_setup._MinLevelFilter(logging.WARNING)
        self.assertFalse(filt.filter(self._record(logging.DEBUG)))
        self.assertFalse(filt.filter(self._record(logging.INFO)))
        self.assertTrue(filt.filter(self._record(logging.WARNING)))
        self.assertTrue(filt.filter(self._record(logging.ERROR)))


class LoggingSetupTestCase(unittest.TestCase):
    """Isolated state-dir + root logger per test."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig_state_dir = logging_setup._STATE_DIR
        self._orig_root_level = _ROOT.level
        self._orig_root_handlers = list(_ROOT.handlers)
        logging_setup._STATE_DIR = self._tmp
        logging_setup._file_setup_done = False
        logging_setup._console_setup_done = False
        logging_setup._installed_handlers.clear()
        logging_setup._glib_handler_id.clear()

    def tearDown(self):
        for handler in list(logging_setup._installed_handlers):
            _ROOT.removeHandler(handler)
            try:
                handler.close()
            except Exception:  # noqa: BLE001 — teardown close is best-effort
                pass
        logging_setup._installed_handlers.clear()
        for handler in list(_ROOT.handlers):
            if handler not in self._orig_root_handlers:
                _ROOT.removeHandler(handler)
        _ROOT.setLevel(self._orig_root_level)
        logging_setup._STATE_DIR = self._orig_state_dir
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _init(self, settings):
        logging_setup.init(
            settings,
            redirect_stdout=False,
            redirect_stderr=False,
            setup_glib=False,
        )

    def _stdout_log(self):
        return os.path.join(self._tmp, "markdown-vault.log")

    def _stderr_log(self):
        return os.path.join(self._tmp, "markdown-vault.stderr.log")


class InitTest(LoggingSetupTestCase):
    def test_init_creates_two_log_files(self):
        self._init({"log": {"level": "debug"}})
        self.assertTrue(os.path.exists(self._stdout_log()))
        self.assertTrue(os.path.exists(self._stderr_log()))

    def test_init_reads_loglevel_from_dict(self):
        self._init({"log": {"level": "debug"}})
        self.assertEqual(logging.getLogger("markdown-vault").level, logging.DEBUG)
        self.assertEqual(_ROOT.level, logging.DEBUG)

    def test_init_defaults_to_warning_for_missing_level(self):
        self._init({})
        self.assertEqual(logging.getLogger("markdown-vault").level, logging.WARNING)

    def test_routes_normal_messages_to_stdout_log(self):
        self._init({"log": {"level": "debug"}})
        logger = logging.getLogger("markdown-vault")
        logger.info("normal message 42")
        logger.warning("warning message 43")
        _flush(logging_setup._installed_handlers)
        with open(self._stdout_log(), encoding="utf-8") as fh:
            stdout_content = fh.read()
        with open(self._stderr_log(), encoding="utf-8") as fh:
            stderr_content = fh.read()
        self.assertIn("normal message 42", stdout_content)
        self.assertNotIn("warning message 43", stdout_content)
        self.assertNotIn("normal message 42", stderr_content)
        self.assertIn("warning message 43", stderr_content)

    def test_routes_debug_to_stdout_log(self):
        self._init({"log": {"level": "debug"}})
        logging.getLogger("markdown-vault").debug("debug message 44")
        _flush(logging_setup._installed_handlers)
        with open(self._stdout_log(), encoding="utf-8") as fh:
            stdout_content = fh.read()
        self.assertIn("debug message 44", stdout_content)

    def test_init_with_none_settings_uses_defaults(self):
        self._init(None)
        self.assertEqual(
            logging.getLogger("markdown-vault").level, logging.WARNING
        )
        self.assertEqual(_ROOT.level, logging.WARNING)
        self.assertTrue(os.path.exists(self._stdout_log()))
        self.assertTrue(os.path.exists(self._stderr_log()))

    def test_init_twice_reattaches_level(self):
        self._init({"log": {"level": "debug"}})
        self._init({"log": {"level": "error"}})
        self.assertEqual(
            logging.getLogger("markdown-vault").level, logging.ERROR
        )
        self.assertEqual(_ROOT.level, logging.ERROR)

    def test_init_enables_faulthandler(self):
        faulthandler.disable()
        self._init({"log": {"level": "debug"}})
        self.assertTrue(faulthandler.is_enabled())


class ThirdPartyLevelTest(unittest.TestCase):
    def setUp(self):
        self._original = {
            prefix: logging.getLogger(prefix).level
            for prefix in ("markdown", "pymdownx", "urllib3", "pygments", "xml")
        }

    def tearDown(self):
        for prefix, level in self._original.items():
            logging.getLogger(prefix).setLevel(level)

    def test_sets_third_party_loggers(self):
        logging_setup.set_third_party_loglevel("error")
        self.assertEqual(logging.getLogger("markdown").level, logging.ERROR)
        self.assertEqual(logging.getLogger("pymdownx").level, logging.ERROR)
        self.assertEqual(logging.getLogger("MARKDOWN").level, logging.ERROR)

    def test_unknown_level_falls_back_to_warning(self):
        logging_setup.set_third_party_loglevel("bogus")
        self.assertEqual(logging.getLogger("markdown").level, logging.WARNING)


class GlibHandlerTest(unittest.TestCase):
    """Threshold + one-time handler install in _setup_glib_logging."""

    def test_handlers_installed_once(self):
        """Handlers are registered exactly once; a level change must not
        re-register (they catch every level; only the threshold moves)."""
        calls = []

        class FakeFlags:
            LEVEL_ERROR = 4
            LEVEL_CRITICAL = 8
            LEVEL_WARNING = 16
            LEVEL_MESSAGE = 32
            LEVEL_INFO = 64
            LEVEL_DEBUG = 128
            LEVEL_MASK = 0xFC
            FLAG_FATAL = 2
            FLAG_RECURSION = 1

        class FakeGLib:
            LogLevelFlags = FakeFlags

            @staticmethod
            def log_set_handler(domain, level, func, data):
                calls.append(("set", domain))
                return len(calls)

            @staticmethod
            def log_set_default_handler(func, data):
                calls.append(("set_default", None))
                return len(calls)

        saved_glib = logging_setup.GLib
        saved_rank = logging_setup._glib_min_rank
        saved_ids = list(logging_setup._glib_handler_id)
        logging_setup.GLib = FakeGLib
        logging_setup._glib_handler_id = []
        try:
            logging_setup._setup_glib_logging("warning")
            after_first = len(calls)
            logging_setup._setup_glib_logging("critical")  # must not re-register
            self.assertEqual(len(calls), after_first)
            self.assertEqual(sum(1 for c in calls if c[0] == "set"), 6)
            self.assertEqual(sum(1 for c in calls if c[0] == "set_default"), 1)
        finally:
            logging_setup.GLib = saved_glib
            logging_setup._glib_min_rank = saved_rank
            logging_setup._glib_handler_id = saved_ids

    def test_threshold_updates_with_level(self):
        saved_rank = logging_setup._glib_min_rank
        saved_ids = list(logging_setup._glib_handler_id)
        logging_setup._glib_handler_id = [("x", 1)]  # pretend already installed
        try:
            logging_setup._setup_glib_logging("critical")
            self.assertEqual(
                logging_setup._glib_min_rank,
                logging_setup._GLIB_THRESHOLD["critical"],
            )
            logging_setup._setup_glib_logging("warning")
            self.assertEqual(
                logging_setup._glib_min_rank,
                logging_setup._GLIB_THRESHOLD["warning"],
            )
            logging_setup._setup_glib_logging("bogus")  # unknown -> default
            self.assertEqual(
                logging_setup._glib_min_rank, logging_setup._DEFAULT_GLIB_RANK,
            )
        finally:
            logging_setup._glib_min_rank = saved_rank
            logging_setup._glib_handler_id = saved_ids

    def test_threshold_filters_handler(self):
        """Below-threshold messages are dropped; at/above are logged."""
        from gi.repository import GLib as _G

        saved_rank = logging_setup._glib_min_rank
        logger = logging.getLogger("markdown-vault")
        records: list[int] = []

        class _Capture(logging.Handler):
            def emit(self, record):
                records.append(record.levelno)

        cap = _Capture()
        logger.addHandler(cap)
        try:
            logging_setup._glib_min_rank = logging_setup._GLIB_THRESHOLD["critical"]
            logging_setup._glib_log_handler("Gdk", _G.LogLevelFlags.LEVEL_WARNING, "noise")
            self.assertEqual(records, [])  # WARNING < critical -> dropped

            logging_setup._glib_min_rank = logging_setup._GLIB_THRESHOLD["warning"]
            records.clear()
            logging_setup._glib_log_handler("Gdk", _G.LogLevelFlags.LEVEL_WARNING, "kept")
            self.assertEqual(records, [logging.WARNING])  # at threshold -> logged
        finally:
            logger.removeHandler(cap)
            logging_setup._glib_min_rank = saved_rank


class RotationTest(unittest.TestCase):
    def test_rotating_handler_creates_backups(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        path = os.path.join(tmp, "stderr.log")
        saved = os.dup(2)
        handler = None
        try:
            handler = logging_setup._StderrRotatingFileHandler(
                path, maxBytes=200, backupCount=3
            )
            for i in range(50):
                handler.emit(
                    logging.LogRecord(
                        "test", logging.WARNING, "", 0, "x" * 50, (), None
                    )
                )
            handler.flush()
            self.assertTrue(os.path.exists(path))
            self.assertTrue(os.path.exists(path + ".1"))
        finally:
            if handler is not None:
                handler.close()
            os.dup2(saved, 2)
            os.close(saved)


class FDRedirectTest(unittest.TestCase):
    def test_stderr_handler_redirects_fd2_to_log_file(self):
        if not os.path.islink("/proc/self/fd/2"):
            self.skipTest("requires /proc")
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        path = os.path.join(tmp, "stderr.log")
        saved = os.dup(2)
        handler = None
        try:
            handler = logging_setup._StderrRotatingFileHandler(
                path, maxBytes=1_000_000, backupCount=0
            )
            target = os.readlink("/proc/self/fd/2")
            self.assertEqual(os.path.realpath(target), os.path.realpath(path))
        finally:
            if handler is not None:
                handler.close()
            os.dup2(saved, 2)
            os.close(saved)


if __name__ == "__main__":
    unittest.main()
