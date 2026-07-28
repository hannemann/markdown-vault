#!/bin/sh
# Single entry point for starting/stopping the app in agent/dev workflows.
#
# Usage: scripts/app.sh {start|stop|restart|status}
#
# The app is a GTK GUI that never exits on its own. Starting it in the
# foreground would block the caller forever, so `start` fully detaches the
# process (no controlling TTY, stdout/stderr to a log). Every subcommand
# returns immediately and exits 0 on success, so callers do not hang and do
# not mistake "nothing to kill" for a failure.
set -u

PATTERN="markdown_vault.main"
BIN="$HOME/.local/bin/markdown-vault"
LOG="/tmp/markdown-vault.log"

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
    setsid "$BIN" >"$LOG" 2>&1 </dev/null &   # fully detached: no TTY, no blocking
    sleep 2
    if pgrep -f "$PATTERN" >/dev/null; then
        echo "started (log: $LOG)"
    else
        echo "FAILED to start; last log lines:" >&2
        tail -n 5 "$LOG" >&2 2>/dev/null || true
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
