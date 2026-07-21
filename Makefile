SHELL := /bin/bash
.PHONY: download-wheels build-flatpak bundle install-flatpak run-flatpak test-flatpak clean clean-build clean-cache build install uninstall clean-local run test

WHEEL_DIR := src/share/markdown-vault
FLATPAK_MANIFEST := $(WHEEL_DIR)/de.hannemann.markdown-vault.yml
BUILD_DIR := build-dir
CACHE_DIR := .flatpak-builder
REPO_DIR := repo
BUNDLE_FILE := markdown-vault.flatpak
APP_ID := de.hannemann.markdown-vault
PYTHONPATH_DIR := src/lib/python3.13/site-packages
# A python3 earlier in PATH (e.g. Homebrew) usually lacks PyGObject.
PYTHON := $(shell python3 -c 'import gi' 2>/dev/null && echo python3 || echo /usr/bin/python3)

download-wheels:
	@echo "=> Downloading Python wheels from PyPI..."
	@mkdir -p $(WHEEL_DIR)
	pip3 download --no-deps --only-binary=:all: \
		--python-version 3.13 --platform manylinux2014_x86_64 \
		--dest $(WHEEL_DIR) \
		pyyaml==6.0.3 pymdown-extensions==11.0.1 Pygments==2.20.0
	@echo "=> Wheels downloaded:"
	@ls -lh $(WHEEL_DIR)/*.whl

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

install: build
	@echo "=> Installing locally..."
	meson install -C builddir

uninstall:
	@echo "=> Uninstalling locally..."
	ninja -C builddir uninstall

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
	PYTHONPATH=$(PYTHONPATH_DIR) $(PYTHON) -m unittest discover -s tests -v

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
