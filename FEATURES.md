# Feature Ideas — Markdown Vault

## Second Brain / Knowledge Management (MVP)

### Priority 1 — Core Daily Driver
- **Daily Notes** — Shortcut `Ctrl+D` öffnet/erstellt heutige Notiz (`YYYY-MM-DD.md`), Vorlage konfigurierbar
- **Templates** — Shortcut `Ctrl+T` wählt Template (Meeting, Project, Literature, Daily), Variablen `{{date}}`, `{{time}}`, `{{title}}`
- **Frontmatter / Properties** — `--- yaml ---` Block oben in Notiz, UI zum Editieren (Sidebar "Properties"), unterstützt `tags`, `alias`, `status`, `date`, custom fields

### Priority 2 — Structured Retrieval
- **Saved Searches / Smart Folders** — `LIST FROM #project WHERE status = "active"` Syntax, Ergebnisse als virtuelle Ordner in Vault-Tree, Live-Updates
- **Dataview-Style Queries** — `TABLE file.mtime, status FROM #meeting WHERE date > 2024-01-01` → Tabelle in Sidebar/Tab
- **Tag/Property Panel** — Filterbare Liste aller Tags/Properties mit Count, Klick = Smart Folder

### Priority 3 — Thinking Tools
- **Outliner** — Einklappbare Bullets (`- `), `Tab`/`Shift+Tab` ein-/ausrücken, `Enter` neuer Bullet, `Ctrl+Enter` Toggle Done, Drag-Reorder
- **Local Graph** — 2-Hops um aktuelle Notiz (nicht global), interaktiv, zeigt Backlinks + Outlinks

## UX / Power User
- **Vim Mode** (GtkSourceView `vim` keymap) + Emacs Mode — Umschaltbar in Preferences
- **Command Palette** (`Ctrl+P`) — Fuzzy-Suche über Commands, Files, Tags, Actions
- **Quick Switcher** — `Ctrl+O` fuzzy über alle Notizen + Aliase, kürzlich genutzt oben
- **Publish → HTML/PDF/EPUB** — Einzelne Notiz oder ganzer Vault, Template-basiert

## Platform / Reliability
- **Vault Directory Watching (inotify)** — `Gio.FileMonitor` für externe Änderungen (create/delete/rename/modify), Auto-Reload, Tab-Sync
- **Git Integration Deepening** — Diff-View inline, Stage/Unstage per File, Commit-Message-Templates, Blame-Ansicht
- **Plugin System** — Python-basiert, Hooks: `on_save`, `on_open`, `on_search`, `render_markdown`

## Infrastructure
- **Integration Tests** — pytest + Xvfb (Widget-API: Tabs, Editor↔Preview, Split, Vault-Tree, Session)
- **E2E Tests** — pytest + dogtail/pyatspi (AT-SPI), User-Flows: New File, Open Vault, Preferences, Zoom
- **CI** — GitHub Actions / GitLab CI mit `xvfb-run` + `libatspi2.0-0`

---

> **Nicht priorisiert:** Global Graph View (Wollknäuel), Canvas/Whiteboard (separates Tool), Mobile App (später).