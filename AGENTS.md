# AGENTS.md

> **graphify gate.** For ANY question about how the code is structured, your
> FIRST tool call MUST be `/graphify` (see **Code Exploration**). If you are
> about to run grep/rg/find/read to *understand* code, STOP — that urge is the
> signal to call `/graphify` instead. Grepping a structural question before
> graphify has run is a process error, even if grep would "work".

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
- **CLI launcher**: `src/bin/markdown-vault.in` — a Meson-generated template installed as `bin/markdown-vault`; it pins the PyGObject interpreter and `PYTHONPATH`, then runs `python3 -m markdown_vault.main`. Match the running process by its command line (`markdown_vault.main`).

## Project structure

```
src/
  bin/markdown-vault.in       — launcher template; Meson substitutes the interpreter
                                and PYTHONPATH, then installs it as bin/markdown-vault
  markdown_vault/             — the Python package (import `markdown_vault`)
    __init__.py               — package marker
    __main__.py               — entry point (python3 -m markdown_vault)
    main.py                   — AdwApplication setup, logging
    app_window.py             — main window, three-panel layout (delegates to the managers below)
    tab_manager.py            — TabOrchestrator: tab lifecycle
    session_manager.py        — vault session save/restore
    view_mode_manager.py      — edit/render/split view switching
    input_manager.py          — keyboard shortcuts / accelerators
    file_manager.py, file_ops.py — file create/delete/rename/move operations
    monitor_handler.py        — routes VaultMonitor events to tree/index/tabs
    event_router.py           — FileEventDispatcher (external-event fan-out to the sidebar)
    content_changes.py        — external-change detection + reload banner
    autosave.py               — AutosaveManager
    dialogs.py, banners.py    — reusable Adw dialogs / banner widget
    vault_tree.py             — left panel: file tree for vaults
    vault_monitor.py          — Gio.FileMonitor wrapper for external change detection
    editor.py                 — text editor widget (GtkSourceView 5)
    preview.py                — WebView-based Markdown renderer
    tabs.py                   — TabBar + Tab widgets
    sidebar.py                — right sidebar (outline, backlinks, git, details)
    search.py                 — bottom bar: full-text search across vaults
    search_logic.py           — search worker (runs in daemon thread)
    git_integration.py        — git status, diff, commit
    tags.py                   — [[wikilink]] parsing, backlinks
    backlink_index.py         — O(1) backlink lookup, built on startup
    file_index.py             — O(1) wikilink resolution (single index shared across previews)
    config.py                 — vaults.yaml reader/writer + settings
    session.py                — session persistence (JSON)
    preferences.py            — Adw.PreferencesDialog
    mru.py                    — MRU tab switcher (Ctrl+Tab)
    history.py                — navigation history (back/forward)
    path_utils.py             — vault path resolution helpers
    validation.py             — input validation utilities
    latex_mathml.py           — LaTeX → MathML converter (no JS/CDN)
    markdown_help.py          — keyboard shortcuts overlay
    meson.build               — Python package build rules (the manually-maintained py_sources list)
  css/
    style.css                 — WebView styling for rendered Markdown
    gtk.css                   — GTK CSS for tab bar and widgets
                                (Meson installs both into markdown_vault/css/)
  share/markdown-vault/
    icons/hicolor/            — icon theme
    de.hannemann.markdown-vault.desktop
    de.hannemann.markdown-vault.metainfo.xml
    de.hannemann.markdown-vault.gresource.xml
    de.hannemann.markdown-vault.yml  — Flatpak manifest
    meson.build               — Data files build rules
meson.build            — top-level build system
tests/                   — unit tests (unittest); run with PYTHONPATH=src
```

## Module Dependencies

<!-- DEPENDENCY_MAP_START -->


<!-- DEPENDENCY_MAP_END -->

**Installation paths:**
- **Binaries:** `~/.local/bin/` (user) or `/usr/bin/` (system)
- **Python code:** `<datadir>/markdown-vault/python/markdown_vault/` — a private directory, not the
  interpreter's `site-packages`, which may sit outside the install prefix. The generated launcher puts
  it on `PYTHONPATH`.
- **Data files:** `~/.local/share/markdown-vault/` or `/usr/share/markdown-vault/`
- **Config:** `~/.config/markdown-vault/` (identical for all installations)
- **State/Logs:** `~/.local/state/markdown-vault/` (identical for all installations)

## Running the app — agents MUST use ONLY these commands

The app is a GTK GUI. NEVER start it in the foreground: a GUI process does not
exit, so the tool call blocks until timeout. `scripts/app.sh` detaches the
process and returns immediately — always use it.

| Task                        | Command (run VERBATIM)     |
|-----------------------------|----------------------------|
| Start / restart the app     | `./scripts/app.sh restart` |
| Stop the app                | `./scripts/app.sh stop`    |
| Check if running            | `./scripts/app.sh status`  |
| Install after a code change | `make install`             |
| Run unit tests              | `make test`                |

Hard rules:

- Run these EXACTLY as written. Do NOT construct your own `gtk-launch`,
  `python3 -m ...`, `pkill`, or `kill` commands for start/stop.
- `make run` is FORBIDDEN — it runs in the foreground and will hang the tool call.
- After changing code you MUST `make install` before `./scripts/app.sh restart`
  (the app runs the installed copy, not the source tree).
- `./scripts/app.sh restart` is idempotent and exits 0 on success — a "not
  running" stop is normal, not an error, so do not retry with other commands.
- App log: `/tmp/markdown-vault.log`.
- NEVER use `killall python3` — that also kills firewalld and other system
  Python processes.

## Test driven development

Always write failing tests first, then implement the fix. Run tests to verify they fail, then implement the minimal code to make them pass. Never commit code without corresponding tests.

## Testing loop (manual integration tests)

For features involving GTK/WebKit/WebViews that cannot be tested
with `unittest`:

**Preparation (before the first loop):**

**WARNING:** Check with `git status` for uncommitted changes before starting.
If any exist, warn the user explicitly — changes may be lost during loop resets.

1. Close the app: `./scripts/app.sh stop`
2. Delete debug dumps: `rm ~/.local/state/markdown-vault/debug-*`
3. In `~/.config/markdown-vault/vaults.yaml` set `loglevel: debug`,
   `debug_active: true` and enable required `debug_dump_*` flags

**Loop:**

1. **Implement change** + write/update unit tests
2. **Run unit tests:** `make test`
3. **Install:** `make install`
4. **Start/restart the app:** `./scripts/app.sh restart`
5. **Test the feature manually** (use debug dumps if available)
6. **Bug found:** implement fix → go to step 2
7. **Commit** (only on user request)
8. **Cleanup** (only on user request): remove test files in `tmp/`,
   delete debug dumps, reset `debug_active` and `loglevel` to originals

**Note:** For interactions that cannot be automated (tab switches,
opening files in editor, toggling sidebar etc.) ask the user.

## Conventions

- All strings in the code or tests are english (comments etc.)
- Follow PEP 8, max line length 100.
- Use `snake_case` for functions/variables, `PascalCase` for classes.
- All user-facing strings must be translatable via `gettext`.
- CSS for WebView rendering goes in `src/css/` (Meson installs it into the package as `markdown_vault/css/`), not inline in Python.
- Vault config YAML keys are case-sensitive, paths are absolute.
- Git features must gracefully handle repos without git initialized.
- Images in Markdown: support `![alt](path)` with both relative and absolute paths.
- **New Python modules**: When creating a new `.py` file in `src/markdown_vault/`, it MUST be added to the `py_sources` list in `src/markdown_vault/meson.build` (alphabetically sorted). Meson has no built-in `glob()` — the list is manually maintained. Forgetting to add it means the file will not be installed and the app will crash with `ModuleNotFoundError`.
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
- **MRU list**: Maintained by `MRUManager` in `src/markdown_vault/mru.py`; rebuilt on every tab change (`_on_tab_changed` → `mru.push()`).
- **No persistence**: The MRU list is in-memory only; it is rebuilt from session tab order on startup.
- **Double-cycle prevention**: Application accelerators (`app.set_accels_for_action`) AND the switcher's key controller both handle Ctrl+Tab. `cycle_from_accelerator()` sets `_accel_handled` flag so the key controller skips the event. If only the key controller fires (no accelerator), it cycles normally.
- **No ShortcutController in MRU mode**: `_update_tab_shortcuts()` skips registering shortcuts when `tab_switch_mode == "mru"` to avoid conflicts with application accelerators.

## Gotchas

- WebKitGTK requires the main thread for JS evaluation — use `GLib.idle_add()` for WebView calls.
- **WebKitGTK 6.0 quirks** (discovered during preview scroll-position work):
  - `Gtk.ScrolledWindow` adjustments are **ignored** by WebView — WebView scrolls internally.
  - `WebKit.WebView.get_hadjustment()` does **not exist** in Python bindings.
  - `evaluate_javascript_finish()` returns `JavaScriptCore.Value` (JSCValue), **not** `GLib.Variant`. Use `result.to_string()` to get the string, then `json.loads()`.
  - **DOM update over full reload**: After the initial `load_html()`, update content via `evaluate_javascript` setting `.innerHTML`, passing the HTML as a `json.dumps(html, ensure_ascii=False)` string literal to handle escaping. (Base64 + `atob` was used earlier but broke non-ASCII characters — do not reintroduce it.) This avoids a full document reload and natively preserves scroll position — no capture/restore dance needed.
  - CSS theme variables can be updated at runtime via `document.documentElement.style.setProperty()`.
- GtkSourceView needs `gi.require_version("GtkSource", "5")` — version 4 is for GTK3.
- `vaults.yaml` must never contain duplicate vault paths; deduplicate on load.
- On Flatpak, file access is sandboxed — use `org.freedesktop.portal` for file chooser.
- GtkSourceView 5 renamed `begin_not_undoable_action` → `begin_irreversible_action`.
- `editor.file_path` is a `str`, not `Path` — use `Path(editor.file_path).parent` for directory.
- Kill all existing app instances before starting a new one: always use `./scripts/app.sh stop` (or `restart`, which stops first) — never hand-roll `pkill`/`kill`/`killall`. Duplicate instances cause confusing state.
- Shift+Tab generates `Gdk.KEY_ISO_Left_Tab`, not `Gdk.KEY_Tab`. Always check for both keyvals.
- **Gtk.Stack remove/add destroys WebView DOM**: When a tab is renamed externally, `_on_tab_renamed` removes and re-adds the content stack child. This destroys the WebView's rendered DOM, but `_loaded` and `_last_html_hash` remain stale. Always call `preview.reset()` before `_refresh_preview()` after stack manipulation.
- **Tab button closures capture file_path**: Close buttons and click gestures in `TabBar._build_tab_widget` must read `_file_path` from the container widget at click time, not capture `file_path` at creation time. After `update_path()`, the old capture points to a dead path.
- **`mkdir -p` race**: A newly created subdirectory's CREATED event fires before monitors exist for its children. After `_start_monitor()` on a new dir, scan existing children with `os.listdir()` and emit CREATED signals for each so the tree picks them up.
- **RENAMED convention**: `Gio.FileMonitorEvent.RENAMED` sets `file=old, other=new` — the **opposite** of `MOVED_IN` (`file=new, other=old`). Always swap in `_on_monitor_event` before emitting.
- **VaultMonitor directory events**: on DELETE and on a RENAME's old path the directory is already gone, so `os.path.isdir()` returns False — determine directory-ness from bookkeeping (`fpath in self._monitors`), NOT the filesystem, or child monitors leak and the tree/index go stale. Directories must pass through to signal emission (don't `return` early after managing child monitors).

## Tickets

Tickets are stored under `./tmp/Tickets/`, grouped by module:

```
tmp/Tickets/
  App_Window/
    Draft/      ← feature drafts (ignore)
    Pending/    ← not yet started
    Progress/   ← in progress
    Review/     ← ready for review
    Done/       ← completed tickets
  VaultTree/
    Done/
    Pending/
    ...
  Sidebar/
    ...
  ...
```

Status folders: `Draft`, `Pending`, `Progress`, `Review`, `Done`.
When a ticket-related keyword is mentioned (e.g. "ticket", "bug", "feature"), search under `./tmp/Tickets/` for relevant files first.
When a ticket changes it's status move it to the appropriate folder.
If a ticket can be broken down into subtasks, create a folder with the same name. Create tickets for subtasks in that folder.

## Code Exploration — graphify FIRST

For ANY question about code structure — architecture, "how does X work?", who
calls Y, dependencies, call chains, where a symbol lives, "what breaks if I
change Z" — your FIRST tool call MUST be `/graphify`. Do NOT use grep/rg/find or
read source files as your first step for a structural question — call graphify
first, every time. You may NOT pre-judge graphify as "insufficient" before
running it. graphify queries a local AST knowledge graph — faster and more
accurate than reading files.

Commands (run VERBATIM — these are the ONLY graphify forms; do not invent others):

| Task                                             | Command                                       |
|--------------------------------------------------|-----------------------------------------------|
| Build/refresh the graph (first use, big changes) | `/graphify .`                                  |
| Incremental update after edits                   | `/graphify . --update`                         |
| "How does X work?" / how does A connect to B?    | `/graphify query "how does the preview render markdown?"` |
| Call chain / dependency path between two symbols | `/graphify path "Editor" "Preview"`           |
| Explain / locate a symbol and how it is wired    | `/graphify explain "TabManager"`              |

Rules:

- graphify is the FIRST step for structural questions, not a fallback.
- If a query returns nothing useful or seems stale, run `/graphify . --update`
  once, then re-query — do NOT silently fall back to grep.
- Only after graphify has run and did not answer the question may you grep/read
  source directly.
- Use exactly the four forms above (`.`, `. --update`, `query`, `path`,
  `explain`). Do NOT invent other subcommands or flags.

**How to comply — every structural task:**

1. Ask yourself: is this about *where / how / why* the code is structured, or who
   calls / depends on what? If yes → graphify is your first tool call.
2. Issue `/graphify query|path|explain …` FIRST. Not grep. Not read. Not glob.
3. Only after graphify has actually run and did not answer may you grep/read the
   files it pointed you to.

```
WRONG:  "How does the sidebar refresh?"  →  grep -r "refresh" src/
RIGHT:  "How does the sidebar refresh?"  →  /graphify query "how does the sidebar refresh?"
                                            →  then read the files it cites
```

If you catch yourself reaching for grep/rg/find/read to understand code you have
not yet queried with graphify, that itch is the trigger to call graphify instead.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

When the user types `/graphify`, use the installed graphify skill or instructions before doing anything else.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- Dirty graphify-out/ files are expected after hooks or incremental updates; dirty graph files are not a reason to skip graphify. Only skip graphify if the task is about stale or incorrect graph output, or the user explicitly says not to use it.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
