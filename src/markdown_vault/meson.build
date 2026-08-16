py_sources = files(
  '__init__.py',
  '__main__.py',
  'main.py',
)

subdir('app')
subdir('core')
subdir('editor')
subdir('graph')
subdir('importers')
subdir('markdown')
subdir('preview')
subdir('search')
subdir('ui')
subdir('uikit')
subdir('vault')

install_data(
  py_sources,
  install_dir: pythondir / 'markdown_vault',
  preserve_path: true,
)

# Single source of truth for the version: meson.build project(version:). Generated
# into the installed package so the app (About dialog) can read it — never hand-edited.
configure_file(
  input: '_version.py.in',
  output: '_version.py',
  configuration: {'VERSION': meson.project_version()},
  install: true,
  install_dir: pythondir / 'markdown_vault',
)

install_data(
  '../css/style.css',
  '../css/gtk.css',
  install_dir: pythondir / 'markdown_vault' / 'css',
)
