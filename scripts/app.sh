#!/bin/sh
# Single entry point for starting/stopping the app in agent/dev workflows.
#
# Usage: scripts/app.sh {start|stop|restart|status}
#
# The app is a GTK GUI that never exits on its own. Starting it in the
# foreground would block the caller forever, so `start` fully detaches the
# process (no controlling TTY). Logging is handled entirely by the app itself
# (log files under ~/.local/state/markdown-vault/ + fd redirect on headless
# launches), so app.sh does not need to do anything for logging. Every
# subcommand returns immediately and exits 0 on success, so callers do not
# hang and do not mistake "nothing to kill" for a failure.
set -u

PATTERN="markdown_vault.main"
BIN="$HOME/.local/bin/markdown-vault"
STDERR_LOG="$HOME/.local/state/markdown-vault/markdown-vault.stderr.log"

stop() {
    pkill -f "$PATTERN" 2>/dev/null || true
    sleep 1
    pkill -9 -f "$PATTERN" 2>/dev/null || true
}

start() {
    if [ ! -x "$BIN" ]; then
        echo "FAILED: $BIN not found — run 'make install' first." >&2
        exit 1
    fi
    # Detached, no blocking. /dev/null only guards against the caller's pipes
    # being inherited; the app redirects fd 1 / fd 2 to its rotated log files
    # itself on headless launches.
    # Dev launch only: expose the D-Bus debug/automation interface
    # (debug_control.py) so searches, file open/close and state reads can be driven
    # over the bus. This script is the DEVELOPMENT launcher; the shipped .desktop
    # entry never sets this, so the interface does not exist in normal use.
    # While enabled it lives on the session bus and any session peer can call it
    # (Search+SearchResults is a content oracle over the vault) — acceptable for a
    # deliberate dev session; do not enable it on the .desktop launch.
    MDV_DEBUG_CONTROL=1 setsid "$BIN" >/dev/null 2>&1 </dev/null &
    sleep 2
    if pgrep -f "$PATTERN" >/dev/null; then
        echo "started"
    else
        echo "FAILED to start; last stderr log lines:" >&2
        tail -n 5 "$STDERR_LOG" >&2 2>/dev/null || true
        exit 1
    fi
}

case "${1:-}" in
    start)   start ;;
    stop)    stop; echo "stopped" ;;
    restart) stop; start ;;
    status)  pgrep -f "$PATTERN" >/dev/null && echo "running" || echo "not running" ;;
    *)       echo "usage: $0 {start|stop|restart|status}" >&2; exit 2 ;;
esac
