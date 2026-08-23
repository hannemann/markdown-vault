"""Runtime gettext binding for user-facing strings.

Exposes ``_`` (singular) and ``ngettext`` (plural), bound to the app's message
catalog for the OS UI language. The source strings in the code are English and
ARE the message keys (msgids); when no catalog matches the OS locale, gettext
returns the msgid, so the app renders in **English** — never blank, never an
error. English is therefore the structural fallback; a shipped translation
(e.g. German) is used only when its ``.mo`` is present and the locale selects it.

Import this and use it explicitly per module::

    from markdown_vault.core.i18n import _, ngettext

Explicit import (not ``gettext.install``'s global ``_``) keeps it testable and
makes a module's participation in translation visible.
"""
import gettext as _gettext
from pathlib import Path

DOMAIN = "de.hannemann.markdown-vault"


def _localedir() -> str:
    """The installed ``<datadir>/locale`` directory.

    Installed layout is ``<datadir>/<app-id>/python/markdown_vault/core/i18n.py``,
    so ``<datadir>/locale`` sits four parents above the package root; the same
    relative shape holds under Flatpak (``/app/share/…``). A wrong path only loses
    translation, never text — gettext falls back to the English msgid.
    """
    return str(Path(__file__).resolve().parents[4] / "locale")


# Reads the OS locale from the environment (LANGUAGE/LC_ALL/LC_MESSAGES/LANG) at
# import time; fallback=True yields a NullTranslations (msgid == English) when the
# catalog is absent, so importing is always safe even uninstalled or under LC_ALL=C.
_translation = _gettext.translation(DOMAIN, _localedir(), fallback=True)

_ = _translation.gettext
ngettext = _translation.ngettext
