py_sources = files(
  '__init__.py',
  '__main__.py',
  'app_window.py',
  'backlink_index.py',
  'dialog_import.py',
  'document_import.py',
  'file_index.py',
  'file_ops.py',
  'file_manager.py',
  'git_integration.py',
   'input_manager.py',
   'llama_runtime.py',
   'main.py',
  'monitor_handler.py',
  'note_writer.py',
  'preferences.py',
  'quick_open.py',
  'ask.py',
  'lexical_search.py',
  'quick_open_palette.py',
  'search.py',
  'search_backend.py',
  'search_logic.py',
  'semantic_index.py',
  'semantic_search.py',
  'session_manager.py',
  'sidebar.py',
  'status_bar.py',
  'tab_manager.py',
   'view_mode_manager.py',
   'vault_monitor.py',
  'vault_scope.py',
  'vault_tree.py',
  'web_import.py',
  'wikilink_autofix.py',
)

subdir('core')
subdir('editor')
subdir('graph')
subdir('markdown')
subdir('preview')
subdir('uikit')

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
