"""Tests for markdown_vault.core.vault_fs — the guarded chokepoint for writes INSIDE a
vault (note rewrites, attachments, renames, deletes).

The mirror of StateFS: one positive clause (the target must stay inside a configured
vault), but the two path_guard modes matter here — a write/move-destination follows a
symlink to where it lands, a delete/rename/move-source acts on the link itself. Real
on-disk symlinks exercise both. Ops are patched onto a temp vault via _vault_roots.
"""

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from markdown_vault.core import vault_fs as vfs


class _Vault:
    """Context manager: a temp vault patched into vault_fs, plus an outside temp dir."""

    def __init__(self):
        self._vault = TemporaryDirectory()
        self._outside = TemporaryDirectory()

    def __enter__(self):
        self.vault = self._vault.__enter__()
        self.outside = self._outside.__enter__()
        self._p = mock.patch.object(vfs, "_vault_roots", return_value=[self.vault])
        self._p.start()
        return self

    def __exit__(self, *a):
        self._p.stop()
        self._vault.__exit__(*a)
        self._outside.__exit__(*a)


class TestWriteOps(unittest.TestCase):
    def test_write_text_inside_the_vault(self):
        with _Vault() as v:
            p = os.path.join(v.vault, "note.md")
            vfs.write_text(p, "# hi")
            self.assertEqual(Path(p).read_text(encoding="utf-8"), "# hi")

    def test_write_text_outside_is_refused(self):
        with _Vault() as v:
            with self.assertRaises(vfs.OutsideVault):
                vfs.write_text(os.path.join(v.outside, "note.md"), "x")

    def test_write_bytes_inside(self):
        with _Vault() as v:
            p = os.path.join(v.vault, "img.png")
            vfs.write_bytes(p, b"\x89PNG")
            self.assertEqual(Path(p).read_bytes(), b"\x89PNG")

    def test_write_through_a_leaf_symlink_pointing_out_is_refused(self):
        # follow_last=True: the write would land where the link points (outside), so refuse.
        with _Vault() as v:
            link = os.path.join(v.vault, "escape")
            os.symlink(os.path.join(v.outside, "victim"), link)
            with self.assertRaises(vfs.OutsideVault):
                vfs.write_text(link, "x")
            self.assertFalse(os.path.exists(os.path.join(v.outside, "victim")))

    def test_mkdir_inside(self):
        with _Vault() as v:
            d = os.path.join(v.vault, "attachments", "sub")
            vfs.mkdir(d, parents=True, exist_ok=True)
            self.assertTrue(Path(d).is_dir())

    def test_mkdir_outside_is_refused(self):
        with _Vault() as v:
            with self.assertRaises(vfs.OutsideVault):
                vfs.mkdir(os.path.join(v.outside, "d"))

    def test_touch_creates_an_empty_file_inside(self):
        with _Vault() as v:
            p = os.path.join(v.vault, "new.md")
            vfs.touch(p)
            self.assertTrue(Path(p).is_file())
            self.assertEqual(Path(p).read_text(), "")

    def test_touch_outside_is_refused(self):
        with _Vault() as v:
            with self.assertRaises(vfs.OutsideVault):
                vfs.touch(os.path.join(v.outside, "new.md"))

    def test_touch_does_not_truncate_an_existing_file(self):
        # The reason touch is its own op, not write_text(""): create_file has no existence
        # check, so an existing name must keep its content (touch only bumps mtime).
        with _Vault() as v:
            p = os.path.join(v.vault, "note.md")
            Path(p).write_text("keep me")
            vfs.touch(p)
            self.assertEqual(Path(p).read_text(), "keep me")


class TestAtomicWrites(unittest.TestCase):
    def test_write_text_atomic_writes_and_leaves_no_part(self):
        with _Vault() as v:
            p = os.path.join(v.vault, "note.md")
            vfs.write_text_atomic(p, "# hi")
            self.assertEqual(Path(p).read_text(encoding="utf-8"), "# hi")
            self.assertFalse(Path(p + ".part").exists())

    def test_write_text_atomic_outside_is_refused(self):
        with _Vault() as v:
            with self.assertRaises(vfs.OutsideVault):
                vfs.write_text_atomic(os.path.join(v.outside, "note.md"), "x")

    def test_a_failed_atomic_write_leaves_the_previous_file_intact(self):
        # The whole point: os.replace failing (or a crash before it) must not destroy the
        # existing note — unlike a direct write_text, which truncates first.
        with _Vault() as v:
            p = os.path.join(v.vault, "note.md")
            Path(p).write_text("PRECIOUS")
            with mock.patch("markdown_vault.core.vault_fs.os.replace",
                            side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    vfs.write_text_atomic(p, "new content")
            self.assertEqual(Path(p).read_text(), "PRECIOUS")   # old content survives
            self.assertFalse(Path(p + ".part").exists())        # partial cleaned up

    def test_atomic_write_through_a_symlinked_note_keeps_the_link(self):
        # AP1: a direct write goes THROUGH a symlink (target updated, link survives). The
        # atomic writer must match — otherwise migrating a batch caller (the backlink
        # rewrite is one) would replace a symlinked note with a file and leave the real
        # note stale. os.replace renames onto the NAME, so resolve the leaf after the guard.
        with _Vault() as v:
            real = os.path.join(v.vault, "real.md")
            Path(real).write_text("ORIGINAL")
            link = os.path.join(v.vault, "alias.md")
            os.symlink(real, link)
            vfs.write_text_atomic(link, "REWRITTEN")
            self.assertTrue(os.path.islink(link))                  # link preserved
            self.assertEqual(Path(real).read_text(), "REWRITTEN")  # target updated through it

    def test_atomic_write_through_a_symlink_pointing_out_is_still_refused(self):
        # Resolving the leaf must not weaken the guard: a link pointing outside is still
        # caught (the guard runs on the original path, follow_last=True, before the write).
        with _Vault() as v:
            link = os.path.join(v.vault, "escape.md")
            os.symlink(os.path.join(v.outside, "victim.md"), link)
            with self.assertRaises(vfs.OutsideVault):
                vfs.write_text_atomic(link, "x")
            self.assertFalse(os.path.exists(os.path.join(v.outside, "victim.md")))

    def test_write_bytes_atomic(self):
        with _Vault() as v:
            p = os.path.join(v.vault, "attachments", "img.png")
            vfs.write_bytes_atomic(p, b"\x89PNG")               # parent created
            self.assertEqual(Path(p).read_bytes(), b"\x89PNG")
            self.assertFalse(Path(p + ".part").exists())


class TestDeleteOps(unittest.TestCase):
    def test_unlink_a_file_inside(self):
        with _Vault() as v:
            p = os.path.join(v.vault, "note.md")
            Path(p).write_text("x")
            vfs.unlink(p)
            self.assertFalse(Path(p).exists())

    def test_unlink_a_leaf_symlink_pointing_out_is_allowed(self):
        # follow_last=False: deleting the link acts on the link (inside the vault), not on
        # its target outside — the victim survives, the link is gone.
        with _Vault() as v:
            victim = os.path.join(v.outside, "victim")
            Path(victim).write_text("precious")
            link = os.path.join(v.vault, "alias")
            os.symlink(victim, link)
            vfs.unlink(link)
            self.assertFalse(os.path.lexists(link))
            self.assertEqual(Path(victim).read_text(), "precious")

    def test_unlink_outside_is_refused(self):
        with _Vault() as v:
            p = os.path.join(v.outside, "f")
            Path(p).write_text("x")
            with self.assertRaises(vfs.OutsideVault):
                vfs.unlink(p)
            self.assertTrue(Path(p).exists())

    def test_rmdir_inside(self):
        with _Vault() as v:
            d = os.path.join(v.vault, "empty")
            os.mkdir(d)
            vfs.rmdir(d)
            self.assertFalse(Path(d).exists())

    def test_rmtree_inside(self):
        with _Vault() as v:
            d = os.path.join(v.vault, "tree")
            os.makedirs(os.path.join(d, "a"))
            Path(d, "a", "f").write_text("x")
            vfs.rmtree(d)
            self.assertFalse(Path(d).exists())

    def test_rmtree_outside_is_refused(self):
        with _Vault() as v:
            d = os.path.join(v.outside, "tree")
            os.mkdir(d)
            with self.assertRaises(vfs.OutsideVault):
                vfs.rmtree(d)
            self.assertTrue(Path(d).exists())


class TestMoveRename(unittest.TestCase):
    def test_rename_within_the_vault(self):
        with _Vault() as v:
            src = os.path.join(v.vault, "a.md")
            dst = os.path.join(v.vault, "b.md")
            Path(src).write_text("x")
            vfs.rename(src, dst)
            self.assertFalse(Path(src).exists())
            self.assertEqual(Path(dst).read_text(), "x")

    def test_rename_destination_outside_is_refused(self):
        with _Vault() as v:
            src = os.path.join(v.vault, "a.md")
            Path(src).write_text("x")
            with self.assertRaises(vfs.OutsideVault):
                vfs.rename(src, os.path.join(v.outside, "a.md"))
            self.assertTrue(Path(src).exists())        # source untouched on refusal

    def test_rename_source_outside_is_refused(self):
        with _Vault() as v:
            src = os.path.join(v.outside, "a.md")
            Path(src).write_text("x")
            with self.assertRaises(vfs.OutsideVault):
                vfs.rename(src, os.path.join(v.vault, "a.md"))

    def test_move_into_the_vault_from_a_vault_source(self):
        with _Vault() as v:
            src = os.path.join(v.vault, "a.md")
            Path(src).write_text("x")
            subdir = os.path.join(v.vault, "sub")
            os.mkdir(subdir)
            vfs.move(src, subdir)
            self.assertTrue(Path(subdir, "a.md").exists())

    def test_move_destination_outside_is_refused(self):
        with _Vault() as v:
            src = os.path.join(v.vault, "a.md")
            Path(src).write_text("x")
            with self.assertRaises(vfs.OutsideVault):
                vfs.move(src, os.path.join(v.outside, "a.md"))
            self.assertTrue(Path(src).exists())

    def test_move_final_path_escaping_via_symlink_is_refused(self):
        # AO1: shutil.move lands the file at dst/basename(src). If THAT path is a symlink
        # pointing out, guarding only the dst directory admits an escape (reachable when a
        # vault spans mount points, where shutil copies through the link). Guard the final
        # resting path, not just the directory.
        with _Vault() as v:
            src = os.path.join(v.vault, "note.md")
            Path(src).write_text("secret")
            subdir = os.path.join(v.vault, "sub")
            os.mkdir(subdir)
            os.symlink(os.path.join(v.outside, "victim.md"),
                       os.path.join(subdir, "note.md"))   # the trap
            with self.assertRaises(vfs.OutsideVault):
                vfs.move(src, subdir)
            self.assertTrue(Path(src).exists())            # refused before moving

    def test_rename_over_a_symlink_pointing_out_stays_inside(self):
        # AO2: os.rename REPLACES a symlink at the destination, it does not write through
        # it, so a dst that is a symlink pointing out still stays inside. Refusing it
        # (follow_last=True) mirrors the AG2 over-strictness; it must be allowed, exactly
        # as unlink of that same link is.
        with _Vault() as v:
            src = os.path.join(v.vault, "a.md")
            Path(src).write_text("NEW")
            victim = os.path.join(v.outside, "victim.md")
            Path(victim).write_text("VICTIM")
            dst = os.path.join(v.vault, "link.md")
            os.symlink(victim, dst)
            vfs.rename(src, dst)
            self.assertFalse(os.path.islink(dst))               # link replaced by the file
            self.assertEqual(Path(dst).read_text(), "NEW")
            self.assertEqual(Path(victim).read_text(), "VICTIM")  # target untouched


if __name__ == "__main__":
    unittest.main()
