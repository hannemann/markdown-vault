project(
  'markdown-vault',
  'c',
  version: '0.1.0',
  license: 'AGPL-3.0-or-later',
  meson_version: '>= 0.64.0',
  default_options: [
    'warning_level=2',
  ],
)

i18n = import('i18n')
gnome = import('gnome')
python = import('python')

# The application needs an interpreter with PyGObject. A python3 earlier in PATH
# (e.g. Homebrew) usually lacks it, so fall back to the distribution one.
py = python.find_installation('python3', modules: ['gi'], required: false)
if not py.found()
  py = python.find_installation('/usr/bin/python3', modules: ['gi'])
endif

# Modules are installed into a private directory instead of the interpreter's
# site-packages, which may live outside the install prefix.
pkgdatadir = get_option('prefix') / get_option('datadir') / 'markdown-vault'
pythondir = pkgdatadir / 'python'
# The launcher runs the app through this venv interpreter (created by
# `make install`), so pip-resolved dependencies are on its path. The venv is
# built with --system-site-packages, so system PyGObject/GTK stay visible.
venvpython = pkgdatadir / 'venv' / 'bin' / 'python3'

subdir('src/bin')
subdir('src/markdown_vault')
subdir('src/share/markdown-vault')

# Ship the full licence text in the installed tree / Flatpak (the About dialog also
# renders it from GTK's own strings, but carry the file for completeness).
install_data(
  'LICENSE',
  install_dir: get_option('datadir') / 'licenses' / meson.project_name(),
)

gnome.post_install(
  update_desktop_database: true,
)
