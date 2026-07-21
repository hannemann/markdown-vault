launcher_conf = configuration_data()
launcher_conf.set('python', py.full_path())
launcher_conf.set('pythondir', pythondir)

configure_file(
  input: 'markdown-vault.in',
  output: 'markdown-vault',
  configuration: launcher_conf,
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'rwxr-xr-x',
)
