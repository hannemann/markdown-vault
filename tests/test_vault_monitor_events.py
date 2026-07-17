"""Tests for VaultMonitor — Event-Filterung und -Weiterleitung.

Tests:
- Nur .md Events werden weitergeleitet
- .txt, .hidden.md, directories werden ignoriert
- Alle Event-Typen werden korrekt emittiert
- N.2: RENAMED events (same-dir rename) werden nicht gedroppt
- N.3: MOVED_IN ohne other_file ruft Callback mit 2 Args
- N.4: Callback-Exceptions werden geloggt statt verschluckt
"""

import gi
import importlib
import sys
import unittest
from unittest.mock import MagicMock, patch


# Echtes Gio.FileMonitorEvent Werte
_CRE = 3
_DEL = 2
_HINT = 1
_RENAMED = 8
_MOI = 9
_MOO = 10


def _make_mock_gio():
    """Erstellt ein gemoddetes Gio mit File/Monitor/MonitorFlags/Event."""
    mock_gio = MagicMock()

    def make_file(path):
        mock_file = MagicMock()
        mock_file.monitor_directory.return_value = MagicMock()
        return mock_file

    mock_gio.File.new_for_path.side_effect = make_file
    # FileMonitorFlags muss ein echter Typ für isinstance-Checks sein
    FileMonitorFlags = type('FileMonitorFlags', (), {'WATCH_MOVES': 8})
    mock_gio.FileMonitorFlags = FileMonitorFlags
    # FileMonitorEvent Werte — müssen mit echten Werten übereinstimmen
    mock_gio.FileMonitorEvent = MagicMock()
    mock_gio.FileMonitorEvent.CREATED = _CRE
    mock_gio.FileMonitorEvent.DELETED = _DEL
    mock_gio.FileMonitorEvent.CHANGES_DONE_HINT = _HINT
    mock_gio.FileMonitorEvent.RENAMED = _RENAMED
    mock_gio.FileMonitorEvent.MOVED_IN = _MOI
    mock_gio.FileMonitorEvent.MOVED_OUT = _MOO
    return mock_gio


def _make_mock_glib():
    """Erstellt ein gemoddetes GLib."""
    mock_glib = MagicMock()
    def fake_timeout_add(interval, func):
        func()
        return 1
    mock_glib.timeout_add.side_effect = fake_timeout_add
    return mock_glib


def _make_mock_file(path):
    mock_file = MagicMock()
    mock_file.get_path.return_value = path
    return mock_file


def _load_monitor(mock_gio, mock_glib):
    """Lädt vault_monitor mit gemoddetem Gio/GLib neu."""
    # Gio aus vault_monitor entfernen
    for mod in list(sys.modules.keys()):
        if mod == 'src.vault_monitor' or mod.startswith('src.vault_monitor.'):
            del sys.modules[mod]

    # gi.repository.Gio direkt patchen
    import gi.repository
    gi.repository.Gio = mock_gio
    gi.repository.GLib = mock_glib

    import src.vault_monitor
    return src.vault_monitor


class TestVaultMonitorFiltering(unittest.TestCase):
    """Phase 2: Event-Filterung — nur .md Dateien."""

    def _create_monitor(self, vault_path="/tmp/testvault"):
        mock_gio = _make_mock_gio()
        mock_glib = _make_mock_glib()

        with patch("src.vault_monitor.os.path.isdir", return_value=True):
            mod = _load_monitor(mock_gio, mock_glib)
            VaultMonitor = mod.VaultMonitor
            monitor = VaultMonitor()
            monitor.set_vaults([vault_path])
            return monitor

    def test_md_file_created_event_is_emitted(self):
        monitor = self._create_monitor()
        received = []
        monitor.connect("external-file-created", lambda *args: received.append(args))

        mock_file = _make_mock_file("/tmp/testvault/new.md")
        mock_monitor = list(monitor._monitors.values())[0]
        monitor._on_monitor_event(mock_monitor, mock_file, None, _CRE)

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0][0], "/tmp/testvault")
        self.assertEqual(received[0][1], "/tmp/testvault/new.md")

    def test_md_file_deleted_event_is_emitted(self):
        monitor = self._create_monitor()
        received = []
        monitor.connect("external-file-deleted", lambda *args: received.append(args))

        mock_file = _make_mock_file("/tmp/testvault/old.md")
        mock_monitor = list(monitor._monitors.values())[0]
        monitor._on_monitor_event(mock_monitor, mock_file, None, _DEL)

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0][0], "/tmp/testvault")
        self.assertEqual(received[0][1], "/tmp/testvault/old.md")

    def test_md_file_changed_event_is_ignored(self):
        monitor = self._create_monitor()
        received = []
        monitor.connect("external-content-changed", lambda *args: received.append(args))

        mock_file = _make_mock_file("/tmp/testvault/changed.md")
        mock_monitor = list(monitor._monitors.values())[0]
        monitor._on_monitor_event(mock_monitor, mock_file, None, 0)  # CHANGED

        self.assertEqual(len(received), 0)

    def test_txt_file_created_is_ignored(self):
        monitor = self._create_monitor()
        received = []
        monitor.connect("external-file-created", lambda *args: received.append(args))

        mock_file = _make_mock_file("/tmp/testvault/file.txt")
        mock_monitor = list(monitor._monitors.values())[0]
        monitor._on_monitor_event(mock_monitor, mock_file, None, _CRE)

        self.assertEqual(len(received), 0)

    def test_hidden_md_file_is_ignored(self):
        monitor = self._create_monitor()
        received = []
        monitor.connect("external-file-created", lambda *args: received.append(args))

        mock_file = _make_mock_file("/tmp/testvault/.hidden.md")
        mock_monitor = list(monitor._monitors.values())[0]
        monitor._on_monitor_event(mock_monitor, mock_file, None, _CRE)

        self.assertEqual(len(received), 0)

    def test_hidden_dir_md_file_is_ignored(self):
        monitor = self._create_monitor()
        received = []
        monitor.connect("external-file-created", lambda *args: received.append(args))

        mock_file = _make_mock_file("/tmp/testvault/.git/file.md")
        mock_monitor = list(monitor._monitors.values())[0]
        monitor._on_monitor_event(mock_monitor, mock_file, None, _CRE)

        self.assertEqual(len(received), 0)

    def test_nested_md_file_is_emitted(self):
        monitor = self._create_monitor()
        received = []
        monitor.connect("external-file-created", lambda *args: received.append(args))

        mock_file = _make_mock_file("/tmp/testvault/subdir/file.md")
        mock_monitor = list(monitor._monitors.values())[0]
        monitor._on_monitor_event(mock_monitor, mock_file, None, _CRE)

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0][1], "/tmp/testvault/subdir/file.md")


class TestVaultMonitorEventTypeMapping(unittest.TestCase):
    """Phase 2: Event-Typ Mapping."""

    def _create_monitor(self):
        mock_gio = _make_mock_gio()
        mock_glib = _make_mock_glib()

        with patch("src.vault_monitor.os.path.isdir", return_value=True):
            mod = _load_monitor(mock_gio, mock_glib)
            VaultMonitor = mod.VaultMonitor
            monitor = VaultMonitor()
            monitor.set_vaults(["/tmp/testvault"])
            return monitor

    def test_created_emits_external_file_created(self):
        monitor = self._create_monitor()
        received = []
        monitor.connect("external-file-created", lambda *args: received.append(args))

        mock_file = _make_mock_file("/tmp/testvault/test.md")
        mock_monitor = list(monitor._monitors.values())[0]
        monitor._on_monitor_event(mock_monitor, mock_file, None, _CRE)

        self.assertEqual(len(received), 1)

    def test_deleted_emits_external_file_deleted(self):
        monitor = self._create_monitor()
        received = []
        monitor.connect("external-file-deleted", lambda *args: received.append(args))

        mock_file = _make_mock_file("/tmp/testvault/test.md")
        mock_monitor = list(monitor._monitors.values())[0]
        monitor._on_monitor_event(mock_monitor, mock_file, None, _DEL)

        self.assertEqual(len(received), 1)

    def test_moved_emits_external_file_moved(self):
        monitor = self._create_monitor()
        received = []
        monitor.connect("external-file-moved", lambda *args: received.append(args))

        mock_file = _make_mock_file("/tmp/testvault/new.md")
        mock_other = _make_mock_file("/tmp/testvault/old.md")
        mock_monitor = list(monitor._monitors.values())[0]
        monitor._on_monitor_event(mock_monitor, mock_file, mock_other, _MOI)

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0][0], "/tmp/testvault")
        self.assertEqual(received[0][1], "/tmp/testvault/new.md")
        self.assertEqual(received[0][2], "/tmp/testvault/old.md")

    def test_changes_done_hint_emits_external_content_changed(self):
        monitor = self._create_monitor()
        received = []
        monitor.connect("external-content-changed", lambda *args: received.append(args))

        mock_file = _make_mock_file("/tmp/testvault/test.md")
        mock_monitor = list(monitor._monitors.values())[0]
        monitor._on_monitor_event(mock_monitor, mock_file, None, _HINT)

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0][0], "/tmp/testvault")
        self.assertEqual(received[0][1], "/tmp/testvault/test.md")


_RENAMED = 8


class TestN2_RenamedEvent(unittest.TestCase):
    """N.2: RENAMED events (same-dir rename) must not be dropped."""

    def _create_monitor(self):
        mock_gio = _make_mock_gio()
        mock_glib = _make_mock_glib()
        with patch("src.vault_monitor.os.path.isdir", return_value=True):
            mod = _load_monitor(mock_gio, mock_glib)
            monitor = mod.VaultMonitor()
            monitor.set_vaults(["/tmp/testvault"])
            return monitor

    def test_renamed_event_maps_to_moved(self):
        """RENAMED must be recognized in _EVENT_MAP."""
        monitor = self._create_monitor()
        self.assertIn("moved", monitor._EVENT_MAP.values())

    def test_renamed_event_emits_file_moved_signal(self):
        """RENAMED event must trigger the external-file-moved callback.

        Gio gives file=old, other=new for RENAMED — our callback expects
        file=new, other=old (matching MOVED_IN convention).
        """
        monitor = self._create_monitor()
        received = []
        monitor.connect("external-file-moved", lambda *args: received.append(args))

        mock_old = _make_mock_file("/tmp/testvault/old.md")
        mock_new = _make_mock_file("/tmp/testvault/new.md")
        mock_monitor = list(monitor._monitors.values())[0]
        # Gio: file=old, other=new
        monitor._on_monitor_event(mock_monitor, mock_old, mock_new, _RENAMED)

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0][1], "/tmp/testvault/new.md")
        self.assertEqual(received[0][2], "/tmp/testvault/old.md")

    def test_renamed_non_md_is_ignored(self):
        """RENAMED for .txt files must be filtered out."""
        monitor = self._create_monitor()
        received = []
        monitor.connect("external-file-moved", lambda *args: received.append(args))

        mock_old = _make_mock_file("/tmp/testvault/old.txt")
        mock_new = _make_mock_file("/tmp/testvault/new.txt")
        mock_monitor = list(monitor._monitors.values())[0]
        monitor._on_monitor_event(mock_monitor, mock_new, mock_old, _RENAMED)

        self.assertEqual(len(received), 0)


class TestN3_MovedInWithoutOtherFile(unittest.TestCase):
    """N.3: MOVED_IN with other_file=None must not raise TypeError."""

    def test_moved_in_with_none_other_calls_callback_with_two_args(self):
        """When other_file is None, callback must receive (vault, file_path)."""
        mock_gio = _make_mock_gio()
        mock_glib = _make_mock_glib()
        with patch("src.vault_monitor.os.path.isdir", return_value=True):
            mod = _load_monitor(mock_gio, mock_glib)
            monitor = mod.VaultMonitor()
            monitor.set_vaults(["/tmp/testvault"])

        received = []
        monitor.connect("external-file-moved", lambda *args: received.append(args))

        mock_file = _make_mock_file("/tmp/testvault/incoming.md")
        mock_monitor = list(monitor._monitors.values())[0]
        monitor._on_monitor_event(mock_monitor, mock_file, None, _MOI)

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0][0], "/tmp/testvault")
        self.assertEqual(received[0][1], "/tmp/testvault/incoming.md")


class TestN4_CallbackExceptionLogging(unittest.TestCase):
    """N.4: Callback exceptions must be logged, not silently swallowed."""

    def test_callback_exception_is_logged(self):
        """When a callback raises, the exception must be logged."""
        mock_gio = _make_mock_gio()
        mock_glib = _make_mock_glib()
        with patch("src.vault_monitor.os.path.isdir", return_value=True):
            mod = _load_monitor(mock_gio, mock_glib)
            monitor = mod.VaultMonitor()
            monitor.set_vaults(["/tmp/testvault"])

        def bad_callback(*args):
            raise RuntimeError("boom")

        monitor.connect("external-file-created", bad_callback)

        mock_file = _make_mock_file("/tmp/testvault/new.md")
        mock_monitor = list(monitor._monitors.values())[0]

        with self.assertLogs(level="WARNING") as cm:
            monitor._on_monitor_event(mock_monitor, mock_file, None, _CRE)

        self.assertTrue(any("boom" in msg for msg in cm.output))


if __name__ == "__main__":
    unittest.main()
