"""Tests for markdown_vault.core.path_guard — the realpath-based containment primitive
the VaultFS / StateFS facades stand on.

The point of these over the lexical path_utils.path_is_within is that they resolve
symlinks, so a crafted link cannot smuggle a write out of its allowed tree. Real symlinks
are created on disk (tempfile lands under ./tmp via the Makefile's TMPDIR pin) — a mock
would not exercise the realpath resolution that is the whole security property.
"""

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from markdown_vault.core import path_guard as pg


class TestWithinAny(unittest.TestCase):
    def test_target_inside_a_root(self):
        with TemporaryDirectory() as d:
            self.assertTrue(pg.within_any([d], os.path.join(d, "sub", "x"), follow_last=True))

    def test_target_equal_to_a_root(self):
        with TemporaryDirectory() as d:
            self.assertTrue(pg.within_any([d], d, follow_last=True))

    def test_target_outside_every_root(self):
        with TemporaryDirectory() as a, TemporaryDirectory() as b:
            self.assertFalse(pg.within_any([a], os.path.join(b, "x"), follow_last=True))

    def test_prefix_sibling_is_not_containment(self):
        # /…/vault must not contain /…/vault-other — the classic startswith bug.
        with TemporaryDirectory() as d:
            root = os.path.join(d, "vault")
            os.mkdir(root)
            sibling = os.path.join(d, "vault-other")
            os.mkdir(sibling)
            self.assertFalse(pg.within_any([root], os.path.join(sibling, "x"),
                                           follow_last=True))

    def test_empty_roots(self):
        with TemporaryDirectory() as d:
            self.assertFalse(pg.within_any([], os.path.join(d, "x"), follow_last=True))

    def test_matches_the_second_root(self):
        with TemporaryDirectory() as a, TemporaryDirectory() as b:
            self.assertTrue(pg.within_any([a, b], os.path.join(b, "x"), follow_last=True))


class TestSymlinkResolution(unittest.TestCase):
    def test_write_through_a_leaf_symlink_pointing_out_escapes(self):
        # follow_last=True (a write / move-destination): the link is resolved, the target
        # lands OUTSIDE the root, so containment must be False — this is the escape.
        with TemporaryDirectory() as root, TemporaryDirectory() as outside:
            link = os.path.join(root, "escape")
            os.symlink(os.path.join(outside, "victim"), link)
            self.assertFalse(pg.within_any([root], link, follow_last=True))

    def test_leaf_symlink_pointing_inside_is_contained(self):
        with TemporaryDirectory() as root:
            inside = os.path.join(root, "real")
            Path(inside).write_text("x")
            link = os.path.join(root, "alias")
            os.symlink(inside, link)
            self.assertTrue(pg.within_any([root], link, follow_last=True))

    def test_deleting_a_leaf_symlink_pointing_out_stays_inside(self):
        # follow_last=False (a delete / rename / move-source): the last component is kept
        # literal, only the parent is resolved. Deleting the link acts on the link itself,
        # which lives inside the root — resolving it would be the AG2 regression.
        with TemporaryDirectory() as root, TemporaryDirectory() as outside:
            link = os.path.join(root, "escape")
            os.symlink(os.path.join(outside, "victim"), link)
            self.assertTrue(pg.within_any([root], link, follow_last=False))

    def test_dotdot_behind_a_dir_symlink_escapes_in_delete_mode(self):
        # AL1: '..' anywhere behind a directory symlink. abspath() would collapse
        # vault/dirlink/../x.md to vault/x.md lexically, before realpath sees dirlink ->
        # outside; the operation actually lands on outside/x.md. follow_last=False must
        # resolve the *original* dirname (dirlink), not the lexically-collapsed one.
        with TemporaryDirectory() as root, TemporaryDirectory() as outside:
            deep = os.path.join(outside, "deep")
            os.mkdir(deep)
            os.symlink(deep, os.path.join(root, "dirlink"))
            target = os.path.join(root, "dirlink", "..", "x.md")
            self.assertFalse(pg.within_any([root], target, follow_last=False))

    def test_dotdot_as_leaf_behind_a_dir_symlink_escapes_in_delete_mode(self):
        with TemporaryDirectory() as root, TemporaryDirectory() as outside:
            deep = os.path.join(outside, "deep")
            os.mkdir(deep)
            os.symlink(deep, os.path.join(root, "dirlink"))
            target = os.path.join(root, "dirlink", "..")
            self.assertFalse(pg.within_any([root], target, follow_last=False))

    def test_intermediate_symlink_out_escapes_in_both_modes(self):
        # A directory component (not the leaf) points out: both modes resolve the parent,
        # so both see the escape.
        with TemporaryDirectory() as root, TemporaryDirectory() as outside:
            os.mkdir(os.path.join(outside, "sub"))
            os.symlink(os.path.join(outside, "sub"), os.path.join(root, "link"))
            target = os.path.join(root, "link", "file")
            self.assertFalse(pg.within_any([root], target, follow_last=True))
            self.assertFalse(pg.within_any([root], target, follow_last=False))


class TestCheckedRoot(unittest.TestCase):
    def test_rejects_empty(self):
        with self.assertRaises(pg.InvalidRoot):
            pg.checked_root("")

    def test_rejects_filesystem_root(self):
        with self.assertRaises(pg.InvalidRoot):
            pg.checked_root("/")

    def test_rejects_relative(self):
        with self.assertRaises(pg.InvalidRoot):
            pg.checked_root("some/relative/dir")

    def test_rejects_a_path_resolving_to_the_filesystem_root(self):
        with self.assertRaises(pg.InvalidRoot):
            pg.checked_root("/..")

    def test_accepts_a_valid_absolute_dir(self):
        with TemporaryDirectory() as d:
            self.assertEqual(pg.checked_root(d), d)


if __name__ == "__main__":
    unittest.main()
