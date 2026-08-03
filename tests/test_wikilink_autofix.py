"""Unit tests for the pure wikilink-autofix logic.

All vault knowledge is injected as fake ``resolve`` / ``find_candidates``
callables, so these tests need no config, filesystem, or GTK.
"""

import unittest
from pathlib import Path

from markdown_vault.wikilink_autofix import (
    analyze_text,
    apply_fixes,
    find_broken_ranges,
)


def make_resolve(existing):
    """Return a resolve() that succeeds for (vault, stem) pairs in *existing*."""
    def resolve(info):
        key = (info.vault, info.stem.strip())
        return "/abs/target.md" if key in existing else None
    return resolve


def make_candidates(mapping):
    """Return a find_candidates() backed by {basename_lower: [(vault, rel)]}."""
    return lambda basename: mapping.get(basename.lower(), [])


def analyze(text, *, existing=frozenset(), candidates=None, source_vault="V",
            normalize=False, relink=False):
    return analyze_text(
        text, "/vault/note.md",
        source_vault=source_vault,
        resolve=make_resolve(existing),
        find_candidates=make_candidates(candidates or {}),
        normalize=normalize,
        relink=relink,
    )


class TestNormalize(unittest.TestCase):
    def test_trims_whitespace_on_resolvable_link(self):
        fixes, broken = analyze(
            "see [[ Foo ]] here", existing={(None, "Foo")}, normalize=True,
        )
        self.assertEqual(broken, [])
        self.assertEqual(len(fixes), 1)
        self.assertEqual(fixes[0].new, "[[Foo]]")
        self.assertEqual(fixes[0].kind, "normalize")

    def test_no_fix_when_normalize_disabled(self):
        fixes, broken = analyze(
            "[[ Foo ]]", existing={(None, "Foo")}, normalize=False,
        )
        self.assertEqual(fixes, [])
        self.assertEqual(broken, [])

    def test_clean_link_produces_no_fix(self):
        fixes, _ = analyze("[[Foo]]", existing={(None, "Foo")}, normalize=True)
        self.assertEqual(fixes, [])

    def test_normalize_preserves_alias_and_vault(self):
        # Vault prefix must be tight ("W>"); alias whitespace is trimmed.
        fixes, broken = analyze(
            "[[W>sub/Foo| Bar ]]", existing={("W", "sub/Foo")},
            normalize=True,
        )
        self.assertEqual(broken, [])
        self.assertEqual(len(fixes), 1)
        self.assertEqual(fixes[0].new, "[[W>sub/Foo|Bar]]")


class TestRelink(unittest.TestCase):
    def test_unique_candidate_relinks_moved_file(self):
        fixes, broken = analyze(
            "[[projects/foo]]",
            candidates={"foo": [("V", "inbox/foo")]},
            relink=True,
        )
        self.assertEqual(broken, [])
        self.assertEqual(len(fixes), 1)
        self.assertEqual(fixes[0].new, "[[inbox/foo]]")
        self.assertEqual(fixes[0].kind, "relink")

    def test_zero_candidates_stays_broken(self):
        fixes, broken = analyze(
            "[[projects/foo]]", candidates={}, relink=True,
        )
        self.assertEqual(fixes, [])
        self.assertEqual(len(broken), 1)
        self.assertEqual(broken[0].stem, "projects/foo")

    def test_multiple_candidates_stays_broken(self):
        fixes, broken = analyze(
            "[[foo]]",
            candidates={"foo": [("V", "a/foo"), ("V", "b/foo")]},
            relink=True,
        )
        self.assertEqual(fixes, [])
        self.assertEqual(len(broken), 1)

    def test_relink_off_reports_broken(self):
        fixes, broken = analyze(
            "[[projects/foo]]",
            candidates={"foo": [("V", "inbox/foo")]},
            relink=False,
        )
        self.assertEqual(fixes, [])
        self.assertEqual(len(broken), 1)

    def test_cross_vault_candidate_emits_qualified_link(self):
        fixes, _ = analyze(
            "[[foo]]",
            candidates={"foo": [("W", "note")]},
            source_vault="V",
            relink=True,
        )
        self.assertEqual(fixes[0].new, "[[W>note]]")

    def test_originally_qualified_link_stays_qualified(self):
        fixes, _ = analyze(
            "[[W>foo]]",
            candidates={"foo": [("W", "sub/foo")]},
            source_vault="V",
            relink=True,
        )
        self.assertEqual(fixes[0].new, "[[W>sub/foo]]")

    def test_alias_preserved_on_relink(self):
        fixes, _ = analyze(
            "[[foo|Bar]]",
            candidates={"foo": [("V", "x/foo")]},
            source_vault="V",
            relink=True,
        )
        self.assertEqual(fixes[0].new, "[[x/foo|Bar]]")

    def test_casing_repaired_via_unique_candidate(self):
        # [[foo]] does not resolve (case-sensitive), unique candidate is Foo.
        fixes, broken = analyze(
            "[[foo]]",
            candidates={"foo": [("V", "Foo")]},
            source_vault="V",
            relink=True,
        )
        self.assertEqual(broken, [])
        self.assertEqual(fixes[0].new, "[[Foo]]")


class TestBrokenDetection(unittest.TestCase):
    def test_find_broken_ranges_offsets(self):
        text = "ok [[good]] bad [[missing]] end"
        resolve = make_resolve({(None, "good")})
        ranges = find_broken_ranges(text, resolve)
        self.assertEqual(len(ranges), 1)
        s, e = ranges[0]
        self.assertEqual(text[s:e], "[[missing]]")

    def test_resolvable_links_have_no_ranges(self):
        resolve = make_resolve({(None, "a"), (None, "b")})
        self.assertEqual(find_broken_ranges("[[a]] [[b]]", resolve), [])

    def test_empty_stem_is_ignored(self):
        resolve = make_resolve(set())
        self.assertEqual(find_broken_ranges("[[]] [[   ]]", resolve), [])


class TestApplyFixes(unittest.TestCase):
    def test_apply_multiple_fixes_right_to_left(self):
        text = "[[a]] mid [[b]]"
        fixes, _ = analyze(
            text,
            candidates={"a": [("V", "x/a")], "b": [("V", "y/b")]},
            source_vault="V",
            relink=True,
        )
        result = apply_fixes(text, fixes)
        self.assertEqual(result, "[[x/a]] mid [[y/b]]")

    def test_apply_no_fixes_is_identity(self):
        self.assertEqual(apply_fixes("hello", []), "hello")


class TestWikilinkResolverFragment(unittest.TestCase):
    """R21.2: heading-anchor links must not be classified broken."""

    def setUp(self):
        import tempfile
        import markdown_vault.config as cfg
        self._cfg = cfg
        self._tmp = tempfile.mkdtemp()
        self._vault = Path(self._tmp) / "V"
        self._vault.mkdir()
        (self._vault / "Note.md").write_text("# Note\n")
        cfg._vaults_cache = [{"name": "V", "path": str(self._vault)}]

    def tearDown(self):
        import shutil
        self._cfg._vaults_cache = None
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _info(self, stem):
        from markdown_vault.tags import WikilinkInfo
        return WikilinkInfo(raw=stem, stem=stem, vault=None, alias=None, display=stem)

    def _resolver(self):
        from markdown_vault.wikilink_autofix import WikilinkResolver
        return WikilinkResolver()

    def test_heading_anchor_on_existing_file_resolves(self):
        src = str(self._vault / "Other.md")
        self.assertIsNotNone(
            self._resolver().resolve(self._info("Note#Section"), src))

    def test_same_file_anchor_resolves_to_source(self):
        src = str(self._vault / "Note.md")
        self.assertEqual(
            self._resolver().resolve(self._info("#Heading"), src), src)

    def test_missing_target_with_anchor_is_broken(self):
        src = str(self._vault / "Note.md")
        self.assertIsNone(
            self._resolver().resolve(self._info("Nope#Section"), src))


if __name__ == "__main__":
    unittest.main()
