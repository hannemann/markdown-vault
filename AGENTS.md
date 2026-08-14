# AGENTS.md

## 🚨 MANDATORY TOOL CHOICE RULES (HIGH PRIORITY)

Before exploring code, searching for symbols, or tracing dependencies:

1. **ALWAYS use Graphify FIRST:** Execute `graphify query "<question>"` or read `graphify-out/GRAPH_REPORT.md`.
2. **STRICT FALLBACK:** Do NOT use `grep`, `ripgrep`, `find`, or direct file browsing until Graphify has been checked.
3. Only fall back to `grep` if Graphify does not return sufficient line-level details.

---

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
- **Right sidebar** (toggleable via hamburger menu or shortcut; a vertical
  icon rail on the far right switches sections):
  - Outline (headings of current file)
  - Backlinks / `[[wikilink]]` references, grouped by vault
  - Metadaten (renders the current file's YAML frontmatter as key/value rows)
  - Git panel (status, diff, commit)
  - File details (word count, last modified)
- **Bottom bar** (Ctrl+Shift+F): live full-text search across vaults — `search.py` (UI) over `search_backend.py` (ripgrep engine with a Python fallback). Results are grouped per file and relevance-ranked (name/title > heading > body). Match modifiers `Aa` (case), `W` (whole word), `.*` (regex); non-regex queries also support operators/filters: multiple terms are AND-combined, `"phrase"` matches literally, `-term` excludes, and `tag:`/`path:`/`vault:` narrow the set. A shared **vault-scope dropdown** (`vault_scope.py`) picks the search scope — current vault (default), all vaults, or a specific vault — and is honoured by every search: full-text, semantic, Ask, and the quick-open file switcher (the same selector appears in both the search bar and the quick-open palette, backed by one `_search_scope` in `app_window`). A `?` popover shows the syntax.
- **Quick Open** (Ctrl+Space): fuzzy file switcher — `quick_open_palette.py` (Adw.Dialog) over `quick_open.py`. An empty query lists recent files (MRU then mtime); typing fuzzy-matches note names, frontmatter aliases, and (when the query contains a `/`) the vault-relative path, with match highlighting. Built on a provider/engine design (`QuickOpenEngine` merges providers), so more result sources — e.g. a future semantic/vector provider — slot in via `_make_quick_open_engine` in `app_window` without touching the palette.
- **Semantic search & Ask** (opt-in): vector retrieval over the vaults. `semantic_search.py` chunks notes and embeds them — local ONNX MiniLM in-process (recommended) or an Ollama backend — and `semantic_index.py` owns the per-file cache, incremental updates, and query, plus a manual rebuild. The **Ask** mode (Quick Open toggle) answers a question *from* your notes (local RAG): `semantic_index.retrieve` returns **note-level** passages (best chunk per note → whole note, title/heading-boosted), and `ask.py` grounds a chat model in them with `[n]` source citations, answering in the OS locale via a user-editable system prompt (the prompt permits comparison/aggregation across excerpts but forbids outside knowledge). The chat backend is Ollama (`/api/chat`) or an OpenAI-compatible server such as llama.cpp (`/v1/chat/completions`), with a reasoning on/off toggle (Qwen3 etc.). All configured under Preferences → Search (Embedding / Ask subpages).
- **In-view find** (Ctrl+F): a find bar (`find_bar.py`) that searches whichever view is focused — the editor (GtkSource search: highlight, next/prev, current/total counter) or the preview (WebKit find controller: total count). Enter / Shift+Enter step matches, Esc closes; the non-searched view is dimmed while open.

## Features

- **Multiple vaults**: freely selectable directories; add/remove via UI.
- **Tabs**: open multiple files simultaneously in center panel. Each tab owns its own `Editor` + `Preview` instance.
- **Dark mode**: `Adw.StyleManager` with System / Light / Dark toggle in hamburger menu. WebView CSS uses `@theme_*` named colours for automatic adaptation.
- **Git integration**: status indicators in file tree, diff view, commit from app.
- **Tags/backlinks**: wikilink-style `[[page]]` parsing and backlink discovery.
- **Wikilink autofix**: opt-in pre-save handling of `[[wikilinks]]` (all off by default) — normalize whitespace, redirect a broken link when exactly one vault file matches the basename (moved/renamed/casing), inform after a manual save about links that stay unresolved, and mark broken links live in the editor (gutter warning triangle + red underline). Runs on manual and close saves, never on autosave. Logic in `wikilink_autofix.py`.
- **Keybindings**: GNOME-style defaults, vim/emacs modes optional.
- **Markdown + images**: `![alt](path)` with relative and absolute path resolution.
- **Preferences dialog**: `Adw.PreferencesDialog` for autosave interval, default view mode, editor font size/tab width/wrap, preview zoom, and wikilink autofix (normalize / auto-fix moved links / warn on save / mark broken links).
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
    sidebar.py                — right sidebar (outline, backlinks, metadata, git, details)
    find_bar.py               — in-view find bar (Ctrl+F), editor/preview search
    quick_open.py             — quick-open engine: fuzzy matcher, providers, ranking
    quick_open_palette.py     — Ctrl+Space palette (Adw.Dialog), provider-driven
    search.py                 — bottom bar UI: live results, modifiers, operators, scope toggle
    search_backend.py         — search engine: ripgrep + Python fallback, ranking, query operators/filters
    search_logic.py           — pure helpers: heading extraction, file details, legacy vault search
    vault_scope.py            — shared vault-scope dropdown (current / all / a vault) for every search
    semantic_search.py        — chunking (chunk_markdown) + embedders (OnnxEmbedder/OllamaEmbedder) + VectorIndex
    semantic_index.py         — semantic index manager: per-file cache, incremental update, note-level RAG retrieve
    ask.py                    — RAG answering: grounded prompt (build_messages) + OllamaChat/OpenAIChat, reasoning toggle
    git_integration.py        — git status, diff, commit
    tags.py                   — [[wikilink]] parsing, backlinks
    wikilink_autofix.py       — pre-save wikilink autofix + broken-link detection (pure logic + WikilinkResolver glue)
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
    markdown_help.py          — Markdown syntax reference overlay (F1)
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
| --------------------------- | -------------------------- |
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
- App logs: `~/.local/state/markdown-vault/markdown-vault.log` (level ≤ INFO)
  and `~/.local/state/markdown-vault/markdown-vault.stderr.log` (level ≥ WARNING
  plus native/child stderr via fd redirect). The app rotates them at 1 MB, 3
  backups. Logging is set up by the app itself as the first action in
  `main.py`, so it works identically regardless of the launcher (app.sh,
  gtk-launch, terminal). On a terminal, messages ≤ INFO additionally appear on
  stdout and ≥ WARNING on stderr.
- NEVER use `killall python3` — that also kills firewalld and other system
  Python processes.

## Dependencies

- NEVER install packages directly — no `pip install`, no editing the venv by hand.
- To add a dependency you MAY (always) add it to `requirements.txt` (base) or
  `requirements-ai.txt` (the heavy/compiled AI stack, kept opt-in), pin the
  version, then pull it in with `make install` / `make install-ai`. That is the
  ONLY allowed way to add a dependency.
- After adding or changing a dependency in `requirements.txt`, regenerate the
  version-controlled Flatpak hash lock: `make lock-wheels`, review the diff, and
  commit the updated `requirements.lock`. Skipping this aborts `make build-flatpak`
  at the hash-verification gate (the download no longer matches the committed lock).
- Before a feature that added or changed a dependency is considered done, VERIFY
  the installer from a clean state: `make uninstall && make install` (and
  `make uninstall && make install-ai` when AI/optional deps are involved). A clean
  reinstall catches installer breakage — a missing transitive dep, a broken
  install script — that an incremental `make install` hides (this is how the
  llama-cpp-python `diskcache` gap surfaced).

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

- **Build a generalist, not edge-case workarounds**: solutions must be general and
  principled. Do NOT hardcode site- or language-specific values (e.g. class names
  like `navileiste`/`klappleiste`, per-site heuristics, magic strings for one page)
  to patch a single failing input — that is exactly what this project does not want.
  This applies especially to the web import, which must work across arbitrary sites,
  not be tuned so one page (e.g. Wikipedia) is perfect while it breaks others. If a
  general approach isn't achievable, surface the limitation and discuss it instead of
  burying a narrow workaround in the code.
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
- **Answer questions before changing code**: when the user asks a question, answer
  it first. Do NOT proactively start editing code, running fixes, or "improving"
  things in place of — or before — giving the answer. Investigate and explain, then
  make changes only when the user actually asks for them.
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

| Task                                             | Command                                                   |
| ------------------------------------------------ | --------------------------------------------------------- |
| Build/refresh the graph (first use, big changes) | `/graphify .`                                             |
| Incremental update after edits                   | `/graphify . --update`                                    |
| "How does X work?" / how does A connect to B?    | `/graphify query "how does the preview render markdown?"` |
| Call chain / dependency path between two symbols | `/graphify path "Editor" "Preview"`                       |
| Explain / locate a symbol and how it is wired    | `/graphify explain "TabManager"`                          |

Rules:

- graphify is the FIRST step for structural questions, not a fallback.
- If a query returns nothing useful or seems stale, run `/graphify . --update`
  once, then re-query — do NOT silently fall back to grep.
- Only after graphify has run and did not answer the question may you grep/read
  source directly.
- Use exactly the four forms above (`.`, `. --update`, `query`, `path`,
  `explain`). Do NOT invent other subcommands or flags.

**How to comply — every structural task:**

1. Ask yourself: is this about _where / how / why_ the code is structured, or who
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
