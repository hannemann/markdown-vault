project(
  'markdown-vault',
  'c',
  version: '0.1.0',
  meson_version: '>= 0.62.0',
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

subdir('src/bin')
subdir('src/lib/python3.13/site-packages')
subdir('src/share/markdown-vault')

gnome.post_install(
  update_desktop_database: true,
)
