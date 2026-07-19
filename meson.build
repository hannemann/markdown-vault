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

subdir('src/bin')
subdir('src/lib/python3.13/site-packages')
subdir('src/share/markdown-vault')

gnome.post_install(
  update_desktop_database: true,
)
