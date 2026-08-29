"""Tests for the attachment lifecycle (paths + move/remove/relink)."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import markdown_vault.core.config as _cfg
from markdown_vault.core import attachments as at
from markdown_vault.core import vault_fs


class TestPaths(unittest.TestCase):
    def test_attachment_target_root_and_subdir(self):
        d, rel = at.attachment_target("/v", "/v", "note")
        self.assertEqual((str(d), rel), ("/v/attachments/note", "attachments/note"))
        d, rel = at.attachment_target("/v", "/v/sub", "note")
        self.assertEqual((str(d), rel), ("/v/attachments/sub/note", "../attachments/sub/note"))

    def test_mirror_dir_note_strips_md(self):
        self.assertEqual(str(at.mirror_dir("/v", "/v/sub/note.md")),
                         "/v/attachments/sub/note")

    def test_mirror_dir_folder(self):
        self.assertEqual(str(at.mirror_dir("/v", "/v/sub")), "/v/attachments/sub")

    def test_link_prefix(self):
        self.assertEqual(at.link_prefix("/v", "/v/a/b/note.md"),
                         "../../attachments/a/b/note")

    def test_is_internal(self):
        self.assertTrue(at.is_internal("/v/attachments"))
        self.assertTrue(at.is_internal("/v/attachments/sub/note"))
        self.assertTrue(at.is_internal("/v/attachments/note/img.png"))
        self.assertFalse(at.is_internal("/v/notes/note.md"))
        self.assertFalse(at.is_internal("/v/note.md"))


class TestRelink(unittest.TestCase):
    def test_rewrites_markdown_and_html_only_for_prefix(self):
        text = ('![x](attachments/old/a.png) [keep](attachments/old-other/z) '
                '<img src="attachments/old/b.png">')
        out = at.relink(text, "attachments/old", "attachments/new")
        self.assertIn("![x](attachments/new/a.png)", out)
        self.assertIn('src="attachments/new/b.png"', out)
        self.assertIn("attachments/old-other/z", out)   # different prefix untouched

    def test_noop_when_prefix_unchanged(self):
        text = "![x](attachments/a/img.png)"
        self.assertEqual(at.relink(text, "attachments/a", "attachments/a"), text)


class TestFilesystem(unittest.TestCase):
    def setUp(self):
        self.v = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.v, ignore_errors=True))
        # The FS ops now route through VaultFS, which refuses writes outside a configured
        # vault. Register the temp dir as the vault so the legitimate in-vault ops here pass.
        _cfg._vaults_cache = [{"name": "v", "path": self.v}]
        self.addCleanup(lambda: setattr(_cfg, "_vaults_cache", None))

    def _mk(self, rel, content=b"x"):
        p = Path(self.v) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
        return p

    def test_remove_note_attachments_and_prune(self):
        self._mk("attachments/sub/note/img.png")
        at.remove(self.v, str(Path(self.v) / "sub" / "note.md"))
        self.assertFalse((Path(self.v) / "attachments" / "sub" / "note").exists())
        self.assertFalse((Path(self.v) / "attachments" / "sub").exists())  # pruned
        self.assertTrue((Path(self.v) / "attachments").exists())           # root kept

    def test_remove_folder_attachments(self):
        self._mk("attachments/sub/a/img.png")
        self._mk("attachments/sub/b/img.png")
        at.remove(self.v, str(Path(self.v) / "sub"))
        self.assertFalse((Path(self.v) / "attachments" / "sub").exists())

    def test_move_note_attachments(self):
        self._mk("attachments/old/img.png")
        at.move(self.v, str(Path(self.v) / "old.md"), self.v, str(Path(self.v) / "new.md"))
        self.assertTrue((Path(self.v) / "attachments" / "new" / "img.png").exists())
        self.assertFalse((Path(self.v) / "attachments" / "old").exists())

    def test_move_into_subfolder(self):
        self._mk("attachments/note/img.png")
        at.move(self.v, str(Path(self.v) / "note.md"),
                self.v, str(Path(self.v) / "sub" / "note.md"))
        self.assertTrue((Path(self.v) / "attachments" / "sub" / "note" / "img.png").exists())
        self.assertFalse((Path(self.v) / "attachments" / "note").exists())

    def test_remove_refuses_path_outside_attachments(self):
        # A mirror that normalises out of the attachments tree (path not under the
        # vault) must never be deleted — it could be the user's own notes.
        victim = Path(self.v) / "other" / "notes"
        victim.mkdir(parents=True)
        (victim / "keep.md").write_text("x")
        outside = str(Path(self.v).parent / "other" / "notes.md")  # sibling of vault
        self.assertEqual(str(at.mirror_dir(self.v, outside)), str(victim))  # escapes
        at.remove(self.v, outside)
        self.assertTrue(victim.exists())            # protected, not deleted

    def test_move_refuses_path_outside_attachments(self):
        victim = Path(self.v) / "other" / "notes"
        victim.mkdir(parents=True)
        (victim / "keep.md").write_text("x")
        outside = str(Path(self.v).parent / "other" / "notes.md")
        at.move(self.v, outside, self.v, str(Path(self.v) / "safe.md"))
        self.assertTrue(victim.exists())

    def test_relink_file_on_disk(self):
        note = self._mk("note.md", b"![x](attachments/old/a.png)")
        at.relink_file(str(note), "attachments/old", "attachments/new")
        self.assertEqual(note.read_text(), "![x](attachments/new/a.png)")

    def test_relink_file_rewrites_through_vaultfs(self):
        # Mutation-verified route check: a raw p.write_text leaves this mock uncalled.
        note = self._mk("note.md", b"![x](attachments/old/a.png)")
        with mock.patch("markdown_vault.core.vault_fs.write_text") as w:
            at.relink_file(str(note), "attachments/old", "attachments/new")
        w.assert_called_once()

    def test_relink_file_swallows_a_refused_write_and_keeps_the_note(self):
        # relink_file is best-effort and its caller (app_window) does not wrap it — a refused
        # write must be logged and swallowed, never propagate, and never corrupt the note.
        note = self._mk("note.md", b"![x](attachments/old/a.png)")
        with mock.patch("markdown_vault.core.vault_fs.write_text",
                        side_effect=vault_fs.VaultWriteError("refused")):
            at.relink_file(str(note), "attachments/old", "attachments/new")  # must not raise
        self.assertEqual(note.read_bytes(), b"![x](attachments/old/a.png)")  # untouched

    def test_store_image_writes_atomically_through_vaultfs(self):
        # A non-.md image is monitor-filtered, so the atomic writer is free of the false-reload
        # banner and guards against a half-written image on crash. Mutation-verified: a direct
        # write_bytes would leave this mock uncalled.
        note = str(Path(self.v) / "note.md")
        with mock.patch("markdown_vault.core.vault_fs.write_bytes_atomic") as w:
            at.store_image(self.v, note, b"PNGDATA", "pic.png")
        w.assert_called_once()

    def test_store_image_outside_any_vault_is_refused(self):
        # The regression the migration makes explicit: a note outside every configured vault
        # (editor's `find_vault_for_dir(...) or note_dir` fallback) can no longer store a
        # managed image — VaultFS refuses the write rather than dropping it beside the note.
        loose = Path(self.v).parent / "loose"
        self.addCleanup(lambda: __import__("shutil").rmtree(loose, ignore_errors=True))
        with self.assertRaises(vault_fs.VaultWriteError):
            at.store_image(str(loose), str(loose / "note.md"), b"X", "p.png")

    def test_remove_routes_through_vaultfs_and_swallows_failure(self):
        # remove is best-effort (shutil's ignore_errors is gone with the raw call) — a failed
        # rmtree must be logged and swallowed, not propagate.
        self._mk("attachments/note/img.png")
        with mock.patch("markdown_vault.core.vault_fs.rmtree",
                        side_effect=OSError("busy")) as w:
            at.remove(self.v, str(Path(self.v) / "note.md"))   # must not raise
        w.assert_called_once()

    def test_move_routes_through_vaultfs(self):
        self._mk("attachments/old/img.png")
        with mock.patch("markdown_vault.core.vault_fs.move") as w:
            at.move(self.v, str(Path(self.v) / "old.md"),
                    self.v, str(Path(self.v) / "new.md"))
        w.assert_called_once()

    def test_store_image_writes_and_returns_link(self):
        note = str(Path(self.v) / "sub" / "note.md")
        link = at.store_image(self.v, note, b"PNGDATA", "My Pic.png")
        self.assertEqual(link, "../attachments/sub/note/my-pic.png")
        self.assertEqual((Path(self.v) / "attachments/sub/note/my-pic.png").read_bytes(),
                         b"PNGDATA")

    def test_store_image_dedups(self):
        note = str(Path(self.v) / "note.md")
        a = at.store_image(self.v, note, b"A", "p.png")
        b = at.store_image(self.v, note, b"B", "p.png")
        self.assertEqual(a, "attachments/note/p.png")
        self.assertEqual(b, "attachments/note/p-2.png")

    def test_store_image_adds_extension_when_missing(self):
        note = str(Path(self.v) / "note.md")
        link = at.store_image(self.v, note, b"X", "pasted")
        self.assertTrue(link.endswith("pasted.png"))

    def _classify(self, text):
        note_dir = str(Path(self.v))
        return at.classify_image_links(text, note_dir, self.v)

    def test_classify_managed_image_is_ignored(self):
        self._mk("attachments/note/ok.png")
        self.assertEqual(self._classify("![](attachments/note/ok.png)"), [])

    def test_classify_missing_is_broken(self):
        out = self._classify("![](gone.png)")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0][3], "broken")

    def test_classify_external_existing_is_adoptable(self):
        self._mk("pics/cat.png")     # exists, but outside attachments/
        out = self._classify("![](pics/cat.png)")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0][3], "adoptable")
        self.assertEqual(out[0][4], str(Path(self.v) / "pics" / "cat.png"))

    def test_classify_skips_remote(self):
        self.assertEqual(self._classify("![](https://ex.com/a.png)"), [])

    def test_retarget_image(self):
        self._mk("pics/cat.png")
        text = "a ![c](pics/cat.png) b ![o](other.png)"
        out = at.retarget_image(text, str(Path(self.v)),
                                str(Path(self.v) / "pics" / "cat.png"),
                                "attachments/note/cat.png")
        self.assertIn("![c](attachments/note/cat.png)", out)
        self.assertIn("![o](other.png)", out)     # unrelated link untouched


if __name__ == "__main__":
    unittest.main()
