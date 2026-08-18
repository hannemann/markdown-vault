#!/bin/sh
# Single entry point for starting/stopping the app in agent/dev workflows.
#
# Usage: scripts/app.sh {start|stop|restart|status}
#
# The app is a GTK GUI that never exits on its own. Starting it in the
# foreground would block the caller forever, so `start` fully detaches the
# process (no controlling TTY). Logging is handled entirely by the app itself
# (log files under ~/.local/state/de.hannemann.markdown-vault/ + fd redirect on headless
# launches), so app.sh does not need to do anything for logging. Every
# subcommand returns immediately and exits 0 on success, so callers do not
# hang and do not mistake "nothing to kill" for a failure.
set -u

PATTERN="markdown_vault.main"
BIN="$HOME/.local/bin/markdown-vault"
STDERR_LOG="$HOME/.local/state/de.hannemann.markdown-vault/markdown-vault.stderr.log"

stop() {
    pgrep -f "$PATTERN" >/dev/null 2>&1 || return 0    # nothing to stop
    # SIGTERM lets the app shut down cleanly — its handler closes the window,
    # which saves the session. Force-killing races that save (and can persist a
    # half-written or stale session), so only SIGKILL a process still alive after
    # a 5s grace period.
    pkill -TERM -f "$PATTERN" 2>/dev/null || true
    n=0
    while pgrep -f "$PATTERN" >/dev/null 2>&1; do
        n=$((n + 1))
        [ "$n" -ge 25 ] && break                       # 25 * 0.2s = 5s
        sleep 0.2
    done
    if pgrep -f "$PATTERN" >/dev/null 2>&1; then
        echo "process still alive after 5s — forcing SIGKILL" >&2
        pkill -KILL -f "$PATTERN" 2>/dev/null || true
    fi
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
    # While enabled it lives on the session bus and any session peer can call it:
    # Search+SearchResults probes note contents, and QuickOpen+Submit+AskAnswer
    # reads note content back out (and spends CPU/GPU) — acceptable for a
    # deliberate dev session; do not enable it on the .desktop launch.
    # A just-stopped instance can hold the session D-Bus name for a moment after
    # its process is gone; a fresh launch then fails to register ("Failed to
    # register: GDBus … Remote peer disconnected") and exits within the 2s window,
    # so pgrep finds nothing. Retry once after a short settle before giving up —
    # the reached retry means no process is alive, so this cannot duplicate one.
    attempt=0
    while :; do
        attempt=$((attempt + 1))
        MDV_DEBUG_CONTROL=1 setsid "$BIN" >/dev/null 2>&1 </dev/null &
        sleep 2
        if pgrep -f "$PATTERN" >/dev/null; then
            echo "started"
            return 0
        fi
        [ "$attempt" -ge 2 ] && break
        sleep 1
    done
    echo "FAILED to start after $attempt attempts; last stderr log lines:" >&2
    tail -n 5 "$STDERR_LOG" >&2 2>/dev/null || true
    exit 1
}

case "${1:-}" in
    start)   start ;;
    stop)    stop; echo "stopped" ;;
    restart) stop; start ;;
    status)  pgrep -f "$PATTERN" >/dev/null && echo "running" || echo "not running" ;;
    *)       echo "usage: $0 {start|stop|restart|status}" >&2; exit 2 ;;
esac
