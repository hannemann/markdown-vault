SHELL := /bin/bash
.PHONY: download-wheels build-flatpak bundle install-flatpak run-flatpak test-flatpak clean clean-build clean-cache build venv venv-ai install install-ai uninstall clean-local run test

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

download-wheels:
	@echo "=> Downloading Python wheels (base + AI) for the Flatpak runtime (py3.13)..."
	@rm -rf $(WHEELS_DIR)
	@mkdir -p $(WHEELS_DIR)
	pip3 download --dest $(WHEELS_DIR) \
		--only-binary=:all: --python-version 3.13 \
		--platform manylinux2014_x86_64 \
		--platform manylinux_2_17_x86_64 \
		--platform manylinux_2_28_x86_64 \
		-r requirements.txt -r requirements-ai.txt
	@echo "=> $$(ls -1 $(WHEELS_DIR)/*.whl | wc -l) wheels in $(WHEELS_DIR)"

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
