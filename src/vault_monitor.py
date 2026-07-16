"""VaultMonitor — Directory watching with Gio.FileMonitor.

Monitors vault directories for file changes (created, deleted, moved, changed).
Only monitors .md files. Uses CHANGES_DONE_HINT for content-changed events.
"""

import os
from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gio, GLib


def _is_valid_md_file(file_path, other_file_path=None):
    """Prueft, ob es sich um eine gueltige .md Datei handelt.

    - Endung muss .md sein
    - Dateiname darf nicht mit . beginnen (.hidden.md → ignoriert)
    - Parent-Verzeichnisse duerfen nicht mit . beginnen (.git/file.md → ignoriert)

    Args:
        file_path: Pfad der Datei ( Gio.File oder str)
        other_file_path: Zweiter Pfad bei MOVE-Events ( Gio.File oder str)

    Returns:
        True wenn die Datei ueberwacht werden soll
    """
    if file_path is None:
        return False

    if hasattr(file_path, "get_path"):
        fpath = file_path.get_path()
    else:
        fpath = str(file_path)

    if not fpath:
        return False

    name = Path(fpath).name

    # Endung muss .md sein
    if not name.endswith(".md"):
        return False

    # Versteckte Dateien ignorieren (.hidden.md)
    if name.startswith("."):
        return False

    # Versteckte Verzeichnisse ignorieren (.git/file.md)
    if other_file_path is not None:
        if hasattr(other_file_path, "get_path"):
            ofpath = other_file_path.get_path()
        else:
            ofpath = str(other_file_path)
        parts = Path(ofpath).parts if ofpath else ()
        for part in parts:
            if part.startswith(".") and part != ".":
                return False
    else:
        parts = Path(fpath).parts
        for part in parts:
            if part.startswith(".") and part != ".":
                return False

    return True


def _is_valid_md_dir(dir_path):
    """Prueft, ob ein Verzeichnis gueltig fuer Monitoring ist.

    Args:
        dir_path: Pfad des Verzeichnisses

    Returns:
        True wenn das Verzeichnis ueberwacht werden soll
    """
    if dir_path is None:
        return False

    if hasattr(dir_path, "get_path"):
        dpath = dir_path.get_path()
    else:
        dpath = str(dir_path)

    if not dpath:
        return False

    name = Path(dpath).name
    if name.startswith("."):
        return False

    parts = Path(dpath).parts
    for part in parts:
        if part.startswith(".") and part != ".":
            return False

    return True


class VaultMonitor:
    """Monitors vault directories for file system changes.

    Uses Gio.FileMonitor to watch vault directories. Only monitors .md files
    that are not in hidden directories and not hidden themselves.

    Public API:
        - set_vaults(vault_paths): Start/stop monitoring for given vault paths
        - connect(signal, callback): Connect to signals
        - disconnect(callback): Disconnect callbacks
        - cleanup(): Stop all monitors
    """

    def __init__(self):
        """Initialisiert einen leeren VaultMonitor."""
        self._monitors = {}  # {vault_path: Gio.FileMonitor}
        self._vault_paths = []  # Liste der aktuell ue berwachten Pfade
        self._debounce_timers = {}  # {event_key: GLib.Source}
        self._skip_paths = set()  # Pfade whose naechstes Changed-Event ignoriert wird

        # Signale: (signal_name, callback)
        self._callbacks = {}

        # Gio.FileMonitorEvent Mapping — nur CHANGES_DONE_HINT triggert changed
        self._EVENT_MAP = {
            Gio.FileMonitorEvent.CHANGES_DONE_HINT: "changed",
            Gio.FileMonitorEvent.CREATED: "created",
            Gio.FileMonitorEvent.DELETED: "deleted",
            Gio.FileMonitorEvent.MOVED_IN: "moved",
            Gio.FileMonitorEvent.MOVED_OUT: "moved",
        }

        # Signal-Names
        self._SIGNAL_NAMES = {
            "created": "external-file-created",
            "deleted": "external-file-deleted",
            "moved": "external-file-moved",
            "changed": "external-content-changed",
        }

    def set_vaults(self, vault_paths):
        """Setzt die zu ue berwachenden Vault-Verzeichnisse.

        Erstellt Monitore fuer neue Pfade, entfernt Monitore fuer geloeschte Pfade.
        Non-existent paths werden ignoriert (kein Monitor, kein Crash).

        Args:
            vault_paths: Liste absoluter Verzeichnis-Pfade
        """
        if not vault_paths:
            self._stop_all_monitors()
            self._vault_paths = []
            return

        # Vorherige Pfade als Menge
        old_paths = set(self._vault_paths)
        new_paths = set(vault_paths)

        # Neue Pfade → Monitor erstellen
        for path in new_paths - old_paths:
            self._start_monitor(path)

        # Alte Pfade → Monitor entfernen
        for path in old_paths - new_paths:
            self._stop_monitor(path)

        self._vault_paths = list(vault_paths)

    def skip_next_event(self, file_path: str) -> None:
        """Markiere einen Pfad fuer das Ignorieren des naechsten Changed-Events.

        Wird vor dem internen Speichern aufgerufen, damit der eigene
        File-Monitor nicht als externe Aenderung interpretiert wird.
        """
        self._skip_paths.add(file_path)

    def _start_monitor(self, vault_path):
        """Startet einen FileMonitor fuer ein Vault-Verzeichnis.

        Args:
            vault_path: Absoluter Pfad zum Vault-Verzeichnis
        """
        if not vault_path or not os.path.isdir(vault_path):
            return

        try:
            file = Gio.File.new_for_path(vault_path)
            monitor = file.monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES, None)
            monitor.connect("changed", self._on_monitor_event)
            self._monitors[vault_path] = monitor
        except GLib.Error:
            # Gio.FileMonitor kann nicht erstellt werden → ignorieren
            pass

    def _stop_monitor(self, vault_path):
        """Entfernt einen FileMonitor.

        Args:
            vault_path: Pfad des Vault-Verzeichnisses
        """
        monitor = self._monitors.pop(vault_path, None)
        if monitor is not None:
            try:
                monitor.cancel()
            except Exception:
                pass

    def _stop_all_monitors(self):
        """Entfernt alle FileMonitore."""
        for path in list(self._monitors.keys()):
            self._stop_monitor(path)

    def cleanup(self):
        """Raumt alle Ressourcen auf (Monitore + Timer)."""
        self._stop_all_monitors()
        self._cancel_all_debounce_timers()

    def connect(self, signal_name, callback):
        """Verbindet einen Callback mit einem Signal.

        Args:
            signal_name: Name des Signals (z.B. 'external-file-created')
            callback: Callback-Funktion
        """
        key = (signal_name, callback)
        if key not in self._callbacks:
            self._callbacks[key] = callback

    def disconnect(self, callback):
        """Trennt einen Callback von allen Signals.

        Args:
            callback: Der zu trennende Callback
        """
        keys_to_remove = [
            k for k, v in self._callbacks.items() if v == callback
        ]
        for key in keys_to_remove:
            del self._callbacks[key]

    def _on_monitor_event(self, monitor, file, other_file, event_type, user_data=None):
        """Callback wenn ein FileMonitor-Event auftritt."""
        mapped_type = self._EVENT_MAP.get(event_type)
        if mapped_type is None:
            return

        if mapped_type in ("created", "deleted", "moved"):
            if not _is_valid_md_file(file, other_file):
                return
        elif mapped_type == "changed":
            if not _is_valid_md_file(file):
                return
            if other_file is not None:
                if not _is_valid_md_dir(file):
                    return

        signal_name = self._SIGNAL_NAMES.get(mapped_type)
        if not signal_name:
            return

        vault_path = self._get_vault_path(monitor)
        if vault_path is None:
            return

        if hasattr(file, "get_path"):
            file_path = file.get_path()
        else:
            file_path = str(file)

        other_path = None
        if other_file is not None:
            if hasattr(other_file, "get_path"):
                other_path = other_file.get_path()
            else:
                other_path = str(other_file)

        event_key = f"{vault_path}:{file_path}:{mapped_type}"
        self._debounce_event(event_key, vault_path, file_path, other_path, mapped_type)

    def _get_vault_path(self, monitor):
        """Ermittelt den Vault-Pfad aus einem FileMonitor.

        Args:
            monitor: Gio.FileMonitor

        Returns:
            Vault-Pfad oder None
        """
        for path, mon in self._monitors.items():
            if mon is monitor:
                return path
        return None

    def _debounce_event(self, event_key, vault_path, file_path, other_path, event_type):
        """Debounced Verarbeitung von Events.

        Verwendet GLib.timeout_add(200ms) mit Reset bei jedem neuen Event.
        Erst nach 200ms Ruhe werden die Events verarbeitet.

        Args:
            event_key: Eindeutiger Schluessel fuer das Event
            vault_path: Pfad des Vault-Verzeichnisses
            file_path: Pfad der betroffenen Datei
            other_path: Zweiter Pfad bei MOVE-Events
            event_type: Event-Typ (created, deleted, moved, changed)
        """
        # Bestehenden Timer loeschen (Reset)
        self._cancel_debounce_timer(event_key)

        # Neuen Timer setzen → nach 200ms verarbeiten
        def _process():
            self._debounce_timers.pop(event_key, None)
            self._emit_event(vault_path, file_path, other_path, event_type)
            return False  # Nur einmal ausfuehren

        self._debounce_timers[event_key] = GLib.timeout_add(200, _process)

    def _cancel_debounce_timer(self, event_key):
        """Loescht einen bestehenden Debounce-Timer.

        Args:
            event_key: Schluessel des Timers
        """
        source = self._debounce_timers.pop(event_key, None)
        if source is not None:
            GLib.source_remove(source)

    def _cancel_all_debounce_timers(self):
        """Loescht alle Debounce-Timer."""
        for key in list(self._debounce_timers.keys()):
            self._cancel_debounce_timer(key)

    def _emit_event(self, vault_path, file_path, other_path, event_type):
        """Ermittelt das Signal und ruft alle verbundenen Callbacks auf.

        Args:
            vault_path: Pfad des Vault-Verzeichnisses
            file_path: Pfad der betroffenen Datei
            other_path: Zweiter Pfad bei MOVE-Events
            event_type: Event-Typ (created, deleted, moved, changed)
        """
        signal_name = self._SIGNAL_NAMES.get(event_type)
        if not signal_name:
            return

        if event_type == "changed" and file_path in self._skip_paths:
            self._skip_paths.discard(file_path)
            return

        for (sig, cb), callback in self._callbacks.items():
            if sig == signal_name:
                try:
                    if event_type == "moved" and other_path is not None:
                        callback(vault_path, file_path, other_path)
                    else:
                        callback(vault_path, file_path)
                except Exception:
                    pass
