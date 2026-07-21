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
- **Rich Markdown (pymdown-extensions)** — strikethrough `~~text~~`, highlight `==text==`, superscript `^sup^`, subscript `~sub~`, task lists `- [ ]`/`- [x]`, superfences (tabs, line numbers, highlight lines), magic links (auto URLs, @mentions, #issues), keyboard keys `++ctrl+c++`, smart symbols (quotes, dashes, ellipsis), emoji shortcodes `:smile:`, math formulas `$...$`, task lists with checkboxes

## Installation

Runtime dependencies for running the application.

### openSUSE Tumbleweed

```sh
sudo zypper install \
  python3-gobject \
  python3-gobject-Gdk \
  gtk4 \
  gtk4-tools \
  libadwaita-1-0 \
  libgtksourceview-5-0 \
  libwebkit2gtk4.1-0 \
  libgirepository-1.0-1 \
  python3-PyYAML \
  python3-markdown \
  python313-pymdown-extensions \
  python313-Pygments \
  python313-setproctitle
```

### Fedora

```sh
sudo dnf install \
  python3-gobject \
  gtk4 \
  libadwaita-1 \
  gtksourceview5 \
  webkit2gtk6.0 \
  gobject-introspection \
  python3-markdown \
  python3-pyyaml \
  python3-pymdown-extensions \
  python3-pygments \
  python3-setproctitle
```

### Ubuntu / Debian

```sh
sudo apt install \
  python3-gi \
  python3-gi-cairo \
  gir1.2-gtk-4.0 \
  gir1.2-adw-1 \
  gir1.2-webkit-6.0 \
  gir1.2-gtksource-5 \
  python3-markdown \
  python3-yaml \
  python3-pymdownx \
  python3-pygments \
  python3-setproctitle
```

### Arch Linux

```sh
sudo pacman -S \
  python \
  python-gobject \
  gtk4 \
  libadwaita \
  webkitgtk-6.0 \
  gtksourceview5 \
  python-markdown \
  python-yaml \
  python-pymdown-extensions \
  python-pygments \
  python-setproctitle \
  gobject-introspection
```

### Ubuntu 24.04: AppArmor profile

Ubuntu 24.04 restricts unprivileged user namespaces
(`kernel.apparmor_restrict_unprivileged_userns=1`). WebKitGTK needs one for the
`bwrap` sandbox it always sets up, so without a profile the application aborts
as soon as a Markdown preview is created:

```
bwrap: setting up uid map: Permission denied
ERROR: Failed to fully launch dbus-proxy: Child process exited with code 1
```

Install the shipped profile, which grants the launcher `userns` and nothing else:

```sh
sudo install -m 644 packaging/apparmor/markdown-vault /etc/apparmor.d/markdown-vault
sudo apparmor_parser -r --skip-cache /etc/apparmor.d/markdown-vault
```

It attaches to `~/.local/bin/markdown-vault`. For a system-wide install, change
the path in the profile to `/usr/bin/markdown-vault`.

## Run from source

```sh
PYTHONPATH=src/lib/python3.13/site-packages python3 -m markdown_vault.main
```

Use an interpreter that provides PyGObject. A `python3` earlier in `PATH`
(Homebrew, pyenv, a virtualenv) usually does not, in which case call
`/usr/bin/python3` explicitly.

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

## Contributing

Development dependencies and build instructions.

### Development dependencies

In addition to the runtime dependencies above, you need:

**openSUSE Tumbleweed:**
- `gtk4-devel`, `libadwaita-devel`, `gtksourceview5-devel`, `webkitgtk4-devel`, `gobject-introspection-devel`, `meson`, `gcc`

**Fedora:**
- `gtk4-devel`, `libadwaita-devel`, `gtksourceview5-devel`, `webkit2gtk6.0-devel`, `gobject-introspection-devel`, `meson`, `gcc`

**Ubuntu / Debian:**
- `libgtk-4-dev`, `libadwaita-1-dev`, `libwebkitgtk-6.0-dev`, `libgtksourceview-5-dev`, `libgirepository1.0-dev`, `meson`, `gcc`

**Arch Linux:**
- `meson`, `gcc`

### Build

```sh
meson setup builddir
meson compile -C builddir
meson install -C builddir
```

### Tests

```sh
make test
```

### Code guidelines

See `AGENTS.md` for project conventions, TDD requirements, and gotchas.

## TODO

- Flatpak packaging
- pip module distribution
- AppStream metadata
