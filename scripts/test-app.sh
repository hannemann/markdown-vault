#!/bin/bash
set -euo pipefail

LOG_DIR="tmp"
LOG_FILE="$LOG_DIR/mv-stderr.log"
LOG_STDOUT="$LOG_DIR/mv-stdout.log"
PID_FILE="$LOG_DIR/markdown-vault.pid"

log() { echo "[$(date +%H:%M:%S)] $*"; }

# 1. PRÜFEN & KILLEN via PID-File
if [[ -f "$PID_FILE" ]]; then
    STORED_PID=$(cat "$PID_FILE" 2>/dev/null || echo "")
    if [[ -n "$STORED_PID" ]] && kill -0 "$STORED_PID" 2>/dev/null; then
        log "Beende existierende Instanz (PID: $STORED_PID)..."
        kill "$STORED_PID" 2>/dev/null
        sleep 1
        if kill -0 "$STORED_PID" 2>/dev/null; then
            log "Force kill (PID: $STORED_PID)..."
            kill -9 "$STORED_PID" 2>/dev/null
            sleep 0.5
        fi
        if kill -0 "$STORED_PID" 2>/dev/null; then
            echo "FEHLER: Konnte Prozess $STORED_PID nicht beenden" >&2
            exit 1
        fi
    else
        log "PID-File existiert aber Prozess nicht mehr (verwaist)."
    fi
else
    # FALLBACK: Pattern-basiert falls PID-File fehlt/kaputt
    log "Kein PID-File → Pattern-Prüfung..."
    if pgrep -f "src.main" >/dev/null; then
        log "Prozess gefunden → Pattern-Kill..."
        pkill -f "python3 -m src.main" 2>/dev/null
        sleep 1
        if pgrep -f "src.main" >/dev/null; then
            pkill -9 -f "src.main" 2>/dev/null
            sleep 0.5
        fi
    fi
fi

# 2. STARTEN & PID SPEICHERN
mkdir -p "$LOG_DIR"
setsid python3 -m src.main >"$LOG_DIR/mv-stdout.log" 2>"$LOG_DIR/mv-stderr.log" &
APP_PID=$!
echo "$APP_PID" >"$PID_FILE"
disown

log "App gestartet (PID: $APP_PID, PID-File: $PID_FILE)"

# 3. VALIDIEREN
sleep 2
if grep -q "main window presented" "$LOG_DIR/mv-stderr.log" 2>/dev/null; then
    log "✓ App gestartet (PID: $(cat "$PID_FILE" 2>/dev/null || pgrep -f 'src.main'))"
    exit 0
else
    log "✗ FEHLER: 'main window presented' nicht in Logs"
    tail -10 "$LOG_DIR/mv-stderr.log" 2>/dev/null || true
    exit 1
fi