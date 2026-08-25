# AGENTS.md

## 🚨 Behaviour & collaboration — non-negotiable (HIGH PRIORITY)

These rules govern **how you work and collaborate with the user** — they come before
any task, no matter how small or obvious a step looks. (How the *code* itself should be
written is separate, under "Conventions".)

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
- **Present the plan BEFORE implementing, and wait for the go-ahead.** Anything
  beyond a trivial, unambiguous edit starts with a short plan — what you will
  change, where, and what it means for the user — not with an edit. Do not start
  implementing while the plan is still being discussed, and do not "just prepare"
  files in the meantime: an unapproved change on disk is an approval you granted
  yourself.
  **An approval covers the plan that was approved, and nothing else.** The moment
  you want a different approach — a different mechanism, a different place, extra
  moving parts, or dropping a part the user said yes to — the old approval is
  **void**: say what changed and why, and get a new yes. This holds even when the
  new approach is better and even when the user's own follow-up prompted it;
  their new requirement is an argument for a new plan, not consent to it. The
  approval also lapses when the plan turns out to rest on a wrong premise —
  re-present, don't improvise.
- **NEVER edit files through a shell script.** File changes go through the `Edit` /
  `Write` tools — never `python3 - <<'EOF'`, never `sed -i`, never `cat > file`, never
  any other heredoc or redirection that rewrites a file. This is not a style
  preference: `Edit` refuses to touch a file that was not read first and fails loudly
  when the expected text is not there, so a stale assumption becomes an error instead
  of a silent no-op — a scripted `.replace()` that matched nothing has already gone
  unnoticed here. It also keeps every change visible in the transcript as a diff
  instead of hiding it inside a script. If you believe an edit is too mechanical for
  `Edit` (a line-range carve across a thousand lines), **ask first** and say why;
  the answer may be yes, but it is the user's call, not yours.
- **Commit autonomously — this project grants you standing commit authority.** Create
  `git commit`s yourself as soon as a topic is finished; you no longer wait for a commit
  word. This is the standing exception the global rule points at — it covers **`git commit`
  only**. **`git push` and merge still require the user's explicit word** in that message
  (they are outward-facing and effectively irreversible; a commit is local and reversible).
  Report each commit briefly so the user keeps oversight and can have you amend or undo it.
  Keep the discipline that makes autonomy safe: one logical change per commit, **tests
  first** (never commit code without its tests, see the TDD section), a factual conventional
  message explaining the *why*, and stage named files only — never `git add -A`/`.`.
- **Commit at topic boundaries — cut in front, never carve up afterwards**: as soon as
  one topic is finished (a feature, a bugfix in a distinct area, a review finding that
  opens a *new* concern) and BEFORE starting the next, commit the finished work rather than
  letting it slide. This keeps history atomic for free and checkpoints work against loss. Do
  NOT let several unrelated topics pile up in one working tree and then try to split them
  into separate commits after the fact — the post-hoc `git stash`/hunk-surgery that requires
  is slow and has already risked losing work. A fix to code from the *same* still-uncommitted
  topic stays in that commit; only a genuinely *different* subsystem or user-facing concern
  is a boundary.

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
- **Stack**: Python 3, GTK 4 + libadwaita, editor in GtkSourceView 5, preview
  rendered as HTML/CSS in WebKitGTK.
- **Config**: `settings.yaml` in the XDG config dir — vaults + settings
  (`core/config.py`).
- **Session**: `session.json` in the XDG **state** dir — window/layout state,
  not configuration; `core/session.py` owns the fields.

## Tech decisions

Decisions the code alone does not explain — the stack itself is under *Project*,
the `gi.require_version` pins and per-file mechanics live in the code:

- **Math** is `latex2mathml` → MathML, rendered natively by WebKitGTK —
  deliberately no JavaScript/CDN.
- **Vault list and settings live in YAML** (`settings.yaml`), not dconf — simpler
  to debug and version.
- **Flatpak** is the primary distribution format (sandboxed file access via portal).

## Project structure

For module-level detail — where a symbol lives, who calls it, how modules connect — use
**graphify** (`make graph-query/explain/path`); it derives that from the code and stays
current. Do **not** keep an exhaustive per-file list here — it only rots and duplicates
the graph. This section is the curated navigation index: directory layout, entry points,
key files.

### Directory layout

| Path | Purpose |
| ---- | ------- |
| `src/bin/markdown-vault.in` | launcher template; Meson substitutes interpreter + PYTHONPATH → installed `bin/markdown-vault` |
| `src/markdown_vault/` | the Python package (`import markdown_vault`) — organized into domain/layer **subpackages** (below); the package root holds only the loaders `__init__.py` / `__main__.py` / `main.py` |
| `src/markdown_vault/<pkg>/` | one subpackage per domain/layer: **`core`** (config, session, paths, attachments, logging, event routing, validation, history, debug) · **`markdown`** (md primitives: fences, tags, frontmatter, LaTeX→MathML) · **`uikit`** (low-level dialogs/banners) · **`editor`** (GtkSourceView surface, tabs, autosave, find bar) · **`preview`** (WebKit rendering, markdown widgets) · **`graph`** (knowledge-graph view) · **`vault`** (file tree, monitor, git, backlinks, wikilinks, file ops) · **`search`** (full-text + semantic + Ask + quick-open + llama runtime) · **`importers`** (document/web/dialog import) · **`ui`** (sidebar/preferences/status panels) · **`app`** (window shell, `*_manager` coordinators, the domain controllers — zoom/zen/find/ask, link navigation, preview actions — that register their own actions, and focused collaborators without actions such as the reading-position memory `ScrollMemory`; see `src/markdown_vault/app/AGENTS.md`). Imports are **absolute** (`markdown_vault.<pkg>.<mod>`); the layering is an acyclic DAG guarded by `tests/test_layering.py` |
| `src/markdown_vault/<pkg>/meson.build` | the **manually-maintained `py_sources` list** for that subpackage — a new `.py` MUST be added to **its subpackage's** list (alphabetical) incl. `__init__.py`, or it is not installed and the app crashes with `ModuleNotFoundError`. Meson has no `glob()` |
| `src/markdown_vault/meson.build` | root build: the three loaders' `py_sources` + one `subdir('<pkg>')` per subpackage |
| `src/css/` | `style.css` (WebKit preview) + `gtk.css` (GTK widgets); Meson installs both into `markdown_vault/css/` |
| `src/share/markdown-vault/` | `.desktop` / metainfo / gresource, icons, and the Flatpak manifest (`.yml`) |
| `tests/` | unit tests (unittest); run via `make test` / `make test-one` |
| `meson.build` (top level) | build-system entry point |

### Entry points — where to start for a task

- **App startup:** `__main__.py` → `main.py` (AdwApplication, logging) → `app/app_window.py`
  (three-panel main window, delegates to the `app/*_manager.py` helpers).
- **Add a document-import format:** `importers/document_import.py` — add a handler and
  register it in `_HANDLERS`; the File tab (`importers/dialog_import.py`) surfaces it via
  `SUPPORTED_SUFFIXES`.
- **Web import (URL → note):** `importers/web_import.py`, surfaced through
  `importers/dialog_import.py`.
- **Managed image attachments:** `core/attachments.py` — the shared layout web import,
  document import, paste and drag-drop all store into.
- **Preview / rendering:** `preview/preview.py` (Markdown → HTML via Python-Markdown +
  pymdownx, rendered in WebKit); styling in `src/css/style.css`.
- **Editor:** `editor/editor.py` (GtkSourceView 5).
- **Search:** bottom-bar `search/search.py` over `search/search_backend.py`; semantic + Ask
  in `search/semantic_search.py` / `search/semantic_index.py` / `search/ask.py`.
- **Config / session:** `core/config.py` (settings.yaml + settings), `core/session.py` (JSON
  state). **The settings have one owner:** `config.settings()` returns *the* dict for the
  process — mutate it in place and call `config.save_settings()`. Never call
  `load_settings()` in application code: a private copy silently resets whatever another
  component changed meanwhile, because the whole block is written back.
  `tests/test_settings_ownership.py` enforces this.
- **Add an embedding backend:** `search/semantic_search.py` — add an `Embedder`
  class and a branch in `build_embedder()` returning `(embedder, signature_tag)`;
  defaults go in `core/config.py`, the picker in
  `ui/preferences/embedding_subpage.py`.
- **Add a Preferences page:** a `…PageMixin` in `ui/preferences/<name>_page.py` —
  add it to the `PreferencesDialog` bases **and** to `_page_names` (so `open_page`
  accepts it) in `ui/preferences/dialog.py`, plus the module to
  `ui/preferences/meson.build`.
- **Add a sidebar panel:** `ui/sidebar.py` — **two** places: `add_titled(...)` in
  `Sidebar.__init__` **and** an entry in `_SIDEBAR_SECTIONS`, which `_build_rail()`
  turns into the icon-rail button. Miss the second and the panel exists in the
  stack but is unreachable — no error, no warning.
- **Add a Quick Open result source:** `app_window._make_quick_open_engine` —
  append a provider; `QuickOpenEngine` (`search/quick_open.py`) merges them, the
  palette is untouched.

For anything finer-grained, query the graph rather than reading top-to-bottom.

## Module Dependencies

**Installation paths** (Meson install prefix):

- **Binaries:** `~/.local/bin/` (user) or `/usr/bin/` (system) — the short command
  `markdown-vault`.
- **Python code:** `<datadir>/de.hannemann.markdown-vault/python/markdown_vault/`, a
  private directory the launcher puts on `PYTHONPATH` (not `site-packages`).
- **Data files:** `<datadir>/de.hannemann.markdown-vault/`.

Every directory the app owns is named after the **app ID**; file and binary names stay
short. The binary path is bound in the AppArmor profile (`packaging/apparmor/`), without
which WebKitGTK aborts on Ubuntu 24.04 (see the WebKit note under *Conventions*).

**XDG base directories** — config (`settings.yaml`), state (logs, `session.json`, debug
dumps), cache (semantic index), data (downloaded models). `core/paths.py` owns the
mapping, the rationale, and the `XDG_*_HOME` / `MDV_CONFIG_DIR` overrides. Under Flatpak
they land in `~/.var/app/<app-id>/…`, so debug a sandboxed build there, not in the host's
dirs.

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
| Measure one file's line coverage | `make coverage FILE=<src-file> [T=<test_module…>]` (`sys.monitoring`, no `coverage.py` dep; fails on a red suite) |
| Measure one file's coupling | `make callbacks FILE=<src-file>` — how many of its own methods it hands to other objects (pure AST, no test run). The metric a split is judged by: moving code between files does not change it, extracting state and responsibility does. Counting rule in `scripts/count_callbacks.py`; take before/after with this same target, never with an ad-hoc counter. |
| Code graph (see graphify)     | `make graph-update` · `graph-query Q="…"` · `graph-explain S="…"` · `graph-path A="…" B="…"` · `graph-build` |
| Drive the running app (D-Bus) | `make dbg-ready` (wait until up, e.g. after restart) · `dbg-state` · `dbg-tabs` · `dbg-active` · `dbg-open F=…` · `dbg-close F=…` · `dbg-select F=…` · `dbg-search Q="…"` · `dbg-ask Q="…"` (open+submit+wait+print answer) · `dbg-prefs PAGE=… [SUB=…]` (open Preferences at a page, print the visible page) · low-level `dbg-quickopen Q=…` · `dbg-submit` · `dbg-waitidle` · `dbg-answer` |

Hard rules:

- To automate the running app (open files, run a search, ask a question and read
  the answer back) use the `make dbg-*` targets — NOT a bare `gdbus …` (not
  pre-approved → prompts). They talk to the app's debug D-Bus interface, which
  exists only when it was started via `make start`/`restart` (the dev launcher
  sets `MDV_DEBUG_CONTROL`); the shipped app has no such interface. After a
  `make restart`, run `make dbg-ready` before the first `dbg-*` call — the app
  needs a moment to own the bus name (do NOT poll with a raw `gdbus` loop; that
  is not pre-approved and prompts). Call each `make dbg-*` as its OWN command,
  not chained with `;`/`&&` or `echo` headers — a compound command is not
  covered by the make allow-rule and prompts (see "Compound Commands").

- Run these EXACTLY as written. Do NOT construct your own `gtk-launch`,
  `python3 -m ...`, `pkill`, or `kill` commands for start/stop.
- `make run` is FORBIDDEN — it runs in the foreground and will hang the tool call.
- After changing code you MUST `make install` before `make restart`
  (the app runs the installed copy, not the source tree).
- `make restart` is idempotent and exits 0 on success — a "not
  running" stop is normal, not an error, so do not retry with other commands.
- App logs: `~/.local/state/de.hannemann.markdown-vault/markdown-vault.log` (level ≤ INFO)
  and `~/.local/state/de.hannemann.markdown-vault/markdown-vault.stderr.log` (level ≥ WARNING
  plus native/child stderr via fd redirect). The app rotates them at 1 MB, 3
  backups. Logging is set up by the app itself as the first action in
  `main.py`, so it works identically regardless of the launcher (app.sh,
  gtk-launch, terminal). On a terminal, messages ≤ INFO additionally appear on
  stdout and ≥ WARNING on stderr. Those paths are the **default** state dir; the
  logs follow `$XDG_STATE_HOME` (`core/paths.py`), so a **Flatpak** build logs to
  `~/.var/app/de.hannemann.markdown-vault/.local/state/de.hannemann.markdown-vault/` and an E2E run
  to its throwaway dir — check there, not in the host's log, when diagnosing those.
- NEVER use `killall python3` — that also kills firewalld and other system
  Python processes. The app runs as `python3 -m markdown_vault.main`; match it
  by that command line when you must identify the process (but prefer
  `make status` / `make stop`).

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
- **Flatpak-manifest changes (permissions, runtime version) are invisible to the
  suite** — the source tree cannot see a packaging mistake, and the only gate is a
  manual `make build-flatpak` + run. So after touching
  `src/share/markdown-vault/*.yml`, verify the affected capability **in the packaged
  app**, not just on the host. A wrong `--talk-name` or a runtime bump that drops a
  typelib degrades *gracefully* and therefore looks like a legitimate environment
  result, not a bug. Current instance: secret storage — open **Preferences → Search →
  Ask** in the Flatpak build and confirm the **API key field is enabled** (if it shows
  "no keyring available — key won't be saved", libsecret's file backend or the Secret
  portal is the problem). Storage differing by install type is expected, not a defect:
  a sandboxed build uses libsecret's app-private file backend (so no Seahorse entry),
  a local install the host keyring — the manifest comment explains why.
- **Verify a sandbox assumption in a sandbox, cheaply — but know what the probe can
  answer.** `org.gnome.Sdk` is installed, so `flatpak run --command=bash
  org.gnome.Sdk//<ver>` (plus `--talk-name=…` to model a grant) answers **permission and
  API** questions — "is this D-Bus service/portal reachable, does this library behave as
  assumed?" — in seconds, no app build needed. That is how the keyring assumption above
  was falsified before it shipped: libsecret ignores a Secret Service grant in a sandbox.
  It does **not** answer **path or environment** questions: a runtime run has no
  application identity (`/.flatpak-info` says `[Runtime]`, no app-id), so Flatpak sets up
  no per-app XDG dirs and the probe sees the **host** ones (`XDG_STATE_HOME=~/.local/state`)
  — whereas the packaged app gets `~/.var/app/<app-id>/…`. For those, build and run the
  app itself.

## Test driven development

Always write failing tests first, then implement the fix. Run tests to verify they fail, then implement the minimal code to make them pass. Never commit code without corresponding tests.

**Test the caller, not only the receiver.** When a change gives a collaborator a new argument, a unit test of the collaborator leaves the wiring that supplies it unguarded — is the value passed at all, and with the right polarity? Add a small test at the call site that fails when the argument is dropped or inverted. This blind spot has recurred across the codebase; the fuller statement lives in `src/markdown_vault/app/AGENTS.md` (it is not `app/`-specific).

## Testing loop (manual integration tests)

For features involving GTK/WebKit/WebViews that cannot be tested
with `unittest`:

**Preparation (before the first loop):**

**WARNING:** Check with `git status` for uncommitted changes before starting.
If any exist, warn the user explicitly — changes may be lost during loop resets.

1. Close the app: `make stop`
2. Delete debug dumps: `rm ~/.local/state/de.hannemann.markdown-vault/debug-*`
3. In `~/.config/de.hannemann.markdown-vault/settings.yaml` set `log.level: debug`
   (`log:\n  level: debug`), `debug.active: true` and enable the required
   `debug.dump.*` flags (the settings are a nested tree — one branch per domain)

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

- **Hold description against behaviour — a comment, a symbol name, an `AGENTS.md`
  line, or a key name is a *claim*, not evidence.** When it matters, grep before you
  trust it; it is cheap. Two claims in this very file were spot-checked and **both
  were wrong**: `app_window._ask_unavailable_reason` (no such symbol — it is
  `AskController.unavailable_reason()`) and "WebView CSS uses `@theme_*` colours"
  (it uses runtime-injected `var(--…)`; `@theme_*` is syntax WebKit silently
  ignores) — cost: **one `grep` each**. Prefer a docstring beside the code over a
  prose copy in a doc (which drifts); when a doc must state behaviour, cite the
  symbol and keep it thin.
- **Question the change before adjusting what it broke.** When a change makes a test
  red, trips a guard, or flips a doc/README claim, the first question is whether the
  *change* must be that way — not how to silence the objection. Weakening the guard,
  relaxing the test, or editing the doc is the *second* question, reached only once the
  change has been confirmed necessary as written.
- **Harden the write path, not only the read path.** When a change defends against a
  hostile input (a crafted repo, a malicious page, untrusted config), ask what the
  *countermeasure itself* does on the write path — not only whether it protects the read
  path. The git-config hardening's worst finding was not a bypass but **data loss**: the
  read-path filter-blanking, applied to the write path, would have committed unfiltered
  content and corrupted a git-lfs tree. A countermeasure can be the defect. (That write
  path isn't wired up yet — the guard is foresight for a planned commit UI — which is the
  point: harden it before it ships, not after it corrupts something.)
- **When a security check stands in for the property it means, find where the proxy
  diverges — and check whether the tool answers the real question directly before
  re-implementing it.** "Does this config belong to the repo (untrusted) or the user
  (trusted)?" was approximated three times — config *level*, then origin *file*, then a
  raw `includeIf` path *value* — and each proxy had its own gap (worktree config,
  `include.path`, a non-matching `includeIf`). `git config --show-scope` answers the real
  question directly — it resolves includes and evaluates conditions itself — which closed
  all three at once and made the code smaller than the first attempt.
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
- **Lint with `make lint`** — ruff, configured in `ruff.toml` (`E501` line length, `F`
  pyflakes, `BLE001` blind-except, `RUF100` unused-noqa; the rest of pycodestyle `E` is
  deliberately off, see the config comment on the `gi.require_version` E402 conflict). The
  suite enforces it: `tests/test_layering.py::TestRuffClean` runs `ruff check` and turns
  `make test` red on a violation — skipped when ruff (a dev-only tool in
  `requirements-dev.txt`) is absent, so a base install stays green. A deliberately broad
  `except Exception` carries `# noqa: BLE001 — <why broad>`.
- **User-facing strings go through gettext** (`core/i18n.py`). Wrap new ones with `_()` (`from markdown_vault.core.i18n import _`); for count-dependent text use `ngettext(singular, plural, n)`, **never** a hand-rolled `"note" if n == 1 else "notes"` (English has 2 plural forms, Polish 3, Arabic 6 — only the catalog knows). **Never `_(f"…")`** — the f-string is interpolated before `_()`, so the msgid becomes the finished text, not the template; use `_("… {name} …").format(...)` with **named** placeholders so a translator can reorder. English source strings ARE the msgids, so a missing catalog renders English (the fallback). Not translated: comments, logs, internal IDs, and the user's Markdown content. Existing strings are being marked **per subpackage** in the i18n rounds — mark what you newly write, but do not wrap existing strings ad hoc outside those rounds (a half-marked module is worse than a clean increment).
- **Never build a user-facing message by concatenating a translated string with a variable** (`_("Failed: ") + str(exc)`, `_("…") + name`). The translator sees only the first fragment and cannot reorder around the insert — fine in German, broken in Japanese/Arabic where the variable must move ahead of it. Use one msgid with a **named** placeholder instead: `_("Failed: {detail}").format(detail=…)`. This also covers a foreign `str(exc)` tail — map the exception to a translated message at the boundary rather than appending it (see `importers/web_import.describe_error`). Pure separators (`", "`, `"\n"`) joining two *already-translated* pieces are not this bug. No automated guard — an AST check can't tell a broken foreign-text append from a harmless newline join, so this holds at review time.
- CSS for WebView rendering goes in `src/css/` (Meson installs it into the package as `markdown_vault/css/`), not inline in Python.
- Vault config YAML keys are case-sensitive, paths are absolute.
- Git features must gracefully handle repos without git initialized.
- Images in Markdown: support `![alt](path)` with both relative and absolute paths.
- **Put behaviour in the module of its domain — don't let one file become a catch-all.**
  Code belongs in the subpackage and class that owns its concern, not wherever it is first
  convenient to drop it. When a class starts accumulating methods that really serve another
  domain (a reading-position memory, link following, a preview action…), **extract them into
  their own object** instead of growing a god-object. An own object earns its place through
  **its own state**, though — without it the coupling only moves address (rule 1 in
  `app/AGENTS.md`); what holds even without state is that a surface is handed over as **one**
  collaborator, not a bundle of callbacks at the call site (rule 3). And where **no existing
  domain fits, give the code its own module** — or, for a whole new domain or layer, a **new
  subpackage**, which groups several such modules (a subpackage is a domain, not a single
  object). Introducing a domain is a valid move, not the last resort of squeezing code into
  the nearest existing file. This is the rule that has been moving `MainWindow` from owning
  everything toward a shell plus focused collaborators — its behaviour is extracted into
  managers and controllers, but **phase 2 is not yet done**: the constructor wiring and the
  callback back-edges the window still hands out are not yet bundled.
  `src/markdown_vault/app/AGENTS.md` holds the full criteria ("Two kinds of neighbours",
  "Rules for the next cut", and the `make callbacks` metric that tracks the remaining work).
  It applies everywhere, not just in `app/`.
- **New Python modules**: a new `.py` goes into its subpackage's `meson.build`
  `py_sources` — see the *Project structure* table for that mechanic and the
  `ModuleNotFoundError` it prevents. Use **absolute** imports
  (`from markdown_vault.<pkg>.<mod> import …`); keep the package DAG acyclic
  (`tests/test_layering.py` guards it). A subpackage may nest one level where a
  single surface got too big — `ui/preferences/` is the dialog split into a shell
  plus one module per page (own `meson.build` + `subdir()` in the parent). The
  "not deeper than package level" rule concerns where an `AGENTS.md` may sit, not
  Python nesting. Watch the direction of the new edge: `ui → ui.preferences`, so
  nothing under `ui/preferences/` may import `ui/sidebar.py` or `ui/status_bar.py`
  — that would close a cycle and turn the layering guard red.
- **GTK CSS in `css/gtk.css`**: Target GTK 4.14 / libadwaita 1.5. `var(--name)` and `color-mix()` need GTK 4.16+ and are silently dropped with "Expected a valid color" parser warnings. Use `@accent_bg_color` and `alpha(@color, 0.3)` instead. This does not apply to `css/style.css`, which is rendered by WebKit.
- **WebKit needs an unprivileged user namespace**: WebKitGTK 2.46+ always sets up a `bwrap` sandbox and aborts the whole process if it cannot (`Failed to fully launch dbus-proxy`). On Ubuntu 24.04 this requires the AppArmor profile in `packaging/apparmor/` — see README. There is no API or env var to disable the sandbox.
- **Test organization**: Add tests to existing test files grouped by topic (e.g. vault_monitor events → `test_vault_monitor_events.py`). Do not create new test files with arbitrary context names — distribute into the files that already cover the module under test. When in doubt, ask.
- **Error handling (non-negotiable, guarded by `tests/test_layering.py::TestNoSilentSwallows`)**: no exception is swallowed without a trace. Every `except` handler — of **any** caught type, not only bare `except Exception` — must do one of three things: **log** it (`logger.<level>(..., exc_info=True)`; a runtime-variable level via `logger.log(level, …)` counts); **re-raise**; or carry a **one-line justification comment** stating *why* swallowing is correct here — the consequence, or what the caller relies on, never a restatement of the caught type (that is slop). A handler that surfaces the error to the user (a banner/toast/dialog) still needs a comment or a log: the guard cannot see surface functions, and a dialog leaves no trace once dismissed. `print()` is only for a CLI driver (`importers/web_import.py`).
- **Logging**: Every module MUST use the standard `logging` module. Add `import logging` and `logger = logging.getLogger(__name__)` at the top of each file. Use `logger.debug()`/`logger.info()`/`logger.warning()`/`logger.error()` — NEVER use `print()` or any other ad-hoc output for diagnostics. Handle every `except` per the Error-handling rule above (log, re-raise, or a justification comment). Log level is configurable via `settings.loglevel` (debug/info/warning/error), effective after restart.
- **Temp files**: NEVER use the system `/tmp` directory. Use the local `./tmp/` directory instead. The system `/tmp` is shared, unpredictable, and cleaned up by the OS. Local `./tmp/` is project-scoped and ignored by `.gitignore`, so it stays fully under your control.

## Gotchas

- WebKitGTK requires the main thread for JS evaluation — use `GLib.idle_add()` for WebView calls.
- **WebKitGTK 6.0 quirks** (discovered during preview scroll-position work):
  - `Gtk.ScrolledWindow` adjustments are **ignored** by WebView — WebView scrolls internally.
  - `WebKit.WebView.get_hadjustment()` does **not exist** in Python bindings.
  - `evaluate_javascript_finish()` returns `JavaScriptCore.Value` (JSCValue), **not** `GLib.Variant`. Use `result.to_string()` to get the string, then `json.loads()`.
  - **DOM update over full reload**: After the initial `load_html()`, update content via `evaluate_javascript` setting `.innerHTML`, passing the HTML as a `json.dumps(html, ensure_ascii=False)` string literal to handle escaping. (Base64 + `atob` was used earlier but broke non-ASCII characters — do not reintroduce it.) This avoids a full document reload and natively preserves scroll position — no capture/restore dance needed.
  - CSS theme variables can be updated at runtime via `document.documentElement.style.setProperty()`.
- GtkSourceView needs `gi.require_version("GtkSource", "5")` — version 4 is for GTK3.
- `settings.yaml` must never contain duplicate vault paths; deduplicate on load.
- On Flatpak, file access is sandboxed — use `org.freedesktop.portal` for file chooser.
- GtkSourceView 5 renamed `begin_not_undoable_action` → `begin_irreversible_action`.
- `editor.file_path` is a `str`, not `Path` — use `Path(editor.file_path).parent` for directory.
- Kill all existing app instances before starting a new one: always use `make stop` (or `make restart`, which stops first) — never hand-roll `pkill`/`kill`/`killall`. Duplicate instances cause confusing state.
- Shift+Tab generates `Gdk.KEY_ISO_Left_Tab`, not `Gdk.KEY_Tab`. Always check for both keyvals.

## Tickets

Tickets are stored under `~/Nextcloud/Notes/Markdown-Vault/Tickets/`, grouped by module
(a Nextcloud-versioned directory outside the repo — no longer `./tmp/Tickets/`):

```
~/Nextcloud/Notes/Markdown-Vault/Tickets/
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
When a ticket-related keyword is mentioned (e.g. "ticket", "bug", "feature"), search under `~/Nextcloud/Notes/Markdown-Vault/Tickets/` for relevant files first.
When a ticket changes it's status move it to the appropriate folder.
If a ticket can be broken down into subtasks, create a folder with the same name. Create tickets for subtasks in that folder.
