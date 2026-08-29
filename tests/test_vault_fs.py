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


class TestAtomicSavePaths(unittest.TestCase):
    """The atomic writer renames <target>.part onto <target>. The vault monitor has to
    announce that exact pair BEFORE the save so it can recognise the resulting rename as our
    own — so the two names must come from ONE place. A second guess at the call site would
    drift from the writer (notably on a symlinked note, where the writer resolves the leaf)
    and the announced pair would silently never match."""

    def test_the_pair_is_what_the_writer_actually_renames(self):
        # Coupling test: capture os.replace's real arguments and compare. A helper that
        # merely *looks* right but disagrees with the writer fails here.
        with _Vault() as v:
            p = os.path.join(v.vault, "note.md")
            with mock.patch("markdown_vault.core.vault_fs.os.replace") as rep:
                vfs.write_text_atomic(p, "x")
            self.assertEqual(vfs.atomic_save_paths(p), tuple(str(a) for a in rep.call_args[0]))

    def test_the_pair_resolves_a_symlinked_note_like_the_writer(self):
        # AP1: the writer writes THROUGH a symlink (renames onto the real file), so the pair
        # must name the real file too — otherwise the announcement misses and a save raises a
        # false "changed externally" banner.
        with _Vault() as v:
            real = os.path.join(v.vault, "real.md")
            Path(real).write_text("x")
            link = os.path.join(v.vault, "alias.md")
            os.symlink(real, link)
            part, target = vfs.atomic_save_paths(link)
            self.assertEqual(target, os.path.realpath(link))
            # The temp file sits beside the REAL note, not the link. Asserted on the prefix
            # so this test stays about resolution and does not re-pin the name's shape.
            self.assertTrue(part.startswith(os.path.realpath(link) + "."))
            self.assertNotIn("alias", part)

    def test_the_part_name_carries_the_process_id(self):
        # BC1: the pair is computed independently by announcer and writer, so it must stay
        # derivable — but derivable must not mean GUESSABLE. ".part" is a common convention
        # (wget, browsers, sync clients), so a plain "<note>.md.part" is a name an external
        # tool can produce, and its atomic save would be indistinguishable from ours and get
        # swallowed as our own. The pid stays derivable inside the process and is effectively
        # unguessable outside it, without threading the pair through the writer's signature.
        with _Vault() as v:
            part, _target = vfs.atomic_save_paths(os.path.join(v.vault, "note.md"))
            self.assertIn(str(os.getpid()), part)

    def test_the_part_name_does_not_end_in_md(self):
        # Load-bearing: VaultMonitor only looks at .md files, so the temp file must stay
        # invisible to it. A "<name>.part.md" form would raise an extra created event.
        with _Vault() as v:
            part, _target = vfs.atomic_save_paths(os.path.join(v.vault, "note.md"))
            self.assertFalse(part.endswith(".md"))


class TestStalePartSweep(unittest.TestCase):
    """BD2: the pid in the temp name stopped orphans from self-healing. With the old fixed
    name a crash-orphan was truncated and renamed away by the next save of that note; a
    later process has a different pid and never touches it, so they accumulate per (note,
    process) — visible in the git panel (untracked) and carried into Nextcloud sync.

    The sweep must not take a CONCURRENT instance's in-flight temp with it, which is why it
    goes by whether the owning process still runs rather than by age. Unknown state keeps the
    file: leaving an orphan is recoverable, deleting a live write is not."""

    def test_an_orphan_from_a_dead_process_is_removed(self):
        with _Vault() as v:
            note = os.path.join(v.vault, "note.md")
            orphan = Path(f"{note}.999999.part")
            orphan.write_text("half-written")
            with mock.patch("markdown_vault.core.vault_fs._pid_alive", return_value=False):
                vfs.write_text_atomic(note, "new")
            self.assertFalse(orphan.exists())
            self.assertEqual(Path(note).read_text(), "new")

    def test_an_orphan_from_a_live_process_is_kept(self):
        # A second instance saving the same note right now. Deleting its temp would corrupt
        # that write; AGENTS.md notes duplicate instances do happen.
        with _Vault() as v:
            note = os.path.join(v.vault, "note.md")
            in_flight = Path(f"{note}.999999.part")
            in_flight.write_text("other instance writing")
            with mock.patch("markdown_vault.core.vault_fs._pid_alive", return_value=True):
                vfs.write_text_atomic(note, "new")
            self.assertTrue(in_flight.exists())

    def test_another_notes_orphan_is_untouched(self):
        with _Vault() as v:
            note = os.path.join(v.vault, "note.md")
            other = Path(os.path.join(v.vault, "other.md.999999.part"))
            other.write_text("not mine")
            with mock.patch("markdown_vault.core.vault_fs._pid_alive", return_value=False):
                vfs.write_text_atomic(note, "new")
            self.assertTrue(other.exists())

    def test_a_sweep_failure_does_not_fail_the_save(self):
        # Cleanup is opportunistic; the write is what matters and must still happen.
        with _Vault() as v:
            note = os.path.join(v.vault, "note.md")
            with mock.patch("markdown_vault.core.vault_fs._sweep_stale_parts",
                            side_effect=OSError("cannot list")):
                vfs.write_text_atomic(note, "new")
            self.assertEqual(Path(note).read_text(), "new")

    def test_pid_alive_reports_this_process(self):
        self.assertTrue(vfs._pid_alive(os.getpid()))


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

    def test_rmtree_forwards_ignore_errors_true(self):
        # AZ1: a best-effort caller (attachments cleanup) relies on shutil clearing everything
        # removable and leaving only the stubborn part, instead of aborting on the first error
        # (which would orphan the whole tree). The flag must reach shutil; the guard already ran
        # on the top path, so it only affects error handling inside the walk, never containment.
        with _Vault() as v:
            d = os.path.join(v.vault, "tree")
            os.makedirs(d)
            with mock.patch("markdown_vault.core.vault_fs.shutil.rmtree") as m:
                vfs.rmtree(d, ignore_errors=True)
            m.assert_called_once_with(d, ignore_errors=True)

    def test_rmtree_defaults_to_not_ignoring_errors(self):
        with _Vault() as v:
            d = os.path.join(v.vault, "tree")
            os.makedirs(d)
            with mock.patch("markdown_vault.core.vault_fs.shutil.rmtree") as m:
                vfs.rmtree(d)
            m.assert_called_once_with(d, ignore_errors=False)


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
