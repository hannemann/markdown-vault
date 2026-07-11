# AGENTS.md

## Project

Markdown Vault — a GNOME desktop app for editing and previewing Markdown files organized in vault directories.

- **App ID**: `de.hannemann.markdown-vault`
- **Language**: Python 3
- **UI toolkit**: GTK 4 + libadwaita
- **Markdown rendering**: HTML/CSS via WebKitGTK (WebView)
- **Config**: `~/.config/markdown-vault/vaults.yaml`

## Tech decisions

- Use `gi.require_version("Gtk", "4.0")` and `gi.require_version("Adw", "1")` before importing.
- Markdown → HTML conversion uses Python `markdown` library (or `mistune`).
- WebView is `WebKitGTK` via `gi.repository.WebKit`.
- Vault list stored in YAML (`vaults.yaml`), not dconf — simpler to debug and version.
- Images referenced in Markdown are resolved relative to the `.md` file's directory.

## Features

- **Three-panel layout**: vault tree (left) | editor/preview/split (center) | optional sidebar
- **View modes**: Edit, Render, Split (default configurable in settings)
- **Multiple vaults**: add/remove directories; tree view shows `.md` files and images
- **Tabs**: open multiple files simultaneously
- **Git integration**: status indicators, diff view, commit from within the app
- **Full-text search**: search across all open vaults
- **Tags/backlinks**: wikilink-style `[[page]]` support
- **Keybindings**: configurable (default GNOME-style, vim/emacs modes optional)

## Project structure (planned)

```
src/
  main.py              — entry point, AdwApplication setup
  app_window.py        — main window, three-panel layout
  vault_tree.py        — left panel: file tree for vaults
  editor.py            — text editor widget (GtkSourceView)
  preview.py           — WebView-based Markdown renderer
  tabs.py              — tab management for open files
  search.py            — full-text search across vaults
  git_integration.py   — git status, diff, commit
  tags.py              — [[wikilink]] parsing, backlinks
  config.py            — vaults.yaml reader/writer
data/
  de.hannemann.markdown-vault.desktop
  de.hannemann.markdown-vault.metainfo.xml
  de.hannemann.markdown-vault.gresource.xml
  icons/
  css/
    style.css          — WebView styling for rendered Markdown
tests/
  test_config.py
  test_tags.py
  test_search.py
meson.build            — build system
```

## Dev commands

```bash
# Run from source (no install needed)
python -m src.main

# Install dependencies (Fedora)
sudo dnf install gtk4 libadwaita webkitgtk6.0 python3-gobject python3-markdown python3-pyyaml

# Install dependencies (Ubuntu/Debian)
sudo apt install libgtk-4-dev libadwaita-1-dev libwebkitgtk-6.0-dev python3-gi python3-markdown python3-yaml

# Build with meson
meson setup builddir
meson compile -C builddir
meson install -C builddir

# Flatpak build
flatpak-builder --user --install --force-clean build-dir de.hannemann.markdown-vault.yml

# Run tests
python -m pytest tests/
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
