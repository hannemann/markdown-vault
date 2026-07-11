# Markdown Vault

A GNOME desktop application for editing and previewing Markdown files organized in vault directories.

## Features

- **Three-panel layout** — vault file tree (left), editor/preview (center), sidebar (right, toggleable)
- **Multiple vaults** — work with several Markdown directories at once
- **View modes** — Edit, Render, or Split (side-by-side)
- **Tab system** — open multiple files simultaneously
- **Sidebar** — outline, backlinks, git status, file details
- **Git integration** — status indicators, diff, commit
- **Full-text search** — bottom bar across all vaults (Ctrl+F)
- **Tags & backlinks** — wikilink-style `[[page]]` navigation
- **Customizable keybindings** — GNOME defaults, optional vim/emacs modes

## Requirements

- Python 3.10+
- GTK 4
- libadwaita 1
- WebKitGTK 6.0
- GtkSourceView 5

### Install dependencies

<details>
<summary><strong>openSUSE Tumbleweed</strong></summary>

```sh
sudo zypper install \
  python3-gobject \
  python3-gobject-Gdk \
  gtk4-devel \
  gtk4-tools \
  libadwaita-devel \
  gtksourceview5-devel \
  webkitgtk4-devel \
  gobject-introspection-devel \
  python3-PyYAML \
  python3-markdown \
  meson \
  gcc
```
</details>

<details>
<summary><strong>Fedora</strong></summary>

```sh
sudo dnf install \
  python3-gobject \
  gtk4-devel \
  libadwaita-devel \
  gtksourceview5-devel \
  webkit2gtk6.0-devel \
  gobject-introspection-devel \
  python3-markdown \
  python3-pyyaml \
  meson \
  gcc
```
</details>

<details>
<summary><strong>Ubuntu / Debian</strong></summary>

```sh
sudo apt install \
  python3-gi \
  python3-gi-cairo \
  gir1.2-gtk-4.0 \
  libgtk-4-dev \
  libadwaita-1-dev \
  libwebkitgtk-6.0-dev \
  libgtksourceview-5-dev \
  libgirepository1.0-dev \
  python3-markdown \
  python3-yaml \
  meson \
  gcc
```
</details>

<details>
<summary><strong>Arch Linux</strong></summary>

```sh
sudo pacman -S \
  python \
  python-gobject \
  gtk4 \
  libadwaita \
  webkitgtk-6.0 \
  gtksourceview5 \
  python-markdown \
  python-pyyaml \
  gobject-introspection \
  meson \
  gcc
```
</details>

<details>
<summary><strong>Flatpak (any distribution)</strong></summary>

```sh
flatpak-builder --user --install --force-clean build-dir \
  de.hannemann.markdown-vault.yml
```
</details>

## Run from source

```sh
python3 -m src.main
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
