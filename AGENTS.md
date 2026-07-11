# AGENTS.md

## Project

Markdown Vault — a GNOME desktop app for editing and previewing Markdown files organized in vault directories.

- **App ID**: `de.hannemann.markdown-vault`
- **Language**: Python 3
- **UI toolkit**: GTK 4 + libadwaita
- **Markdown rendering**: HTML/CSS via WebKitGTK (WebView)
- **Config**: `~/.config/markdown-vault/vaults.yaml` (vaults + settings)
- **Session**: `~/.config/markdown-vault/session.json` (window geometry, tabs, view modes, split positions, sidebar, expanded_vaults, editor_zoom, preview_zoom)

## Tech decisions

- Use `gi.require_version("Gtk", "4.0")` and `gi.require_version("Adw", "1")` before importing.
- **GtkSourceView 5** for editor (`gi.require_version("GtkSource", "5")`).
- Markdown → HTML conversion uses Python `markdown` library.
- WebView is `WebKitGTK` via `gi.repository.WebKit`.
- Vault list stored in YAML (`vaults.yaml`), not dconf — simpler to debug and version.
- Images referenced in Markdown are resolved relative to the `.md` file's directory.
- **Flatpak** as primary distribution format (sandboxed file access via portal).

## Layout

- **Left panel**: vault tree — all vaults as expandable file trees (IDE-style project browser).
- **Center panel**: editor/preview/split with tabs.
  - **Edit** — GtkSourceView with syntax highlighting.
  - **Render** — WebKitGTK WebView with styled HTML.
  - **Split** — editor + preview side by side.
  - Default view is user-configurable.
- **Right sidebar** (toggleable via hamburger menu or shortcut):
  - Outline (headings of current file)
  - Backlinks / `[[wikilink]]` references
  - Git panel (status, diff, commit)
  - File details (metadata, word count, last modified)
- **Bottom bar**: full-text search across all vaults (Ctrl+F expands to vault-wide search).

## Features

- **Multiple vaults**: freely selectable directories; add/remove via UI.
- **Tabs**: open multiple files simultaneously in center panel. Each tab owns its own ``Editor`` + ``Preview`` instance.
- **Dark mode**: ``Adw.StyleManager`` with System / Light / Dark toggle in hamburger menu. WebView CSS uses ``@theme_*`` named colours for automatic adaptation.
- **Git integration**: status indicators in file tree, diff view, commit from app.
- **Tags/backlinks**: wikilink-style `[[page]]` parsing and backlink discovery.
- **Keybindings**: GNOME-style defaults, vim/emacs modes optional.
- **Markdown + images**: `![alt](path)` with relative and absolute path resolution.
- **Preferences dialog**: ``Adw.PreferencesDialog`` for autosave interval, default view mode, editor font size/tab width/wrap, preview zoom.
- **Zoom**: Ctrl+plus/minus/0 keyboard shortcuts; Ctrl+Wheel zoom on content area; per-tab zoom persisted in session.
- **Session persistence**: window size, sidebar, tabs (view modes + split positions), active tab, expanded vaults, editor/preview zoom.

## Project structure (planned)

```
src/
  main.py              — entry point, AdwApplication setup
  app_window.py        — main window, three-panel layout
  vault_tree.py        — left panel: file tree for vaults
  editor.py            — text editor widget (GtkSourceView 5)
  preview.py           — WebView-based Markdown renderer
  tabs.py              — tab management for open files
  sidebar.py           — right sidebar (outline, backlinks, git, details)
  search.py            — bottom bar: full-text search across vaults
  git_integration.py   — git status, diff, commit
  tags.py              — [[wikilink]] parsing, backlinks
  config.py            — vaults.yaml reader/writer + settings
  session.py           — session persistence (JSON)
  preferences.py       — Adw.PreferencesDialog
data/
  de.hannemann.markdown-vault.desktop
  de.hannemann.markdown-vault.metainfo.xml
  de.hannemann.markdown-vault.gresource.xml
  icons/
  css/
    style.css          — WebView styling for rendered Markdown
  de.hannemann.markdown-vault.yml  — Flatpak manifest
tests/
  test_config.py
  test_tags.py
  test_search.py
  test_session.py
  test_preferences.py
  test_editor.py
  test_preview.py
  test_git_integration.py
  test_tabs.py
meson.build            — build system
```

## Dev commands

```bash
# Run from source (no install needed)
python3 -m src.main

# Run detached for manual testing (survives bash tool timeout)
setsid python3 -m src.main >tmp/mv-stdout.log 2>tmp/mv-stderr.log &
disown
# Check logs: tail -f tmp/mv-stderr.log
# Kill later: pkill -f "src.main"

# IMPORTANT: Before starting a new instance for testing, ALWAYS kill
# all existing instances first to avoid duplicate windows:
# pkill -f "python3 -m src.main" || true

# Run detached for manual testing (survives bash tool timeout)
setsid python3 -m src.main >tmp/mv-stdout.log 2>tmp/mv-stderr.log &
disown
# Check logs: tail -f tmp/mv-stderr.log
# Kill later: pkill -f "src.main"

# Install dependencies (openSUSE Tumbleweed)
sudo zypper install python3-gobject python3-gobject-Gdk gtk4-devel gtk4-tools \
  libadwaita-devel gtksourceview5-devel webkitgtk4-devel \
  gobject-introspection-devel python3-PyYAML python3-markdown meson gcc

# Install dependencies (Fedora)
sudo dnf install python3-gobject gtk4-devel libadwaita-devel gtksourceview5-devel \
  webkit2gtk6.0-devel gobject-introspection-devel python3-markdown \
  python3-pyyaml meson gcc

# Install dependencies (Ubuntu/Debian)
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0 libgtk-4-dev \
  libadwaita-1-dev libwebkitgtk-6.0-dev libgtksourceview-5-dev \
  libgirepository1.0-dev python3-markdown python3-yaml meson gcc

# Install dependencies (Arch)
sudo pacman -S python python-gobject gtk4 libadwaita webkitgtk-6.0 \
  gtksourceview5 python-markdown python-pyyaml gobject-introspection meson gcc

# Build with meson
meson setup builddir
meson compile -C builddir
meson install -C builddir

# Flatpak build
flatpak-builder --user --install --force-clean build-dir de.hannemann.markdown-vault.yml

# Run tests
python3 -m unittest discover -s tests -v
```

## Conventions

- Follow PEP 8, max line length 100.
- Use `snake_case` for functions/variables, `PascalCase` for classes.
- All user-facing strings must be translatable via `gettext`.
- CSS for WebView rendering goes in `data/css/`, not inline in Python.
- Vault config YAML keys are case-sensitive, paths are absolute.
- Git features must gracefully handle repos without git initialized.
- Images in Markdown: support `![alt](path)` with both relative and absolute paths.

## Gotchas

- WebKitGTK requires the main thread for JS evaluation — use `GLib.idle_add()` for WebView calls.
- GtkSourceView needs `gi.require_version("GtkSource", "5")` — version 4 is for GTK3.
- `vaults.yaml` must never contain duplicate vault paths; deduplicate on load.
- On Flatpak, file access is sandboxed — use `org.freedesktop.portal` for file chooser.
- GtkSourceView 5 renamed `begin_not_undoable_action` → `begin_irreversible_action`.
- `editor.file_path` is a `str`, not `Path` — use `Path(editor.file_path).parent` for directory.
- Kill all existing app instances before starting a new one (`pkill -f "python3 -m src.main" || true`). Duplicate instances cause confusing state.
