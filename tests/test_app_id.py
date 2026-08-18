"""Guard: every place that names the application ID names the *same* one.

The id is not a Python constant everyone can import — it appears in Python, Meson,
Make and shell, so the places cannot share a definition. They can be compared.

This is the check that would have caught the `post_uninstall.py` near-miss during the
app-id rename: that file assembles its path from separate string parts
(`os.path.join(prefix, 'share', '<id>', 'python', …)`), so it matched no text search
for the old name and would have cleaned a directory that no longer exists.

A mismatch here is silent in production, which is why it is worth a test: the uninstall
script cleans nothing, `app.sh` writes its stderr log beside the real state directory,
an `install_dir` puts the gresource somewhere the app never looks.
"""

import re
import unittest
from pathlib import Path

from markdown_vault.core import paths

_ROOT = Path(__file__).resolve().parents[1]

#: (file, regex capturing the id). Add a row when a new place names it — and if a row
#: stops matching, the test fails loudly rather than skipping the site.
SITES = [
    ("meson.build", r"app_id\s*=\s*'([^']+)'"),
    ("Makefile", r"APP_ID\s*:=\s*(\S+)"),
    ("scripts/app.sh", r"\.local/state/([^/]+)/"),
    ("build-aux/meson/post_uninstall.py", r"'(de\.[^']+)'"),
    ("src/markdown_vault/core/paths.py", r'_APP\s*=\s*"([^"]+)"'),
    ("src/markdown_vault/main.py", r'application_id="([^"]+)"'),
]


def _extract(text: str, pattern: str) -> str | None:
    """The captured id, or None when the pattern no longer matches."""
    match = re.search(pattern, text)
    return match.group(1) if match else None


class TestTheAppIdIsOneString(unittest.TestCase):
    def test_every_site_names_the_same_id(self):
        found = {}
        for rel, pattern in SITES:
            text = (_ROOT / rel).read_text(encoding="utf-8")
            value = _extract(text, pattern)
            self.assertIsNotNone(value, f"{rel}: no id found — pattern stale?")
            found[rel] = value
        # One set, one element: reporting the whole dict names the odd one out.
        self.assertEqual(set(found.values()), {paths._APP}, found)

    def test_no_install_dir_spells_out_an_id_instead_of_using_the_variable(self):
        # Two targets go into shared directories ('applications', 'metainfo') and must
        # NOT use app_id; the app's own two must not spell any id out. So the rule is
        # not "every line uses the variable" but "no line names a reverse-DNS id" —
        # that catches a *wrong* literal too, which asserting against the current id
        # alone does not (a typo'd id does not contain paths._APP and slips through,
        # while installing the gresource where the app never looks).
        share = (_ROOT / "src/share/markdown-vault/meson.build").read_text()
        targets = [ln for ln in share.splitlines() if "install_dir:" in ln]
        self.assertTrue(any("app_id" in ln for ln in targets), targets)
        for line in targets:
            spelled = re.search(r"['\"](\w+(?:\.\w[\w-]*)+)['\"]", line)
            self.assertIsNone(
                spelled,
                f"install_dir names an id literally instead of app_id: {line.strip()}")


class TestTheExtractionItself(unittest.TestCase):
    """The guard is only worth its salt if a wrong id actually shows up."""

    def test_a_differing_site_is_visible(self):
        pattern = SITES[0][1]
        self.assertEqual(_extract("app_id = 'de.hannemann.markdown-vault'", pattern),
                         "de.hannemann.markdown-vault")
        self.assertEqual(_extract("app_id = 'de.other.app'", pattern), "de.other.app")

    def test_a_stale_pattern_yields_none_instead_of_a_false_pass(self):
        self.assertIsNone(_extract("pkgdatadir = 'whatever'", SITES[0][1]))


if __name__ == "__main__":
    unittest.main()
