# Extracts messages into a .pot (target `<app-id>-pot`), compiles each LINGUAS .po
# into a .mo and installs it to <datadir>/locale/<lang>/LC_MESSAGES/<app-id>.mo — where
# core/i18n.py binds the textdomain. Keywords cover the project's markers: _() and the
# 1st/2nd argument of ngettext().
i18n.gettext(
  app_id,
  args: [
    '--keyword=_',
    '--keyword=ngettext:1,2',
    '--from-code=UTF-8',
    '--add-comments=TRANSLATORS:',
  ],
)
