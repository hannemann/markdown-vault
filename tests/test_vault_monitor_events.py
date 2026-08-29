"""Tests for VaultMonitor — Event-Filterung und -Weiterleitung.

Tests:
- Nur .md Events werden weitergeleitet
- .txt, .hidden.md, directories werden ignoriert
- Alle Event-Typen werden korrekt emittiert
- N.2: RENAMED events (same-dir rename) werden nicht gedroppt
- N.3: MOVED_IN ohne other_file ruft Callback mit 2 Args
- N.4: Callback-Exceptions werden geloggt statt verschluckt
"""

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
        if (mod == 'markdown_vault.vault.vault_monitor'
                or mod.startswith('markdown_vault.vault.vault_monitor.')):
            del sys.modules[mod]

    # gi.repository.Gio direkt patchen
    import gi.repository
    gi.repository.Gio = mock_gio
    gi.repository.GLib = mock_glib

    import markdown_vault.vault.vault_monitor
    return markdown_vault.vault.vault_monitor


class TestVaultMonitorFiltering(unittest.TestCase):
    """Phase 2: Event-Filterung — nur .md Dateien."""

    def _create_monitor(self, vault_path="/tmp/testvault"):
        mock_gio = _make_mock_gio()
        mock_glib = _make_mock_glib()

        with patch("markdown_vault.vault.vault_monitor.os.path.isdir", return_value=True):
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

        with patch("markdown_vault.vault.vault_monitor.os.path.isdir", return_value=True):
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
        with patch("markdown_vault.vault.vault_monitor.os.path.isdir", return_value=True):
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
        with patch("markdown_vault.vault.vault_monitor.os.path.isdir", return_value=True):
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


class TestR11_1_AtomicSaveRenamedFilter(unittest.TestCase):
    """R11.1 Lücke: RENAMED-Filter muss temp→.md durchlassen.

    Atomic-save writers (Claude Code, VS Code, vim backupcopy=no)
    schreiben in eine temp-Datei und rename() sie über das Ziel.
    Gio meldet das als RENAMED mit file=temp, other=target.

    Der Filter an Zeile 338 prüft:
        _is_valid_md_file(file) or _is_valid_md_file(other_file)

    Ohne den `or _is_valid_md_file(other_file)` Teil wird das Event
    gedroppt, weil file (temp-Name) keine .md-Endung hat.

    Dieser Test stellt sicher, dass die Filter-Logik den Ziel-Pfad
    berücksichtigt — ein Zurück revertieren auf `_is_valid_md_file(file)`
    allein lässt den Test durchfallen.
    """

    def _create_monitor(self):
        mock_gio = _make_mock_gio()
        mock_glib = _make_mock_glib()
        with patch("markdown_vault.vault.vault_monitor.os.path.isdir", return_value=True):
            mod = _load_monitor(mock_gio, mock_glib)
            monitor = mod.VaultMonitor()
            monitor.set_vaults(["/tmp/testvault"])
            return monitor

    def test_renamed_temp_to_md_emits_file_moved(self):
        """RENAMED von .tmp → .md muss durch den Filter durchkommen."""
        monitor = self._create_monitor()
        received = []
        monitor.connect("external-file-moved", lambda *args: received.append(args))

        mock_tmp = _make_mock_file("/tmp/testvault/note.md.tmp.12345")
        mock_target = _make_mock_file("/tmp/testvault/note.md")
        mock_monitor = list(monitor._monitors.values())[0]
        # Gio RENAMED: file=temp, other=target
        monitor._on_monitor_event(mock_monitor, mock_tmp, mock_target, _RENAMED)

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0][1], "/tmp/testvault/note.md")
        self.assertEqual(received[0][2], "/tmp/testvault/note.md.tmp.12345")

    def test_renamed_md_to_temp_passes_filter(self):
        """RENAMED von .md → .tmp muss den Filter passieren.

        RENAMED emittiert immer das "moved"-Signal (via _SIGNAL_NAMES).
        Die Semantik (create/changed/delete) wird in on_file_moved
        klassifiziert. Wichtig: Der Filter muss das Event durchlassen,
        sonst wird es still verschluckt.
        """
        mock_gio = _make_mock_gio()
        mock_glib = _make_mock_glib()
        # is_dir=True nur für Verzeichnisse, False für Dateien
        def is_dir_side_effect(path):
            return path.endswith("/") or path in ("/tmp/testvault", "/tmp/testvault/subdir")
        with patch("markdown_vault.vault.vault_monitor.os.path.isdir",
                   side_effect=is_dir_side_effect):
            mod = _load_monitor(mock_gio, mock_glib)
            monitor = mod.VaultMonitor()
            monitor.set_vaults(["/tmp/testvault"])
            monitor._monitors["/tmp/testvault/subdir"] = MagicMock()

        received_moved = []
        monitor.connect("external-file-moved", lambda *args: received_moved.append(args))

        mock_target = _make_mock_file("/tmp/testvault/subdir/note.md")
        mock_tmp = _make_mock_file("/tmp/testvault/subdir/note.md.tmp.12345")
        mock_monitor = list(monitor._monitors.values())[0]
        # Gio RENAMED: file=target(.md), other=tmp
        monitor._on_monitor_event(mock_monitor, mock_target, mock_tmp, _RENAMED)

        # Event muss durchkommen — on_file_moved klassifiziert es später
        self.assertEqual(len(received_moved), 1)
        self.assertEqual(received_moved[0][1], "/tmp/testvault/subdir/note.md.tmp.12345")
        self.assertEqual(received_moved[0][2], "/tmp/testvault/subdir/note.md")


class TestAtomicSaveAnnouncement(unittest.TestCase):
    """An atomic save renames <note>.part onto <note>, which reaches the monitor as a rename
    and would otherwise be reported as an external change. The app announces that exact PAIR
    beforehand, so the monitor recognises its own save by identity.

    Deliberately not the skip_next_event counter ("ignore the next event, whatever it is"):
    that one is blind, so a concurrent external change consumes it — the real change is
    swallowed and our own save then raises the banner, i.e. wrong in both directions. Matching
    the pair keeps every other event untouched, and a leftover announcement is inert because
    only this one rename can redeem it.
    """

    def _monitor(self):
        mock_gio = _make_mock_gio()
        mock_glib = _make_mock_glib()
        with patch("markdown_vault.vault.vault_monitor.os.path.isdir", return_value=True):
            mod = _load_monitor(mock_gio, mock_glib)
            monitor = mod.VaultMonitor()
            monitor.set_vaults(["/vaulttest"])
            return monitor

    def _rename(self, monitor, part, target):
        """Feed the monitor a Gio RENAMED (file=source, other=destination)."""
        mock_monitor = list(monitor._monitors.values())[0]
        monitor._on_monitor_event(mock_monitor, _make_mock_file(part),
                                  _make_mock_file(target), _RENAMED)

    def setUp(self):
        from markdown_vault.core import vault_fs
        self.note = "/vaulttest/note.md"
        self.part, self.target = vault_fs.atomic_save_paths(self.note)
        self.monitor = self._monitor()
        self.moved = []
        self.changed = []
        self.monitor.connect("external-file-moved", lambda *a: self.moved.append(a))
        self.monitor.connect("external-content-changed", lambda *a: self.changed.append(a))

    def test_an_announced_atomic_save_is_not_reported_as_external(self):
        self.monitor.expect_atomic_save(self.note)
        self._rename(self.monitor, self.part, self.target)
        self.assertEqual(self.moved, [])

    def test_an_external_change_to_the_same_note_still_gets_through(self):
        # The race the blind counter gets wrong: a concurrent external write must still be
        # reported while our own save is pending. Its event is a content change, not our
        # rename pair, so the announcement must leave it alone.
        self.monitor.expect_atomic_save(self.note)
        mock_monitor = list(self.monitor._monitors.values())[0]
        self.monitor._on_monitor_event(mock_monitor, _make_mock_file(self.target), None, _HINT)
        self.assertEqual(len(self.changed), 1)

    def test_a_rename_of_a_different_note_is_unaffected(self):
        self.monitor.expect_atomic_save(self.note)
        self._rename(self.monitor, "/vaulttest/other.md", "/vaulttest/renamed.md")
        self.assertEqual(len(self.moved), 1)

    def test_announcing_twice_leaves_nothing_behind(self):
        # Two saves inside the 200 ms debounce coalesce into ONE event. With a counter the
        # second announcement would leak; identity has nothing to count, so after the single
        # event the announcement is gone and the next genuine rename is reported.
        self.monitor.expect_atomic_save(self.note)
        self.monitor.expect_atomic_save(self.note)
        self._rename(self.monitor, self.part, self.target)
        self.assertEqual(self.moved, [])
        self._rename(self.monitor, self.part, self.target)
        self.assertEqual(len(self.moved), 1)

    def test_forget_withdraws_the_announcement(self):
        # A failed save produces no event; withdrawing keeps the announcement from lingering.
        self.monitor.expect_atomic_save(self.note)
        self.monitor.forget_atomic_save(self.note)
        self._rename(self.monitor, self.part, self.target)
        self.assertEqual(len(self.moved), 1)

    def test_forgetting_what_was_never_announced_is_harmless(self):
        self.monitor.forget_atomic_save(self.note)   # must not raise

    def test_an_unannounced_atomic_save_is_reported(self):
        # Another editor atomically saving our note IS an external change — it must reach the
        # banner. Pins that the announcement is what distinguishes them, not the shape.
        self._rename(self.monitor, self.part, self.target)
        self.assertEqual(len(self.moved), 1)


class TestN4_CallbackExceptionLogging(unittest.TestCase):
    """N.4: Callback exceptions must be logged, not silently swallowed."""

    def test_callback_exception_is_logged(self):
        """When a callback raises, the exception must be logged."""
        mock_gio = _make_mock_gio()
        mock_glib = _make_mock_glib()
        with patch("markdown_vault.vault.vault_monitor.os.path.isdir", return_value=True):
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
