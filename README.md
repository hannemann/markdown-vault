# Markdown Vault

A GNOME desktop application for editing and previewing Markdown files organized in vault directories.

## Features

- **Three-panel layout** — vault file tree, editor, and live preview
- **Multiple vaults** — work with several Markdown directories at once
- **View modes** — Edit, Render, or Split (side-by-side)
- **Tab system** — open multiple files simultaneously
- **Git integration** — status indicators, diff, commit
- **Full-text search** — search across all open vaults
- **Tags & backlinks** — wikilink-style `[[page]]` navigation
- **Customizable keybindings** — GNOME defaults, optional vim/emacs modes

## Requirements

- Python 3.10+
- GTK 4
- libadwaita 1
- WebKitGTK 6.0
- GtkSourceView 5

### Install dependencies

Fedora:

```sh
sudo dnf install gtk4 libadwaita webkitgtk6.0 gtksourceview5 python3-gobject python3-markdown python3-pyyaml python3-pygit2
```

Ubuntu/Debian:

```sh
sudo apt install libgtk-4-dev libadwaita-1-dev libwebkitgtk-6.0-dev libgtksourceview-5-dev python3-gi python3-markdown python3-yaml python3-pygit2
```

## Run from source

```sh
python -m src.main
```

## Build & Install

```sh
meson setup builddir
meson compile -C builddir
meson install -C builddir
```

## Vault Configuration

Vaults are stored in `~/.config/markdown-vault/vaults.yaml`:

```yaml
vaults:
  - name: "Notes"
    path: "/home/user/Documents/Notes"
  - name: "Work"
    path: "/home/user/Work/docs"
```

## License

GPL-3.0-or-later
