"""Tests for VaultMonitor — Phase 1: init, set_vaults, cleanup.

Tests die Basis-Funktionalität des VaultMonitors:
- Initialisierung (leer)
- set_vaults() erstellt Monitore
- set_vaults([]) entfernt Monitore
- Non-existent paths werden ignoriert
- Keine Duplikate beim erneuten set_vaults
- N.1: Subdirectories werden rekursiv überwacht
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Gio.FileMonitorEvent constants
_CRE = 3
_DEL = 2
_HINT = 1
_MOI = 9
_MOO = 10


def _make_mock_gio():
    """Erstellt ein gemoddetes Gio-Modul mit unabhängigen Monitoren."""
    mock_gio = MagicMock()

    def make_file(path):
        mock_file = MagicMock()
        mock_file.monitor_directory.return_value = MagicMock()
        return mock_file

    mock_gio.File.new_for_path.side_effect = make_file
    FileMonitorFlags = type("FileMonitorFlags", (), {"WATCH_MOVES": 8})
    mock_gio.FileMonitorFlags = FileMonitorFlags
    mock_gio.FileMonitorEvent = MagicMock()
    mock_gio.FileMonitorEvent.CREATED = _CRE
    mock_gio.FileMonitorEvent.DELETED = _DEL
    mock_gio.FileMonitorEvent.CHANGES_DONE_HINT = _HINT
    mock_gio.FileMonitorEvent.MOVED_IN = _MOI
    mock_gio.FileMonitorEvent.MOVED_OUT = _MOO
    return mock_gio


def _make_mock_glib():
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
    for mod in list(sys.modules.keys()):
        if mod == "src.vault_monitor" or mod.startswith("src.vault_monitor."):
            del sys.modules[mod]
    import gi.repository
    gi.repository.Gio = mock_gio
    gi.repository.GLib = mock_glib
    import src.vault_monitor
    return src.vault_monitor


class TestVaultMonitorInit(unittest.TestCase):
    """Phase 1: VaultMonitor Initialisierung."""

    def test_init_creates_empty_monitors_dict(self):
        with patch("src.vault_monitor.Gio", _make_mock_gio()):
            from src.vault_monitor import VaultMonitor
            monitor = VaultMonitor()
            self.assertEqual(monitor._monitors, {})

    def test_init_sets_empty_vault_paths(self):
        mock_gio = _make_mock_gio()
        with patch("src.vault_monitor.Gio", mock_gio):
            from src.vault_monitor import VaultMonitor
            monitor = VaultMonitor()
            self.assertEqual(monitor._vault_paths, [])


class TestVaultMonitorSetVaults(unittest.TestCase):
    """Phase 1: set_vaults erstellt/entfernt Monitore."""

    def test_set_vaults_creates_monitor_for_one_vault(self):
        """Ein vault path sollte einen Monitor erstellen."""
        mock_gio = _make_mock_gio()
        with patch("src.vault_monitor.Gio", mock_gio):
            with patch("src.vault_monitor.os.path.isdir", return_value=True):
                from src.vault_monitor import VaultMonitor
                monitor = VaultMonitor()
                monitor.set_vaults(["/tmp/testvault"])

                mock_gio.File.new_for_path.assert_called_once_with("/tmp/testvault")
                self.assertIn("/tmp/testvault", monitor._monitors)

    def test_set_vaults_creates_monitors_for_multiple_vaults(self):
        """Mehrere vault paths sollten mehrere Monitore erstellen."""
        mock_gio = _make_mock_gio()
        with patch("src.vault_monitor.Gio", mock_gio):
            with patch("src.vault_monitor.os.path.isdir", return_value=True):
                from src.vault_monitor import VaultMonitor
                monitor = VaultMonitor()
                monitor.set_vaults(["/tmp/vault1", "/tmp/vault2"])

                self.assertEqual(mock_gio.File.new_for_path.call_count, 2)
                self.assertIn("/tmp/vault1", monitor._monitors)
                self.assertIn("/tmp/vault2", monitor._monitors)

    def test_set_vaults_removes_old_monitors(self):
        """Neue vault paths sollten alte Monitore mit cancel() entfernen."""
        mock_gio = _make_mock_gio()
        with patch("src.vault_monitor.Gio", mock_gio):
            with patch("src.vault_monitor.os.path.isdir", return_value=True):
                from src.vault_monitor import VaultMonitor
                monitor = VaultMonitor()
                monitor.set_vaults(["/tmp/vault1"])
                old_monitor = monitor._monitors["/tmp/vault1"]

                mock_gio.File.new_for_path.reset_mock()
                monitor.set_vaults(["/tmp/vault2"])

                self.assertNotIn("/tmp/vault1", monitor._monitors)
                self.assertIn("/tmp/vault2", monitor._monitors)
                old_monitor.cancel.assert_called_once()

    def test_set_vaults_clears_all_monitors(self):
        """set_vaults([]) sollte alle Monitore entfernen."""
        mock_gio = _make_mock_gio()
        with patch("src.vault_monitor.Gio", mock_gio):
            with patch("src.vault_monitor.os.path.isdir", return_value=True):
                from src.vault_monitor import VaultMonitor
                monitor = VaultMonitor()
                monitor.set_vaults(["/tmp/vault1", "/tmp/vault2"])
                monitors_before = dict(monitor._monitors)

                monitor.set_vaults([])

                self.assertEqual(monitor._monitors, {})
                # Alle Monitore wurden mit cancel() entfernt
                cancel_count = sum(m.cancel.call_count for m in monitors_before.values())
                self.assertEqual(cancel_count, len(monitors_before))

    def test_set_vaults_stores_paths(self):
        """set_vaults sollte _vault_paths speichern."""
        mock_gio = _make_mock_gio()
        with patch("src.vault_monitor.Gio", mock_gio):
            with patch("src.vault_monitor.os.path.isdir", return_value=True):
                from src.vault_monitor import VaultMonitor
                monitor = VaultMonitor()
                monitor.set_vaults(["/tmp/a", "/tmp/b"])
                self.assertEqual(monitor._vault_paths, ["/tmp/a", "/tmp/b"])

    def test_set_vaults_noop_same_paths(self):
        """Selbe vault_paths sollten keine neuen Monitore erstellen."""
        mock_gio = _make_mock_gio()
        with patch("src.vault_monitor.Gio", mock_gio):
            with patch("src.vault_monitor.os.path.isdir", return_value=True):
                from src.vault_monitor import VaultMonitor
                monitor = VaultMonitor()
                monitor.set_vaults(["/tmp/vault1"])
                call_count_after_first = mock_gio.File.new_for_path.call_count

                # Gleiche Pfade erneut setzen
                monitor.set_vaults(["/tmp/vault1"])

                # Kein weiterer Aufruf von new_for_path
                self.assertEqual(mock_gio.File.new_for_path.call_count, call_count_after_first)

    def test_set_vaults_nonexistent_path_no_monitor(self):
        """Non-existent path sollte keinen Monitor erstellen und nicht crashen."""
        mock_gio = _make_mock_gio()
        with patch("src.vault_monitor.Gio", mock_gio):
            with patch("src.vault_monitor.os.path.isdir", return_value=False):
                from src.vault_monitor import VaultMonitor
                monitor = VaultMonitor()
                # Sollte nicht crashen und keinen Monitor erstellen
                monitor.set_vaults(["/nonexistent/xyz/path"])

                self.assertNotIn("/nonexistent/xyz/path", monitor._monitors)
                mock_gio.File.new_for_path.assert_not_called()

    def test_set_vaults_mixed_existing_and_nonexistent(self):
        """Gemischte Pfade: existierende Monitore, nicht-existierende ignoriert."""
        mock_gio = _make_mock_gio()
        with patch("src.vault_monitor.Gio", mock_gio):
            def isdir_side_effect(path):
                return path == "/tmp/existing"
            with patch("src.vault_monitor.os.path.isdir", side_effect=isdir_side_effect):
                from src.vault_monitor import VaultMonitor
                monitor = VaultMonitor()
                monitor.set_vaults(["/tmp/existing", "/nonexistent/path"])

                self.assertIn("/tmp/existing", monitor._monitors)
                self.assertNotIn("/nonexistent/path", monitor._monitors)


class TestVaultMonitorSubdirectories(unittest.TestCase):
    """N.1: _start_monitor must create monitors for subdirectories."""

    def _create_monitor(self, vault_path="/tmp/vault"):
        mock_gio = _make_mock_gio()
        mock_glib = _make_mock_glib()
        with patch("src.vault_monitor.os.path.isdir", return_value=True):
            mod = _load_monitor(mock_gio, mock_glib)
            monitor = mod.VaultMonitor()
            monitor.set_vaults([vault_path])
            return monitor, mock_gio

    def test_start_monitor_creates_child_monitors(self):
        """When vault has subdirs, monitors must be created for each."""
        mock_gio = _make_mock_gio()
        mock_glib = _make_mock_glib()

        def isdir_side_effect(path):
            return path in (
                "/tmp/vault",
                "/tmp/vault/subdir1",
                "/tmp/vault/subdir1/subdir2",
            )

        def listdir_side_effect(path):
            if path == "/tmp/vault":
                return ["subdir1", "file.md"]
            if path == "/tmp/vault/subdir1":
                return ["subdir2", "note.md"]
            if path == "/tmp/vault/subdir1/subdir2":
                return ["deep.md"]
            return []

        with patch("src.vault_monitor.os.path.isdir", side_effect=isdir_side_effect):
            with patch("src.vault_monitor.os.listdir", side_effect=listdir_side_effect):
                mod = _load_monitor(mock_gio, mock_glib)
                monitor = mod.VaultMonitor()
                monitor.set_vaults(["/tmp/vault"])

        created_paths = [
            c[0][0] for c in mock_gio.File.new_for_path.call_args_list
        ]
        self.assertIn("/tmp/vault", created_paths)
        self.assertIn("/tmp/vault/subdir1", created_paths)
        self.assertIn("/tmp/vault/subdir1/subdir2", created_paths)

    def test_hidden_directories_are_skipped(self):
        """Directories starting with . must not get monitors."""
        mock_gio = _make_mock_gio()
        mock_glib = _make_mock_glib()

        def isdir_side_effect(path):
            return path in (
                "/tmp/vault",
                "/tmp/vault/.git",
                "/tmp/vault/.hidden",
                "/tmp/vault/subdir",
            )

        def listdir_side_effect(path):
            if path == "/tmp/vault":
                return [".git", ".hidden", "subdir"]
            if path == "/tmp/vault/subdir":
                return []
            return []

        with patch("src.vault_monitor.os.path.isdir", side_effect=isdir_side_effect):
            with patch("src.vault_monitor.os.listdir", side_effect=listdir_side_effect):
                mod = _load_monitor(mock_gio, mock_glib)
                monitor = mod.VaultMonitor()
                monitor.set_vaults(["/tmp/vault"])

        created_paths = [
            c[0][0] for c in mock_gio.File.new_for_path.call_args_list
        ]
        self.assertIn("/tmp/vault", created_paths)
        self.assertIn("/tmp/vault/subdir", created_paths)
        self.assertNotIn("/tmp/vault/.git", created_paths)
        self.assertNotIn("/tmp/vault/.hidden", created_paths)

    def test_child_monitor_catches_nested_file_events(self):
        """Events from files in subdirectories must be forwarded."""
        mock_gio = _make_mock_gio()
        mock_glib = _make_mock_glib()

        with patch("src.vault_monitor.os.path.isdir", return_value=True):
            mod = _load_monitor(mock_gio, mock_glib)
            monitor = mod.VaultMonitor()
            monitor.set_vaults(["/tmp/vault"])

        received = []
        monitor.connect("external-file-created", lambda *args: received.append(args))

        child_monitor = MagicMock()
        monitor._monitors["/tmp/vault/subdir"] = child_monitor

        mock_file = _make_mock_file("/tmp/vault/subdir/note.md")
        monitor._on_monitor_event(child_monitor, mock_file, None, _CRE)

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0][1], "/tmp/vault/subdir/note.md")


if __name__ == "__main__":
    unittest.main()
