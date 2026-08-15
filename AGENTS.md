# AGENTS.md

## 🚨 Code exploration — graphify FIRST (HIGH PRIORITY)

For ANY question about code structure — architecture, "how does X work?", who
calls Y, dependencies, call chains, where a symbol lives, "what breaks if I change
Z" — your FIRST tool call MUST be graphify, never grep/rg/find or reading source.
It queries a local AST knowledge graph (`graphify-out/`) and returns a scoped
subgraph, faster and more accurate than reading files. Do NOT pre-judge it as
"insufficient" before running it; only after it has run and did not answer may you
grep/read the files it points you to. (When the user types `/graphify`, invoke the
graphify skill.)

Run graphify through its **pre-approved `make` wrappers** (no approval prompt),
never a bare `graphify …`:

| Task                                             | `make` target                 | wraps                  |
| ------------------------------------------------ | ----------------------------- | ---------------------- |
| Build/refresh the graph (first use, big changes) | `make graph-build`            | `graphify .`           |
| Incremental update after edits                   | `make graph-update`           | `graphify update .`    |
| "How does X work?" / how A connects to B         | `make graph-query Q="…"`      | `graphify query "…"`   |
| Call chain / path between two symbols            | `make graph-path A="…" B="…"` | `graphify path "A" "B"`|
| Explain / locate a symbol and its wiring         | `make graph-explain S="…"`    | `graphify explain "…"` |

Rules:

- graphify is the FIRST step for structural questions, not a fallback. If a query
  returns nothing useful or seems stale, `make graph-update` once and re-query —
  do NOT silently fall back to grep. Only after it has run and did not answer may
  you grep/read the files it cited.
- Dirty `graphify-out/` files are expected after edits; not a reason to skip it.
- `graphify-out/wiki/index.md` is for broad navigation; read
  `graphify-out/GRAPH_REPORT.md` only for broad architecture review or when
  query/path/explain don't surface enough.
- After modifying code, run `make graph-update` to keep the graph current
  (AST-only, no API cost).

```
WRONG:  "How does the sidebar refresh?"  →  grep -r "refresh" src/
RIGHT:  "How does the sidebar refresh?"  →  make graph-query Q="how does the sidebar refresh?"
                                            →  then read the files it cites
```

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
- **Dependencies**: never install packages yourself and never add a dependency without asking first — see the **Dependencies** section for the only allowed flow.

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
- **Managed image attachments** (`attachments.py`): a note's downloaded/inserted images live under one per-vault tree mirroring the note tree — `<vault>/attachments/<note-relative-path>/<note-stem>/` — and are kept in sync with the note. Added by web import (opt-in download), paste (Ctrl+V / "Paste Image"), drag-drop onto the editor, or "Insert Image…" (hamburger + editor context menu); never by hand-copying into the tree. Deleting a note/folder removes its attachments; renaming/moving moves them and relinks the note (in the editor buffer if open, else on disk) — driven from both the in-app tree handlers and the file monitor (external), idempotent. The attachments tree shows dimmed in the sidebar with an "internal" pill, is not a drop target and blocks new-file/folder/import; its images are visible but not openable/draggable. Hand-typed image links are classified live: a broken target gets a gutter warning + red underline, a local image outside the tree gets a gutter hint + hover tooltip and adopts into the tree on a gutter double-click.
- **Keybindings**: GNOME-style defaults, vim/emacs modes optional.
- **Markdown + images**: `![alt](path)` with relative and absolute path resolution.
- **Preferences dialog**: `Adw.PreferencesDialog` for autosave interval, default view mode, editor font size/tab width/wrap, preview zoom, and wikilink autofix (normalize / auto-fix moved links / warn on save / mark broken links).
- **Zoom**: Ctrl+plus/minus/0 keyboard shortcuts; Ctrl+Wheel zoom on content area; per-tab zoom persisted in session.
- **Session persistence**: window size, sidebar, tabs (view modes + split positions), active tab, expanded vaults, editor/preview zoom.
- **Rich Markdown (pymdown-extensions)**: strikethrough `~~text~~`, highlight `==text==`, superscript `^sup^`, subscript `~sub~`, task lists `- [ ]`, tasklist `- [x]`, superfences (tabs, line numbers, highlight lines), magic links (auto URLs, @mentions, #issues), keyboard keys `++ctrl+c++`, smart symbols (quotes, dashes, ellipsis), emoji shortcodes `:smile:`, math formulas `$...$`, footnotes `[^1]` … `[^1]: …` (Python-Markdown `footnotes`; in-page anchor clicks scroll instead of navigating), task lists with checkboxes.
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
    attachments.py            — managed image attachments: <vault>/attachments/<note-path>/ layout
                                (attachment_target/store_image), move/remove on rename/delete + relink,
                                is_internal guard, classify/adopt for hand-typed links
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
exit, so the tool call blocks until timeout.

**All routine tooling is exposed as `make` targets — always prefer them.** `make`
is pre-approved in this environment, so these run WITHOUT an approval prompt,
whereas a bare `./scripts/app.sh …`, `graphify …`, or an ad-hoc
`python -m unittest …` is NOT pre-approved and interrupts the user with a
permission request. The app targets just wrap `scripts/app.sh`, which detaches
the GUI and returns immediately. Do not hand-roll the wrapped commands when a
target exists.

| Task                          | Command (run VERBATIM)                                       |
| ----------------------------- | ------------------------------------------------------------ |
| Start / restart the app       | `make restart`                                               |
| Stop the app                  | `make stop`                                                  |
| Check if running              | `make status`                                                |
| Install after a code change   | `make install`                                               |
| Run the full unit test suite  | `make test`                                                  |
| Run one test / tests by name  | `make test-one T=<module[.Class[.method]]>` or `make test-one K=<name-substring>` |
| Code graph (see graphify)     | `make graph-update` · `graph-query Q="…"` · `graph-explain S="…"` · `graph-path A="…" B="…"` · `graph-build` |

Hard rules:

- Run these EXACTLY as written. Do NOT construct your own `gtk-launch`,
  `python3 -m ...`, `pkill`, or `kill` commands for start/stop.
- `make run` is FORBIDDEN — it runs in the foreground and will hang the tool call.
- After changing code you MUST `make install` before `make restart`
  (the app runs the installed copy, not the source tree).
- `make restart` is idempotent and exits 0 on success — a "not
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
- After adding or changing a dependency in **either** `requirements.txt` **or**
  `requirements-ai.txt` (the lock spans both, and `download-wheels` fetches from
  both), regenerate the version-controlled Flatpak hash lock: `make lock-wheels`,
  review the diff, and commit the updated `requirements.lock`. Skipping this aborts
  `make build-flatpak` at the hash-verification gate (the download no longer matches
  the committed lock).
- Before a feature that added or changed a dependency is considered done, VERIFY
  **both** installation types from a clean state — `make uninstall && make install`
  (base) **and** `make uninstall && make install-ai` (with the optional AI stack) —
  regardless of which requirements file changed. A clean reinstall catches installer
  breakage — a missing transitive dep, a broken install script — that an incremental
  `make install` hides (this is how the llama-cpp-python `diskcache` gap surfaced).
  And run the suite in the **base-only** state too: it MUST stay green, with tests
  that need the optional stack `skipUnless`-guarded (e.g. `llama_runtime.is_available()`)
  so they skip rather than error — a base install must never produce a red suite.

## Test driven development

Always write failing tests first, then implement the fix. Run tests to verify they fail, then implement the minimal code to make them pass. Never commit code without corresponding tests.

## Testing loop (manual integration tests)

For features involving GTK/WebKit/WebViews that cannot be tested
with `unittest`:

**Preparation (before the first loop):**

**WARNING:** Check with `git status` for uncommitted changes before starting.
If any exist, warn the user explicitly — changes may be lost during loop resets.

1. Close the app: `make stop`
2. Delete debug dumps: `rm ~/.local/state/markdown-vault/debug-*`
3. In `~/.config/markdown-vault/vaults.yaml` set `loglevel: debug`,
   `debug_active: true` and enable required `debug_dump_*` flags

**Loop:**

1. **Implement change** + write/update unit tests
2. **Run unit tests:** `make test`
3. **Install:** `make install`
4. **Start/restart the app:** `make restart`
5. **Test the feature manually** (use debug dumps if available)
6. **Bug found:** implement fix → go to step 2
7. **Commit** (only on user request)
8. **Cleanup** (only on user request): remove test files in `tmp/`,
   delete debug dumps, reset `debug_active` and `loglevel` to originals

**Note:** For interactions that cannot be automated (tab switches,
opening files in editor, toggling sidebar etc.) ask the user.

## Conventions

- **Build generalists, never one-case special-cases** (applies everywhere, not just
  the importer). A solution must handle the general class of a problem, not target
  exactly one input / site / page. Special-casing a single case is how an app rots
  into an unmaintainable monster — do NOT do it, anywhere in the codebase. Do NOT
  hardcode site- or language-specific values (class names like
  `navileiste`/`klappleiste`, per-site heuristics, magic strings for one page) to
  patch a single failing input. If a general approach isn't achievable, surface the
  limitation and discuss it instead of burying a narrow workaround in the code. When
  case-specific behaviour IS genuinely wanted, isolate it in a **separate, dedicated
  unit** selected explicitly for that case (e.g. a Wikipedia-specific importer
  applied only to Wikipedia) — never as a conditional special-case inside the general
  path. The web import is one instance: it must work across arbitrary sites, not be
  tuned so one page is perfect while others break.
- **This governs how you reason about requirements, not only how you write code.**
  Markdown Vault is a general-purpose GNOME app shipped to **every user on Earth** — any
  language, any script, any locale, any document. NEVER weigh or dismiss a requirement by
  the developer's own situation. "Deutsch/Englisch reicht", "for this user's vault it's
  irrelevant", "unlikely anyone imports Arabic/Hindi/Thai", "it's just their PDF" is the
  SAME single-case specialization this rule forbids — only aimed at the person asking
  instead of at one web page. A capability (OCR scripts, encodings, RTL, formats, …) is a
  first-class requirement for the whole audience; judge trade-offs for that audience, and
  never assume the requester's language or use-case is the app's scope.
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
- **Answer questions; never infer permission to act.** When the user asks a
  question, answer it — do NOT start editing code, running fixes, or "improving"
  things instead of, or before, giving the answer. And when *you* asked the user a
  question or proposed doing something ("should I fix X?", "shall I filter Y?"),
  you MUST wait for an explicit, unambiguous **yes to that action** before doing it.
  A reply that does not clearly approve it — a pasted URL, a clarification, a
  tangent, "ok"/"👍" about something else — is NOT consent: hold, or ask again.
  Never treat your own proposal as pre-approved, and never bundle an unrequested
  change onto an approved one. When unsure whether something is authorized, STOP and
  ask instead of assuming. Make changes only when the user has actually and clearly
  asked for them.
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
- Kill all existing app instances before starting a new one: always use `make stop` (or `make restart`, which stops first) — never hand-roll `pkill`/`kill`/`killall`. Duplicate instances cause confusing state.
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
