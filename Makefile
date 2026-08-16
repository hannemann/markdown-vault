SHELL := /bin/bash
.PHONY: _fetch-wheels lock-wheels download-wheels build-flatpak bundle-flatpak install-flatpak uninstall-flatpak run-flatpak test-flatpak clean clean-build clean-cache build venv venv-ai install install-ai uninstall clean-local run test test-one test-e2e graph-build graph-update graph-query graph-path graph-explain start stop restart status dbg-ready dbg-state dbg-tabs dbg-active dbg-open dbg-close dbg-select dbg-search dbg-quickopen dbg-submit dbg-waitidle dbg-answer dbg-ask

WHEEL_DIR := src/share/markdown-vault
WHEELS_DIR := $(WHEEL_DIR)/wheels
FLATPAK_MANIFEST := $(WHEEL_DIR)/de.hannemann.markdown-vault.yml
BUILD_DIR := build-dir
CACHE_DIR := .flatpak-builder
REPO_DIR := repo
BUNDLE_FILE := markdown-vault.flatpak
APP_ID := de.hannemann.markdown-vault
PYTHONPATH_DIR := src
# A python3 earlier in PATH (e.g. Homebrew) usually lacks PyGObject.
PYTHON := $(shell python3 -c 'import gi' 2>/dev/null && echo python3 || echo /usr/bin/python3)
VENV := $(HOME)/.local/share/markdown-vault/venv
REQUIREMENTS := requirements.txt
REQUIREMENTS_AI := requirements-ai.txt
LOCK := requirements.lock

_fetch-wheels:
	@echo "=> Downloading Python wheels (base + AI) for the Flatpak runtime (py3.13)..."
	@rm -rf $(WHEELS_DIR)
	@mkdir -p $(WHEELS_DIR)
	# odfpy publishes no wheel. Split it off (single-sourcing its pin from
	# requirements-ai.txt) and fetch it as an sdist via host resolution — pip
	# forbids --no-binary together with --platform. Its deps come as wheels; pip
	# builds odfpy at Flatpak-build time under --no-build-isolation. Everything
	# else is fetched as cross-platform wheels.
	@grep -vE '^odfpy([[:space:]=<>!~]|$$)' $(REQUIREMENTS_AI) > $(WHEELS_DIR)/.req.wheels
	@grep -E  '^odfpy([[:space:]=<>!~]|$$)' $(REQUIREMENTS_AI) > $(WHEELS_DIR)/.req.sdist
	pip3 download --dest $(WHEELS_DIR) --no-binary odfpy -r $(WHEELS_DIR)/.req.sdist
	pip3 download --dest $(WHEELS_DIR) \
		--only-binary=:all: --python-version 3.13 \
		--platform manylinux2014_x86_64 \
		--platform manylinux_2_17_x86_64 \
		--platform manylinux_2_28_x86_64 \
		-r requirements.txt -r $(WHEELS_DIR)/.req.wheels
	@rm -f $(WHEELS_DIR)/.req.wheels $(WHEELS_DIR)/.req.sdist
	@echo "=> $$(ls -1 $(WHEELS_DIR)/*.whl | wc -l) wheels + $$(ls -1 $(WHEELS_DIR)/*.tar.gz 2>/dev/null | wc -l) sdists in $(WHEELS_DIR)"

# Regenerate the VERSION-CONTROLLED lock. Run deliberately when deps change, then
# review the diff and commit requirements.lock — that committed file is the trust
# anchor an upstream change has to get past in review.
lock-wheels: _fetch-wheels
	@scripts/gen-wheel-lock.sh $(WHEELS_DIR) > $(LOCK)
	@echo "=> $(LOCK) updated ($$(wc -l < $(LOCK)) entries) — review the diff and commit it"

# Fetch wheels and VERIFY them against the committed lock: a changed upstream
# artifact fails the build (a reviewable diff) instead of being re-hashed
# silently. The install then uses the committed lock, copied in for the manifest.
download-wheels: _fetch-wheels
	@test -f $(LOCK) || { echo "!! no $(LOCK) — run 'make lock-wheels' and commit it first"; exit 1; }
	@scripts/gen-wheel-lock.sh $(WHEELS_DIR) > $(WHEELS_DIR)/.lock.actual
	@if ! diff -u $(LOCK) $(WHEELS_DIR)/.lock.actual; then \
		echo "!! downloaded wheels do not match $(LOCK) — either a dependency in"; \
		echo "   requirements*.txt changed (lock is behind) or an upstream artifact"; \
		echo "   was substituted. Review the diff above; if intended, run"; \
		echo "   'make lock-wheels' and commit the updated $(LOCK)."; \
		rm -f $(WHEELS_DIR)/.lock.actual; exit 1; \
	fi
	@rm -f $(WHEELS_DIR)/.lock.actual
	@cp $(LOCK) $(WHEELS_DIR)/requirements.lock
	@echo "=> wheels verified against committed $(LOCK)"

build-flatpak: download-wheels
	@echo "=> Cleaning previous build..."
	@rm -rf $(BUILD_DIR) $(CACHE_DIR)
	@echo "=> Building Flatpak..."
	flatpak-builder --force-clean $(BUILD_DIR) $(FLATPAK_MANIFEST)

bundle-flatpak: build-flatpak
	@echo "=> Exporting repo..."
	flatpak build-export $(REPO_DIR) $(BUILD_DIR)
	@echo "=> Creating bundle..."
	flatpak build-bundle $(REPO_DIR) $(BUNDLE_FILE) $(APP_ID)
	@echo "=> Bundle created: $(BUNDLE_FILE) ($$(ls -lh $(BUNDLE_FILE) | awk '{print $$5}'))"

install-flatpak: bundle-flatpak
	@if flatpak list --app | grep -q $(APP_ID); then \
		echo "=> Uninstalling existing version..."; \
		flatpak remove --noninteractive $(APP_ID); \
	fi
	@echo "=> Installing Flatpak bundle..."
	flatpak install --user --noninteractive $(BUNDLE_FILE)

uninstall-flatpak:
	@echo "=> Uninstalling Flatpak..."
	flatpak remove --noninteractive $(APP_ID)

build:
	@echo "=> Building with Meson..."
	meson setup --prefix=$$HOME/.local builddir && meson compile -C builddir

venv:
	@echo "=> Ensuring venv with Python dependencies ($(REQUIREMENTS))..."
	@test -d $(VENV) || $(PYTHON) -m venv --system-site-packages $(VENV)
	@$(VENV)/bin/pip install --upgrade --disable-pip-version-check -r $(REQUIREMENTS)

venv-ai: venv
	@echo "=> Adding optional AI dependencies ($(REQUIREMENTS_AI)) to the venv..."
	@$(VENV)/bin/pip install --upgrade --disable-pip-version-check -r $(REQUIREMENTS_AI)
	@sh scripts/install-llama.sh $(VENV)/bin/pip

install: build venv
	@echo "=> Installing locally..."
	meson install -C builddir

install-ai: build venv-ai
	@echo "=> Installing locally (with optional AI dependencies)..."
	meson install -C builddir

uninstall:
	@echo "=> Uninstalling locally..."
	ninja -C builddir uninstall
	@echo "=> Removing venv..."
	@rm -rf $(VENV)
	@echo "=> Cleaning __pycache__ directories..."
	MESON_INSTALL_PREFIX=$$HOME/.local $(PYTHON) build-aux/meson/post_uninstall.py

clean-local:
	@echo "=> Cleaning local build..."
	rm -rf builddir

run:
	$$HOME/.local/bin/markdown-vault

run-flatpak:
	flatpak run $(APP_ID)

test-flatpak:
	@echo "=> Testing Python dependencies in sandbox..."
	flatpak run --command=python3 $(APP_ID) -c "\
import pygments; print('pygments:', pygments.__version__); \
import yaml; print('yaml:', yaml.__version__); \
import markdown; print('markdown:', markdown.__version__); \
import pymdownx; print('pymdownx: OK')"

test:
	@echo "=> Running tests..."
	@PY=$$([ -x "$(VENV)/bin/python" ] && echo "$(VENV)/bin/python" || echo "$(PYTHON)"); \
	PYTHONPATH=$(PYTHONPATH_DIR) "$$PY" -m unittest discover -s tests -v

# Run a single test target instead of the whole suite. Pass T= a dotted path
# (module, class, or method) and/or K= a name substring (unittest -k):
#   make test-one T=test_preview
#   make test-one T=test_preview.TestBlankLineBeforeList
#   make test-one T=test_preview.TestBlankLineBeforeList.test_thematic_break_is_not_treated_as_a_list
#   make test-one K=blank_line          # every test whose name matches
test-one:
	@test -n "$(T)$(K)" || { echo "usage: make test-one T=<module[.Class[.method]]> | K=<name-substring>"; exit 2; }
	@PY=$$([ -x "$(VENV)/bin/python" ] && echo "$(VENV)/bin/python" || echo "$(PYTHON)"); \
	if [ -n "$(T)" ]; then \
	  echo "=> Running $(T)..."; \
	  PYTHONPATH=$(PYTHONPATH_DIR):tests "$$PY" -m unittest -v $(T); \
	else \
	  echo "=> Running tests matching '$(K)'..."; \
	  PYTHONPATH=$(PYTHONPATH_DIR) "$$PY" -m unittest discover -s tests -v -k "$(K)"; \
	fi

# Full-app E2E smoke tests: spawn the app on an ISOLATED session bus
# (dbus-run-session — so the developer's running instance is not activated
# instead) and drive it over the D-Bus debug interface. Headless via xvfb-run
# when present, else the current display, else skipped. Kept out of `test`.
test-e2e:
	@echo "=> Running E2E smoke tests (isolated bus)..."
	@PY=$$([ -x "$(VENV)/bin/python" ] && echo "$(VENV)/bin/python" || echo "$(PYTHON)"); \
	if command -v xvfb-run >/dev/null 2>&1; then \
	  PYTHONPATH=$(PYTHONPATH_DIR) xvfb-run -a dbus-run-session -- "$$PY" -m unittest discover -s tests-e2e -v; \
	elif [ -n "$$DISPLAY" ] || [ -n "$$WAYLAND_DISPLAY" ]; then \
	  echo "   (no xvfb-run; using the current display)"; \
	  PYTHONPATH=$(PYTHONPATH_DIR) dbus-run-session -- "$$PY" -m unittest discover -s tests-e2e -v; \
	else \
	  echo "   SKIP: need xvfb-run or a display"; \
	fi

clean-build:
	@echo "=> Removing build directory..."
	rm -rf $(BUILD_DIR)

clean-cache:
	@echo "=> Removing flatpak-builder cache..."
	rm -rf $(CACHE_DIR)

clean: clean-build clean-cache
	@echo "=> Removing repo and bundle..."
	rm -rf $(REPO_DIR) $(BUNDLE_FILE)
	@echo "=> Cleaning wheel files and temp archives..."
	rm -f $(WHEEL_DIR)/*.whl $(WHEEL_DIR)/*.tar.gz
	@echo "Done."

start:
	@echo "=> start app"
	./scripts/app.sh start

stop:
	@echo "=> stop app"
	./scripts/app.sh stop

restart:
	@echo "=> restart app"
	./scripts/app.sh restart

status:
	@./scripts/app.sh status

# --- Code graph (graphify) ------------------------------------------------
# Wrapped as make targets so graphify runs without a per-command approval
# prompt (make is pre-approved; a bare `graphify ...` is not).
graph-build:
	graphify .

graph-update:
	graphify update .

graph-query:
	@test -n "$(Q)" || { echo 'usage: make graph-query Q="how does the preview render markdown?"'; exit 2; }
	graphify query "$(Q)"

graph-path:
	@test -n "$(A)" && test -n "$(B)" || { echo 'usage: make graph-path A="Editor" B="Preview"'; exit 2; }
	graphify path "$(A)" "$(B)"

graph-explain:
	@test -n "$(S)" || { echo 'usage: make graph-explain S="TabManager"'; exit 2; }
	graphify explain "$(S)"

# --- D-Bus debug interface (dev launcher only) ----------------------------
# Drive the running app over its debug D-Bus interface without a per-command
# approval prompt (make is pre-approved; a bare `gdbus ...` is not). The
# interface only exists when the app was started via the dev launcher
# (start/restart set MDV_DEBUG_CONTROL); it is absent from the shipped app.
DBUS_PATH := /de/hannemann/markdown_vault/debug
DBUS_IFACE := de.hannemann.markdown_vault.Debug
DBUS_CALL := gdbus call --session --dest $(APP_ID) --object-path $(DBUS_PATH) --method $(DBUS_IFACE)
# gdbus prints the reply as a GVariant tuple; unwrap it to the raw string/paths.
UNWRAP := python3 scripts/dbus-unwrap.py
# pipefail so `gdbus … | $(UNWRAP)` fails when gdbus fails (a dead app must not
# read as an empty result). Scoped to dbg-* only, leaving other recipes as-is.
dbg-%: .SHELLFLAGS := -o pipefail -c

dbg-ready:                 # block (≤10s) until the debug interface answers, e.g. after restart
	@for i in $$(seq 1 50); do \
		$(DBUS_CALL).ActiveFile >/dev/null 2>&1 && exit 0; \
		sleep 0.2; \
	done; \
	echo "debug D-Bus interface not up — start the app with 'make start'/'make restart'" >&2; \
	exit 1

dbg-state:                 ## dump window state as JSON (active file, tabs, vault)
	@$(DBUS_CALL).DumpState | $(UNWRAP)

dbg-tabs:
	@$(DBUS_CALL).ListTabs | $(UNWRAP)

dbg-active:
	@$(DBUS_CALL).ActiveFile | $(UNWRAP)

dbg-open:                  # F=<abs path>: open a note in a tab
	@test -n "$(F)" || { echo 'usage: make dbg-open F=/abs/path/note.md'; exit 2; }
	@$(DBUS_CALL).OpenFile "$(F)"

dbg-close:                 # F=<abs path>: close a tab
	@test -n "$(F)" || { echo 'usage: make dbg-close F=/abs/path/note.md'; exit 2; }
	@$(DBUS_CALL).CloseTab "$(F)"

dbg-select:                # F=<abs path>: select a file in the vault tree
	@test -n "$(F)" || { echo 'usage: make dbg-select F=/abs/path/note.md'; exit 2; }
	@$(DBUS_CALL).SelectInTree "$(F)"

dbg-search:                # Q=<query>: run full-text search, wait, print result paths
	@test -n "$(Q)" || { echo 'usage: make dbg-search Q="query"'; exit 2; }
	@$(DBUS_CALL).Search "$(Q)" >/dev/null
	@$(DBUS_CALL).WaitIdle 10000 >/dev/null
	@$(DBUS_CALL).SearchResults | $(UNWRAP)

# Low-level Quick-Open/Ask steps (compose your own flow), plus the dbg-ask combo.
dbg-quickopen:             # Q=<text>: open the palette and type the query (no submit)
	@test -n "$(Q)" || { echo 'usage: make dbg-quickopen Q="text"'; exit 2; }
	@$(DBUS_CALL).QuickOpen "$(Q)"

dbg-submit:                # press Enter in the palette (answer / open selection)
	@$(DBUS_CALL).Submit

dbg-waitidle:              # [T=<ms>, default 120000]: block until async work settles
	@t=$(or $(T),120000); \
	gdbus call --timeout $$(( t/1000 + 10 )) --session --dest $(APP_ID) --object-path $(DBUS_PATH) --method $(DBUS_IFACE).WaitIdle $$t | $(UNWRAP)

dbg-answer:                # print the current (streaming) Ask answer as raw Markdown
	@$(DBUS_CALL).AskAnswer | $(UNWRAP)

dbg-ask:                   # Q=<question>: full Ask flow (open, submit, wait, print answer)
	@test -n "$(Q)" || { echo 'usage: make dbg-ask Q="your question"'; exit 2; }
	@$(DBUS_CALL).QuickOpen "$(Q)" >/dev/null
	@$(DBUS_CALL).Submit >/dev/null
	@settled=$$(gdbus call --timeout 130 --session --dest $(APP_ID) --object-path $(DBUS_PATH) --method $(DBUS_IFACE).WaitIdle 120000); \
	case "$$settled" in *true*) ;; *) echo "WARNING: answer may be truncated — WaitIdle timed out with the answer still streaming" >&2 ;; esac
	@$(DBUS_CALL).AskAnswer | $(UNWRAP)
