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
- **Math rendering**: `latex2mathml` converts LaTeX → MathML, WebKitGTK renders MathML natively. No JavaScript/CDN.
- WebView is `WebKitGTK` via `gi.repository.WebKit`.
- Vault list stored in YAML (`vaults.yaml`), not dconf — simpler to debug and version.
- Images referenced in Markdown are resolved relative to the `.md` file's directory.
- **Flatpak** as primary distribution format (sandboxed file access via portal).
- **Dependencies**: Before adding a new Python dependency, ALWAYS ask the user first. Never add dependencies without confirmation.
- **NEVER install packages**: You MUST NEVER run `pip install`, `zypper install`, `dnf install`, `apt install`, `pacman -S`, or any other package installation command. ONLY the user installs packages on this system. This is a non-negotiable rule. If a package is missing, tell the user what to install — do NOT install it yourself.

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
- **Rich Markdown (pymdown-extensions)**: strikethrough `~~text~~`, highlight `==text==`, superscript `^sup^`, subscript `~sub~`, task lists `- [ ]`, tasklist `- [x]`, superfences (tabs, line numbers, highlight lines), magic links (auto URLs, @mentions, #issues), keyboard keys `++ctrl+c++`, smart symbols (quotes, dashes, ellipsis), emoji shortcodes `:smile:`, math formulas `$...$`, task lists with checkboxes.
- **CLI launcher**: `bin/markdown-vault` with shebang; `setproctitle` for correct process name (for `ps`/`killall`).

## Project structure (planned)

```
src/
  bin/markdown-vault.in       — launcher template; Meson substitutes the interpreter
                                and PYTHONPATH, then installs it as bin/markdown-vault
  lib/python3.13/site-packages/markdown_vault/
    __init__.py               — package marker
    __main__.py               — entry point (python3 -m markdown_vault)
    main.py                   — AdwApplication setup
    app_window.py             — main window, three-panel layout
    vault_tree.py             — left panel: file tree for vaults
    vault_monitor.py          — Gio.FileMonitor wrapper for external change detection
    editor.py                 — text editor widget (GtkSourceView 5)
    preview.py                — WebView-based Markdown renderer
    tabs.py                   — tab management for open files
    sidebar.py                — right sidebar (outline, backlinks, git, details)
    search.py                 — bottom bar: full-text search across vaults
    search_logic.py           — search worker (runs in daemon thread)
    git_integration.py        — git status, diff, commit
    tags.py                   — [[wikilink]] parsing, backlinks
    backlink_index.py         — O(1) backlink lookup, built on startup
    config.py                 — vaults.yaml reader/writer + settings
    session.py                — session persistence (JSON)
    preferences.py            — Adw.PreferencesDialog
    mru.py                    — MRU tab switcher (Ctrl+Tab)
    history.py                — navigation history (back/forward)
    path_utils.py             — vault path resolution helpers
    validation.py             — input validation utilities
    latex_mathml.py           — LaTeX → MathML converter (no JS/CDN)
    markdown_help.py          — keyboard shortcuts overlay
    css/
      style.css               — WebView styling for rendered Markdown
      gtk.css                 — GTK CSS for tab bar and widgets
    meson.build               — Python package build rules
  share/markdown-vault/
    css/style.css             — CSS (copy for Flatpak resource bundle)
    css/gtk.css
    icons/hicolor/            — icon theme
    de.hannemann.markdown-vault.desktop
    de.hannemann.markdown-vault.metainfo.xml
    de.hannemann.markdown-vault.gresource.xml
    de.hannemann.markdown-vault.yml  — Flatpak manifest
    meson.build               — Data files build rules
meson.build            — top-level build system
tests/                   — unit tests (unittest)
```

**Installation paths:**
- **Binaries:** `~/.local/bin/` (user) or `/usr/bin/` (system)
- **Python code:** `<datadir>/markdown-vault/python/markdown_vault/` — a private directory, not the
  interpreter's `site-packages`, which may sit outside the install prefix. The generated launcher puts
  it on `PYTHONPATH`.
- **Data files:** `~/.local/share/markdown-vault/` or `/usr/share/markdown-vault/`
- **Config:** `~/.config/markdown-vault/` (identical for all installations)
- **State/Logs:** `~/.local/state/markdown-vault/` (identical for all installations)

## Dev commands

```bash
# Start app (as user would do it)
gtk-launch de.hannemann.markdown-vault

# Stop app (clean, like X click — session is saved)
killall markdown-vault

# If app hangs and does not respond to SIGTERM:
killall -9 markdown-vault

# Run local tests (from project root)
# Note: use an interpreter that has PyGObject — a python3 earlier in PATH
# (e.g. Homebrew) usually does not.
PYTHONPATH=src/lib/python3.13/site-packages /usr/bin/python3 -m unittest discover -s tests -v

# Or with make
make test
```

```bash
# DO NOT use killall python3 — that also kills firewalld and other system Python processes!

# Install dependencies (openSUSE Tumbleweed)
sudo zypper install python3-gobject python3-gobject-Gdk gtk4-devel gtk4-tools \
  libadwaita-devel gtksourceview5-devel webkitgtk4-devel \
  gobject-introspection-devel python3-PyYAML python3-markdown \
  python313-setproctitle meson gcc

# Install dependencies (Fedora)
sudo dnf install python3-gobject gtk4-devel libadwaita-devel gtksourceview5-devel \
  webkit2gtk6.0-devel gobject-introspection-devel python3-markdown \
  python3-pyyaml python3-setproctitle meson gcc

# Install dependencies (Ubuntu/Debian)
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0 libgtk-4-dev \
  libadwaita-1-dev libwebkitgtk-6.0-dev libgtksourceview-5-dev \
  libgirepository1.0-dev python3-markdown python3-yaml \
  python3-setproctitle meson gcc

# Install dependencies (Arch)
sudo pacman -S python python-gobject gtk4 libadwaita webkitgtk-6.0 \
  gtksourceview5 python-markdown python-pyyaml python-setproctitle \
  gobject-introspection meson gcc

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
- **Test-driven development**: Always write failing tests first, then implement the fix. Run tests to verify they fail, then implement the minimal code to make them pass. Never commit code without corresponding tests.
- **New Python modules**: When creating a new `.py` file in `src/lib/python3.13/site-packages/markdown_vault/`, it MUST be added to the `py_sources` list in `src/lib/python3.13/site-packages/markdown_vault/meson.build` (alphabetically sorted). Meson has no built-in `glob()` — the list is manually maintained. Forgetting to add it means the file will not be installed and the app will crash with `ModuleNotFoundError`.
- **GTK CSS in `css/gtk.css`**: Target GTK 4.14 / libadwaita 1.5. `var(--name)` and `color-mix()` need GTK 4.16+ and are silently dropped with "Expected a valid color" parser warnings. Use `@accent_bg_color` and `alpha(@color, 0.3)` instead. This does not apply to `css/style.css`, which is rendered by WebKit.
- **WebKit needs an unprivileged user namespace**: WebKitGTK 2.46+ always sets up a `bwrap` sandbox and aborts the whole process if it cannot (`Failed to fully launch dbus-proxy`). On Ubuntu 24.04 this requires the AppArmor profile in `packaging/apparmor/` — see README. There is no API or env var to disable the sandbox.
- **Test organization**: Add tests to existing test files grouped by topic (e.g. vault_monitor events → `test_vault_monitor_events.py`). Do not create new test files with arbitrary context names — distribute into the files that already cover the module under test. When in doubt, ask.
- **Error handling**: Never use bare `except Exception: pass` — always log the exception at a minimum. Use `logging.warning()` or `logging.error()` with exc_info=True so errors are visible and debuggable.
- **Logging**: Every module MUST use the standard `logging` module. Add `import logging` and `logger = logging.getLogger(__name__)` at the top of each file. Use `logger.debug()`/`logger.info()`/`logger.warning()`/`logger.error()` — NEVER use `print()` or any other ad-hoc output for diagnostics. Every `except` block must log at minimum with `exc_info=True`. Log level is configurable via `settings.loglevel` (debug/info/warning/error), effective after restart.
- **Temp files**: NEVER use the system `/tmp` directory. Use the local `./tmp/` directory instead. The system `/tmp` is shared, unpredictable, and cleaned up by the OS. Local `./tmp/` is project-scoped and ignored by `.gitignore`, so it stays fully under your control.
- **NEVER commit without explicit user request**: NEVER run `git commit` unless the user explicitly asks for it. Not after editing files, not after testing, not ever. The user will say "commit" when ready.

## MRU Tab Switcher (Ctrl+Tab / Ctrl+Shift+Tab)

- **Single instance**: Only one `MRUSwitcher` dialog may be open at a time. Subsequent Ctrl+Tab while open is ignored.
- **Exclusive during open**: While the switcher is shown, no other actions (editor typing, sidebar toggling, etc.) are possible — only Tab/Ctrl+Tab navigation and Escape to close.
- **Alt+Tab behaviour**: Starts at MRU[1] (the previously active tab; MRU[0] is always the current tab), cycles forward with Tab, backward with Ctrl+Shift+Tab. Ctrl+release commits the selection and closes the dialog.
- **MRU list**: Maintained by `MRUManager` in `src/mru.py`; rebuilt on every tab change (`_on_tab_changed` → `mru.push()`).
- **No persistence**: The MRU list is in-memory only; it is rebuilt from session tab order on startup.
- **Double-cycle prevention**: Application accelerators (`app.set_accels_for_action`) AND the switcher's key controller both handle Ctrl+Tab. `cycle_from_accelerator()` sets `_accel_handled` flag so the key controller skips the event. If only the key controller fires (no accelerator), it cycles normally.
- **No ShortcutController in MRU mode**: `_update_tab_shortcuts()` skips registering shortcuts when `tab_switch_mode == "mru"` to avoid conflicts with application accelerators.

## Gotchas

- WebKitGTK requires the main thread for JS evaluation — use `GLib.idle_add()` for WebView calls.
- **WebKitGTK 6.0 quirks** (discovered during preview scroll-position work):
  - `Gtk.ScrolledWindow` adjustments are **ignored** by WebView — WebView scrolls internally.
  - `WebKit.WebView.get_hadjustment()` does **not exist** in Python bindings.
  - `evaluate_javascript_finish()` returns `JavaScriptCore.Value` (JSCValue), **not** `GLib.Variant`. Use `result.to_string()` to get the string, then `json.loads()`.
  - **DOM update over full reload**: After the initial `load_html()`, update content via `evaluate_javascript` setting `.innerHTML` (base64-encoded to avoid escaping issues). This avoids full document reload and natively preserves scroll position — no capture/restore dance needed.
  - CSS theme variables can be updated at runtime via `document.documentElement.style.setProperty()`.
- GtkSourceView needs `gi.require_version("GtkSource", "5")` — version 4 is for GTK3.
- `vaults.yaml` must never contain duplicate vault paths; deduplicate on load.
- On Flatpak, file access is sandboxed — use `org.freedesktop.portal` for file chooser.
- GtkSourceView 5 renamed `begin_not_undoable_action` → `begin_irreversible_action`.
- `editor.file_path` is a `str`, not `Path` — use `Path(editor.file_path).parent` for directory.
- Kill all existing app instances before starting a new one: Always use `./scripts/test-app.sh` — never manually `pkill` or `killall` (runs in timeout). Duplicate instances cause confusing state.
- Shift+Tab generates `Gdk.KEY_ISO_Left_Tab`, not `Gdk.KEY_Tab`. Always check for both keyvals.
- **Gtk.Stack remove/add destroys WebView DOM**: When a tab is renamed externally, `_on_tab_renamed` removes and re-adds the content stack child. This destroys the WebView's rendered DOM, but `_loaded` and `_last_html_hash` remain stale. Always call `preview.reset()` before `_refresh_preview()` after stack manipulation.
- **Tab button closures capture file_path**: Close buttons and click gestures in `TabBar._build_tab_widget` must read `_file_path` from the container widget at click time, not capture `file_path` at creation time. After `update_path()`, the old capture points to a dead path.
- **`mkdir -p` race**: A newly created subdirectory's CREATED event fires before monitors exist for its children. After `_start_monitor()` on a new dir, scan existing children with `os.listdir()` and emit CREATED signals for each so the tree picks them up.
- **RENAMED convention**: `Gio.FileMonitorEvent.RENAMED` sets `file=old, other=new` — the **opposite** of `MOVED_IN` (`file=new, other=old`). Always swap in `_on_monitor_event` before emitting.
- **VaultMonitor directory events**: `os.path.isdir()` must check real filesystem, not `_is_valid_md_dir()` which only validates the name. Directories must pass through to signal emission (don't `return` early after managing child monitors).

## Future Features

- **Vault Directory Watching (inotify)** ✓ implemented
  - `VaultMonitor` in `src/vault_monitor.py`: `Gio.FileMonitor` per vault + recursive subdirs
  - Events: `created`, `deleted`, `moved`, `renamed`, `changed` (with debounce)
  - External changes: new files → tree + backlink; deleted files → close tabs; renamed/moved → update paths + tabs; modified → content-changed banner
  - `mkdir -p` race: after `_start_monitor()`, scan existing children and emit signals
  - Skip mechanism: ref-counted `_skip_paths` dict for user-initiated operations

- **Integration & E2E Tests**
  - *Integration*: pytest + Xvfb (headless display) — Widget API tests for tab handling, editor↔preview sync, split view, vault tree expansion, session restore
  - *E2E*: pytest + dogtail/pyatspi (AT-SPI accessibility) — real keyboard/mouse events via accessibility bus
  - Goal: cover 80% of critical paths via integration tests, E2E for user flows (new file, open vault, preferences, zoom)
  - CI: GitHub Actions / GitLab CI with `xvfb-run` and `libatspi2.0-0`