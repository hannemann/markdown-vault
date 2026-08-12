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

System dependencies for running the application (the GObject/GTK stack). The
app's pure-Python packages (Markdown, PyYAML, pymdown-extensions, Pygments) are
**not** listed here — `make install` installs them from `requirements.txt` into a
private venv (see [Install and run](#install-and-run)).

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
  typelib-1_0-Pango-1_0
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
  gobject-introspection
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
  python3-venv
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
  gobject-introspection
```

### Semantic search (optional)

Semantic (vector) search is **opt-in** (Preferences → Search) and off by
default — the base app needs none of the packages below. It needs **numpy** for
the vector math, plus **one** embedding backend:

**Local ONNX backend (recommended)** — in-process, no server, nothing leaves
your machine, fast per query. Needs `numpy`, `onnxruntime` and the HuggingFace
`tokenizers` (installed into the venv by `make install-ai`), plus a downloaded
sentence-transformer ONNX model and its `tokenizer.json`.

Install the optional dependencies into the app's venv:

```sh
make install-ai   # base install + numpy, onnxruntime, tokenizers (requirements-ai.txt)
```

These are prebuilt PyPI wheels on the common platforms (Linux x86_64 / aarch64,
glibc). On musl/Alpine, 32-bit or exotic architectures a wheel may be missing —
use the Ollama backend below instead.

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

### Local answers (Ask, optional)

The quick-open **Ask** mode answers from your notes with a local chat model,
in-process via `llama-cpp-python` (no server) — installed by the same
`make install-ai`. The GGUF model is a one-time download from Preferences →
Search → Ask. The default engine is **Automatic**: it sets up the backend, a
safe thread count and GPU offload when available, so only the model download
needs a click. `make install-ai` fetches a **CPU** build by default — it works
everywhere and needs no build tools.

#### GPU acceleration (Vulkan) — optional, not officially supported

GPU offload via Vulkan is **implemented but not officially supported** (best
effort — it depends on your driver stack). If the build toolchain is present
when you run `make install-ai`, it builds `llama-cpp-python` **with Vulkan
automatically**; otherwise it installs the CPU build and tells you so. Install
the toolchain first, then run the install (openSUSE Tumbleweed shown):

```sh
sudo zypper install vulkan-devel shaderc glslang-devel cmake gcc-c++
make install-ai   # detects the toolchain and builds with Vulkan
```

Equivalents: Fedora `vulkan-headers vulkan-loader-devel glslang gcc-c++ cmake`;
Debian/Ubuntu `libvulkan-dev glslc cmake g++`. Once a Vulkan build is installed,
the **GPU layers** control appears in Preferences → Search → Ask; set it above 0
(e.g. 999) to offload the model.

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
make install     # builds, creates the venv, and installs to ~/.local
markdown-vault   # launcher installed to ~/.local/bin (make run does the same)
```

`make install` also creates a private venv at `~/.local/share/markdown-vault/venv`
(with `--system-site-packages`, so the system PyGObject/GTK stay visible) and
installs the Python packages from `requirements.txt` into it; the generated
launcher runs the app through that venv interpreter. The first install needs
network access to fetch the packages from PyPI. (Bare `meson install` skips the
venv step — use `make install`.)

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
