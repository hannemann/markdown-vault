"""Version stays single-sourced. meson.build `project(version:)` is the source of
truth (generated into `_version.py` at build time); the AppStream metainfo's latest
`<release>` must match it, so a bump can't silently drift them apart."""

import re
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_METAINFO = _ROOT / "src/share/markdown-vault/de.hannemann.markdown-vault.metainfo.xml"


def _meson_version() -> str:
    # `\bversion:` avoids matching `meson_version:` (no boundary before "version" there).
    m = re.search(r"\bversion:\s*'([^']+)'", (_ROOT / "meson.build").read_text())
    return m.group(1) if m else ""


def _metainfo_latest_release() -> str:
    versions = re.findall(r'<release\s+version="([^"]+)"', _METAINFO.read_text())
    return versions[0] if versions else ""


class TestVersion(unittest.TestCase):
    def test_meson_and_metainfo_versions_match(self):
        mv = _meson_version()
        self.assertTrue(mv, "no project(version:) found in meson.build")
        self.assertEqual(mv, _metainfo_latest_release())

    def test_version_template_defines_version(self):
        tpl = (_ROOT / "src/markdown_vault/_version.py.in").read_text()
        self.assertIn("__version__", tpl)
        self.assertIn("@VERSION@", tpl)


if __name__ == "__main__":
    unittest.main()
