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
- **Live reload** — external file changes (edits, git pulls) are detected and the affected tab offers a reload
- **Interactive checkboxes** — toggle `- [ ]`/`- [x]` task items directly in the rendered preview
- **Zoom & session** — per-tab zoom (Ctrl+±/0, Ctrl+wheel); window, tabs, view modes and zoom are restored on restart
- **Math** — LaTeX `$...$`/`$$...$$` rendered as native MathML (no JavaScript/CDN)
- **Customizable keybindings** — GNOME defaults, optional vim/emacs modes
- **Rich Markdown (pymdown-extensions)** — strikethrough `~~text~~`, highlight `==text==`, superscript `^sup^`, subscript `~sub~`, task lists `- [ ]`/`- [x]`, superfences (tabs, line numbers, highlight lines), magic links (auto URLs, @mentions, #issues), keyboard keys `++ctrl+c++`, smart symbols (quotes, dashes, ellipsis), emoji shortcodes `:smile:`, math formulas `$...$`, task lists with checkboxes

## Installation

Runtime dependencies for running the application.

### openSUSE Tumbleweed

```sh
sudo zypper install \
  python313-gobject \
  python313-gobject-Gdk \
  python313-gobject-cairo \
  typelib-1_0-Gtk-4_0 \
  typelib-1_0-Adw-1 \
  typelib-1_0-GtkSource-5 \
  typelib-1_0-WebKit-6_0 \
  typelib-1_0-Pango-1_0 \
  python313-PyYAML \
  python313-Markdown \
  python313-pymdown-extensions \
  python313-Pygments
```

The `python313-*` names track the current default Python on Tumbleweed; adjust
the prefix if your interpreter differs. The `typelib-1_0-*` packages are the
GObject-introspection bindings the app imports at runtime (they pull in the
underlying GTK 4 / libadwaita / WebKitGTK 6.0 / GtkSourceView 5 libraries).

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
  python3-pygments
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
  python3-pygments
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
  gobject-introspection
```

### Semantic search (optional)

Semantic (vector) search is **opt-in** (Preferences → Search) and off by
default — the base app needs none of the packages below. It needs **numpy** for
the vector math, plus **one** embedding backend:

**Local ONNX backend (recommended)** — in-process, no server, nothing leaves
your machine, fast per query. Needs `onnxruntime` and the HuggingFace
`tokenizers`, plus a downloaded sentence-transformer ONNX model and its
`tokenizer.json`. No pip on the host.

- **openSUSE Tumbleweed:** `sudo zypper install python313-numpy python313-onnxruntime python313-tokenizers`
- **Fedora:** `python3-numpy` (onnxruntime/tokenizers from your repos if
  packaged; otherwise use the Ollama backend or the Flatpak, which bundles them)
- **Ubuntu / Debian:** `python3-numpy` (onnxruntime/tokenizers likewise)
- **Arch Linux:** `python-numpy python-onnxruntime` (`tokenizers` via AUR)

The model + tokenizer are a one-time **file download** (not a package), placed
in the app data dir and selected in Preferences — e.g. a multilingual MiniLM
ONNX export (`model.onnx` + `tokenizer.json`, ~90 MB). Exact paths are shown in
Preferences → Search once the ONNX backend is enabled.

**Ollama backend (alternative)** — external server, no Python packages beyond
`numpy`. Best if you already run Ollama (e.g. with a GPU). Run an Ollama server
and pull an embedding model:

```sh
ollama pull nomic-embed-text
```

Point the app at it in Preferences → Search (URL + model). Fully local when the
server runs on `localhost`; a remote server means embeddings are sent there.

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

## Install and run

Install the app and its launcher into your user prefix with Meson, then run it:

```sh
make install     # builds and installs to ~/.local (or: meson setup builddir && meson install -C builddir)
markdown-vault   # launcher installed to ~/.local/bin (make run does the same)
```

The launcher pins an interpreter that provides PyGObject. A `python3` earlier in
`PATH` (Homebrew, pyenv, a virtualenv) usually does not — the generated launcher
handles this for you.

> Running straight from the source tree (`PYTHONPATH=src python3 -m markdown_vault.main`)
> is **not supported** for normal use: it loads no CSS, because the stylesheets
> ship inside the installed package. That path is only for the test suite.

### Flatpak

```sh
make install-flatpak
make run-flatpak
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

## Contributing

Development dependencies and build instructions.

### Development dependencies

The build ships no C sources — it only installs the Python package and data
files. Beyond the runtime dependencies above you need **Meson**, a **C compiler**
(Meson probes for one because the project declares the C language), and
**gettext** (used by Meson's i18n module). No GUI `-devel` headers are required.

- **openSUSE Tumbleweed:** `meson`, `gcc`, `gettext-tools`
- **Fedora:** `meson`, `gcc`, `gettext`
- **Ubuntu / Debian:** `meson`, `gcc`, `gettext`
- **Arch Linux:** `meson`, `gcc`, `gettext`

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

- Publish to Flathub (local Flatpak build already works, see above)
- pip / PyPI distribution
