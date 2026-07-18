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
import time
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
                with patch("src.vault_monitor.os.listdir", return_value=[]):
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
                with patch("src.vault_monitor.os.listdir", return_value=[]):
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
                with patch("src.vault_monitor.os.listdir", return_value=[]):
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
                with patch("src.vault_monitor.os.listdir", return_value=[]):
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
                with patch("src.vault_monitor.os.listdir", return_value=[]):
                    from src.vault_monitor import VaultMonitor
                    monitor = VaultMonitor()
                    monitor.set_vaults(["/tmp/a", "/tmp/b"])
                    self.assertEqual(monitor._vault_paths, ["/tmp/a", "/tmp/b"])

    def test_set_vaults_noop_same_paths(self):
        """Selbe vault_paths sollten keine neuen Monitore erstellen."""
        mock_gio = _make_mock_gio()
        with patch("src.vault_monitor.Gio", mock_gio):
            with patch("src.vault_monitor.os.path.isdir", return_value=True):
                with patch("src.vault_monitor.os.listdir", return_value=[]):
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
                with patch("src.vault_monitor.os.listdir", return_value=[]):
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
            with patch("src.vault_monitor.os.listdir", return_value=[]):
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
            with patch("src.vault_monitor.os.listdir", return_value=[]):
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


# ── Skip-Logik Tests ──────────────────────────────────────────────


class TestSkipNextEvent(unittest.TestCase):
    """skip_next_event: Ref-Counter für Skip-Pfade."""

    def _create_monitor(self):
        mock_gio = _make_mock_gio()
        mock_glib = _make_mock_glib()
        with patch("src.vault_monitor.os.path.isdir", return_value=True):
            with patch("src.vault_monitor.os.listdir", return_value=[]):
                mod = _load_monitor(mock_gio, mock_glib)
                monitor = mod.VaultMonitor()
                monitor.set_vaults(["/tmp/vault"])
                return monitor

    def test_skip_sets_count_to_1(self):
        """Erster skip_next_event setzt Count auf 1."""
        monitor = self._create_monitor()
        monitor.skip_next_event("/tmp/vault/note.md")
        self.assertEqual(monitor._skip_paths["/tmp/vault/note.md"], 1)

    def test_skip_increments_count(self):
        """Zweiter skip_next_event erhöht Count auf 2."""
        monitor = self._create_monitor()
        monitor.skip_next_event("/tmp/vault/note.md")
        monitor.skip_next_event("/tmp/vault/note.md")
        self.assertEqual(monitor._skip_paths["/tmp/vault/note.md"], 2)

    def test_skip_records_timestamp(self):
        """skip_next_event setzt Zeitstempel."""
        monitor = self._create_monitor()
        before = time.monotonic()
        monitor.skip_next_event("/tmp/vault/note.md")
        after = time.monotonic()
        ts = monitor._skip_timestamps["/tmp/vault/note.md"]
        self.assertGreaterEqual(ts, before)
        self.assertLessEqual(ts, after)

    def test_skip_different_paths_independent(self):
        """Verschiedene Pfade haben unabhängige Counter."""
        monitor = self._create_monitor()
        monitor.skip_next_event("/tmp/vault/a.md")
        monitor.skip_next_event("/tmp/vault/b.md")
        monitor.skip_next_event("/tmp/vault/b.md")
        self.assertEqual(monitor._skip_paths["/tmp/vault/a.md"], 1)
        self.assertEqual(monitor._skip_paths["/tmp/vault/b.md"], 2)


class TestDecrementSkip(unittest.TestCase):
    """_decrement_skip: Counter-Decrement, TTL, Entry-Entfernung."""

    def _create_monitor(self):
        mock_gio = _make_mock_gio()
        mock_glib = _make_mock_glib()
        with patch("src.vault_monitor.os.path.isdir", return_value=True):
            with patch("src.vault_monitor.os.listdir", return_value=[]):
                mod = _load_monitor(mock_gio, mock_glib)
                monitor = mod.VaultMonitor()
                monitor.set_vaults(["/tmp/vault"])
                return monitor

    def test_decrement_removes_entry_at_count_1(self):
        """Count=1 nach Decrement → Entry entfernt."""
        monitor = self._create_monitor()
        monitor.skip_next_event("/tmp/vault/note.md")
        self.assertIn("/tmp/vault/note.md", monitor._skip_paths)
        monitor._decrement_skip("/tmp/vault/note.md")
        self.assertNotIn("/tmp/vault/note.md", monitor._skip_paths)

    def test_decrement_decreases_count_from_2(self):
        """Count=2 nach Decrement → Count=1."""
        monitor = self._create_monitor()
        monitor.skip_next_event("/tmp/vault/note.md")
        monitor.skip_next_event("/tmp/vault/note.md")
        monitor._decrement_skip("/tmp/vault/note.md")
        self.assertEqual(monitor._skip_paths["/tmp/vault/note.md"], 1)

    def test_decrement_removes_stale_entry(self):
        """Timestamp >2s alt → Entry wird entfernt (egal Count)."""
        monitor = self._create_monitor()
        monitor.skip_next_event("/tmp/vault/note.md")
        monitor.skip_next_event("/tmp/vault/note.md")  # count=2
        # Simuliere alten Timestamp
        monitor._skip_timestamps["/tmp/vault/note.md"] = time.monotonic() - 5.0
        monitor._decrement_skip("/tmp/vault/note.md")
        self.assertNotIn("/tmp/vault/note.md", monitor._skip_paths)

    def test_decrement_nonexistent_path_no_crash(self):
        """Nicht-existierender Pfad → kein Crash."""
        monitor = self._create_monitor()
        monitor._decrement_skip("/nonexistent")
        # Sollte nicht crashen

    def test_decrement_removes_timestamp_on_removal(self):
        """Bei Entry-Entfernung wird auch Timestamp entfernt."""
        monitor = self._create_monitor()
        monitor.skip_next_event("/tmp/vault/note.md")
        monitor._decrement_skip("/tmp/vault/note.md")
        self.assertNotIn("/tmp/vault/note.md", monitor._skip_timestamps)


class TestEmitEventSkipLogic(unittest.TestCase):
    """_emit_event: Skip-Logik für created/changed/moved/renamed."""

    def _create_monitor(self):
        mock_gio = _make_mock_gio()
        mock_glib = _make_mock_glib()
        with patch("src.vault_monitor.os.path.isdir", return_value=True):
            with patch("src.vault_monitor.os.listdir", return_value=[]):
                mod = _load_monitor(mock_gio, mock_glib)
                monitor = mod.VaultMonitor()
                monitor.set_vaults(["/tmp/vault"])
                return monitor

    def test_created_event_skipped_when_in_skip_paths(self):
        """CREATED Event wird übersprungen wenn Pfad in _skip_paths."""
        monitor = self._create_monitor()
        received = []
        monitor.connect("external-file-created", lambda *args: received.append(args))
        monitor.skip_next_event("/tmp/vault/note.md")
        monitor._emit_event("/tmp/vault", "/tmp/vault/note.md", None, "created")
        self.assertEqual(len(received), 0)

    def test_changed_event_skipped_when_in_skip_paths(self):
        """CHANGED Event wird übersprungen wenn Pfad in _skip_paths."""
        monitor = self._create_monitor()
        received = []
        monitor.connect("external-content-changed", lambda *args: received.append(args))
        monitor.skip_next_event("/tmp/vault/note.md")
        monitor._emit_event("/tmp/vault", "/tmp/vault/note.md", None, "changed")
        self.assertEqual(len(received), 0)

    def test_created_event_not_skipped_without_skip(self):
        """CREATED Event wird emittiert wenn kein Skip registriert."""
        monitor = self._create_monitor()
        received = []
        monitor.connect("external-file-created", lambda *args: received.append(args))
        monitor._emit_event("/tmp/vault", "/tmp/vault/note.md", None, "created")
        self.assertEqual(len(received), 1)

    def test_moved_event_skipped_when_file_in_skip_paths(self):
        """MOVED Event wird übersprungen wenn file_path in _skip_paths."""
        monitor = self._create_monitor()
        received = []
        monitor.connect("external-file-moved", lambda *args: received.append(args))
        monitor.skip_next_event("/tmp/vault/new.md")
        monitor._emit_event("/tmp/vault", "/tmp/vault/new.md", "/tmp/vault/old.md", "moved")
        self.assertEqual(len(received), 0)

    def test_moved_event_skipped_when_other_in_skip_paths(self):
        """MOVED Event wird übersprungen wenn other_path in _skip_paths."""
        monitor = self._create_monitor()
        received = []
        monitor.connect("external-file-moved", lambda *args: received.append(args))
        monitor.skip_next_event("/tmp/vault/old.md")
        monitor._emit_event("/tmp/vault", "/tmp/vault/new.md", "/tmp/vault/old.md", "moved")
        self.assertEqual(len(received), 0)

    def test_skip_counter_decremented_on_event_skip(self):
        """Bei Skip wird Counter dekrementiert."""
        monitor = self._create_monitor()
        monitor.connect("external-file-created", lambda *args: None)
        monitor.skip_next_event("/tmp/vault/note.md")
        monitor.skip_next_event("/tmp/vault/note.md")
        monitor._emit_event("/tmp/vault", "/tmp/vault/note.md", None, "created")
        self.assertEqual(monitor._skip_paths["/tmp/vault/note.md"], 1)

    def test_two_skips_consume_both_created_and_changed(self):
        """2 Skips konsumieren CREATED + CHANGED von touch()."""
        monitor = self._create_monitor()
        received_created = []
        received_changed = []
        monitor.connect("external-file-created", lambda *args: received_created.append(args))
        monitor.connect("external-content-changed", lambda *args: received_changed.append(args))
        monitor.skip_next_event("/tmp/vault/note.md")
        monitor.skip_next_event("/tmp/vault/note.md")
        monitor._emit_event("/tmp/vault", "/tmp/vault/note.md", None, "created")
        monitor._emit_event("/tmp/vault", "/tmp/vault/note.md", None, "changed")
        self.assertEqual(len(received_created), 0)
        self.assertEqual(len(received_changed), 0)

    def test_one_skip_only_consumes_one_event(self):
        """1 Skip konsumiert nur 1 Event, 2. geht durch."""
        monitor = self._create_monitor()
        received = []
        monitor.connect("external-content-changed", lambda *args: received.append(args))
        monitor.skip_next_event("/tmp/vault/note.md")
        # Erstes Event: created → konsumiert
        monitor._emit_event("/tmp/vault", "/tmp/vault/note.md", None, "created")
        self.assertEqual(len(received), 0)
        # Zweites Event: changed → kein Skip mehr → geht durch
        monitor._emit_event("/tmp/vault", "/tmp/vault/note.md", None, "changed")
        self.assertEqual(len(received), 1)


class TestEmitExistingEntries(unittest.TestCase):
    """_emit_existing_entries: Rekursion, Monitor-Start, Hidden-Skip."""

    def _create_monitor(self):
        mock_gio = _make_mock_gio()
        mock_glib = _make_mock_glib()
        with patch("src.vault_monitor.os.path.isdir", return_value=True):
            with patch("src.vault_monitor.os.listdir", return_value=[]):
                mod = _load_monitor(mock_gio, mock_glib)
                monitor = mod.VaultMonitor()
                monitor.set_vaults(["/tmp/vault"])
                return monitor

    def test_emits_created_for_dir_itself(self):
        """Verzeichnis selbst bekommt CREATED Event."""
        monitor = self._create_monitor()
        received = []
        monitor.connect("external-file-created", lambda *args: received.append(args))
        with patch("src.vault_monitor.os.listdir", return_value=[]):
            monitor._emit_existing_entries("/tmp/vault", "/tmp/vault/sub")
        dir_events = [r for r in received if r[1] == "/tmp/vault/sub"]
        self.assertEqual(len(dir_events), 1)

    def test_emits_created_for_md_files(self):
        """*.md Dateien bekommen CREATED Events."""
        monitor = self._create_monitor()
        received = []
        monitor.connect("external-file-created", lambda *args: received.append(args))

        def listdir_side_effect(path):
            return ["note.md", "readme.txt"]

        with patch("src.vault_monitor.os.listdir", side_effect=listdir_side_effect):
            with patch("src.vault_monitor.os.path.isdir", return_value=False):
                monitor._emit_existing_entries("/tmp/vault", "/tmp/vault/sub")

        md_events = [r for r in received if r[1] == "/tmp/vault/sub/note.md"]
        txt_events = [r for r in received if "readme.txt" in str(r)]
        self.assertEqual(len(md_events), 1)
        self.assertEqual(len(txt_events), 0)

    def test_hidden_entries_skipped(self):
        """Versteckte Einträge (*.hidden*, .git/) werden ignoriert."""
        monitor = self._create_monitor()
        received = []
        monitor.connect("external-file-created", lambda *args: received.append(args))

        def listdir_side_effect(path):
            return [".hidden.md", ".git", "note.md"]

        def isdir_side_effect(path):
            return path == "/tmp/vault/sub/.git"

        with patch("src.vault_monitor.os.listdir", side_effect=listdir_side_effect):
            with patch("src.vault_monitor.os.path.isdir", side_effect=isdir_side_effect):
                monitor._emit_existing_entries("/tmp/vault", "/tmp/vault/sub")

        hidden_events = [r for r in received if ".hidden" in str(r) or ".git" in str(r)]
        self.assertEqual(len(hidden_events), 0)

    def test_recurses_into_subdirectories(self):
        """Rekursion in Unterverzeichnisse."""
        monitor = self._create_monitor()
        received = []
        monitor.connect("external-file-created", lambda *args: received.append(args))

        def listdir_side_effect(path):
            if path == "/tmp/vault/sub":
                return ["deep"]
            if path == "/tmp/vault/sub/deep":
                return ["deep.md"]
            return []

        def isdir_side_effect(path):
            return path == "/tmp/vault/sub/deep"

        with patch("src.vault_monitor.os.listdir", side_effect=listdir_side_effect):
            with patch("src.vault_monitor.os.path.isdir", side_effect=isdir_side_effect):
                with patch("src.vault_monitor.Gio.File.new_for_path") as mock_file:
                    mock_file.return_value.monitor_directory.return_value = MagicMock()
                    monitor._emit_existing_entries("/tmp/vault", "/tmp/vault/sub")

        deep_events = [r for r in received if "deep.md" in str(r)]
        self.assertEqual(len(deep_events), 1)

    def test_starts_monitor_for_subdirectories(self):
        """Für Unterverzeichnisse wird Monitor gestartet."""
        monitor = self._create_monitor()
        calls = []
        original_start = monitor._start_monitor

        def tracking_start(path):
            calls.append(path)
            original_start(path)

        monitor._start_monitor = tracking_start

        def listdir_side_effect(path):
            if path == "/tmp/vault/sub":
                return ["child"]
            return []

        def isdir_side_effect(path):
            return path == "/tmp/vault/sub/child"

        with patch("src.vault_monitor.os.listdir", side_effect=listdir_side_effect):
            with patch("src.vault_monitor.os.path.isdir", side_effect=isdir_side_effect):
                monitor._emit_existing_entries("/tmp/vault", "/tmp/vault/sub")

        self.assertIn("/tmp/vault/sub/child", calls)

    def test_vault_path_none_is_noop(self):
        """vault_path=None → kein Event."""
        monitor = self._create_monitor()
        received = []
        monitor.connect("external-file-created", lambda *args: received.append(args))
        monitor._emit_existing_entries(None, "/tmp/vault/sub")
        self.assertEqual(len(received), 0)

    def test_oserror_logged_not_crashed(self):
        """OSError bei os.listdir → geloggt, kein Crash."""
        monitor = self._create_monitor()
        with patch("src.vault_monitor.os.listdir", side_effect=OSError("perm")):
            # Sollte nicht crashen
            monitor._emit_existing_entries("/tmp/vault", "/tmp/vault/sub")


class TestDisconnect(unittest.TestCase):
    """disconnect: Callback-Entfernung."""

    def _create_monitor(self):
        mock_gio = _make_mock_gio()
        mock_glib = _make_mock_glib()
        with patch("src.vault_monitor.os.path.isdir", return_value=True):
            with patch("src.vault_monitor.os.listdir", return_value=[]):
                mod = _load_monitor(mock_gio, mock_glib)
                monitor = mod.VaultMonitor()
                monitor.set_vaults(["/tmp/vault"])
                return monitor

    def test_disconnect_removes_callback(self):
        """disconnect entfernt den Callback."""
        monitor = self._create_monitor()
        cb = lambda *args: None
        monitor.connect("external-file-created", cb)
        self.assertIn(("external-file-created", cb), monitor._callbacks)
        monitor.disconnect(cb)
        self.assertNotIn(("external-file-created", cb), monitor._callbacks)

    def test_disconnect_removes_from_multiple_signals(self):
        """disconnect entfernt Callback von allen Signals."""
        monitor = self._create_monitor()
        cb = lambda *args: None
        monitor.connect("external-file-created", cb)
        monitor.connect("external-file-deleted", cb)
        monitor.disconnect(cb)
        self.assertEqual(len([k for k, v in monitor._callbacks.items() if v == cb]), 0)

    def test_disconnect_other_callbacks_remain(self):
        """Andere Callbacks bleiben erhalten."""
        monitor = self._create_monitor()
        cb1 = lambda *args: None
        cb2 = lambda *args: None
        monitor.connect("external-file-created", cb1)
        monitor.connect("external-file-created", cb2)
        monitor.disconnect(cb1)
        self.assertIn(("external-file-created", cb2), monitor._callbacks)

    def test_disconnect_nonexistent_no_crash(self):
        """Nicht-existierender Callback → kein Crash."""
        monitor = self._create_monitor()
        monitor.disconnect(lambda *args: None)


class TestCleanup(unittest.TestCase):
    """cleanup: Alle Ressourcen aufraumen."""

    def test_cleanup_removes_all_monitors(self):
        """cleanup entfernt alle Monitore."""
        mock_gio = _make_mock_gio()
        mock_glib = _make_mock_glib()
        with patch("src.vault_monitor.os.path.isdir", return_value=True):
            with patch("src.vault_monitor.os.listdir", return_value=[]):
                mod = _load_monitor(mock_gio, mock_glib)
                monitor = mod.VaultMonitor()
                monitor.set_vaults(["/tmp/vault"])
                self.assertGreater(len(monitor._monitors), 0)
                monitor.cleanup()
                self.assertEqual(len(monitor._monitors), 0)

    def test_cleanup_cancels_debounce_timers(self):
        """cleanup entfernt alle Debounce-Timer."""
        mock_gio = _make_mock_gio()
        mock_glib = _make_mock_glib()
        with patch("src.vault_monitor.os.path.isdir", return_value=True):
            with patch("src.vault_monitor.os.listdir", return_value=[]):
                mod = _load_monitor(mock_gio, mock_glib)
                monitor = mod.VaultMonitor()
                monitor._debounce_timers["test"] = 123
                monitor.cleanup()
                self.assertEqual(len(monitor._debounce_timers), 0)


if __name__ == "__main__":
    unittest.main()
