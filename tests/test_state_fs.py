"""Tests for markdown_vault.core.state_fs — the guarded chokepoint for filesystem writes
OUTSIDE any vault (settings, session, caches, models, debug dumps, logs).

Two concerns kept apart: the containment guard (a write must land under an allowed state
root AND under no vault) and the write ops themselves (atomic, cleaning up on failure).
The op tests patch _allowed_roots/_vault_roots to controlled temp dirs; a separate class
exercises the real root derivation from the XDG dirs and the configured model folders.
"""

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from markdown_vault.core import state_fs as sfs


class _Roots:
    """Context manager patching state_fs's roots. The vault is a subdir of the allowed
    root, so a path under the vault is under both — that is what the negative clause is
    about (a model folder pointed inside a vault), and it lets one topology test both
    clauses: root/… is allowed, root/vault/… is refused as InsideVault."""

    def __init__(self):
        self._base = TemporaryDirectory()
        self.root = None
        self.vault = None

    def __enter__(self):
        self.root = self._base.__enter__()
        self.vault = os.path.join(self.root, "vault")
        os.mkdir(self.vault)
        self._patches = [
            mock.patch.object(sfs, "_state_roots", return_value=[self.root]),
            mock.patch.object(sfs, "_vault_roots", return_value=[self.vault]),
            mock.patch.object(sfs, "_model_roots", return_value=[]),
        ]
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *a):
        for p in self._patches:
            p.stop()
        self._base.__exit__(*a)


class TestGuard(unittest.TestCase):
    def test_write_under_an_allowed_root_succeeds(self):
        with _Roots() as r:
            sfs.write_text(os.path.join(r.root, "settings.yaml"), "a: 1")
            self.assertEqual(Path(r.root, "settings.yaml").read_text(), "a: 1")

    def test_write_under_a_vault_is_refused(self):
        with _Roots() as r:
            with self.assertRaises(sfs.InsideVault):
                sfs.write_text(os.path.join(r.vault, "note.md"), "x")
            self.assertFalse(Path(r.vault, "note.md").exists())

    def test_write_outside_every_root_is_refused(self):
        with _Roots(), TemporaryDirectory() as outside:
            with self.assertRaises(sfs.OutsideAllowedRoots):
                sfs.write_text(os.path.join(outside, "x"), "x")

    def test_a_root_that_is_also_inside_a_vault_is_refused(self):
        # The negative clause: even under an allowed root, a target that is ALSO under a
        # vault is refused (a model folder placed inside a vault must not be written to).
        with TemporaryDirectory() as vault:
            model_dir = os.path.join(vault, "models")
            os.mkdir(model_dir)
            with mock.patch.object(sfs, "_state_roots", return_value=[model_dir]), \
                 mock.patch.object(sfs, "_vault_roots", return_value=[vault]):
                with self.assertRaises(sfs.InsideVault):
                    sfs.write_bytes(os.path.join(model_dir, "m.gguf"), b"GGUF")


class TestOps(unittest.TestCase):
    def test_write_text_is_atomic_no_part_left(self):
        with _Roots() as r:
            p = os.path.join(r.root, "f.txt")
            sfs.write_text(p, "Ünïcode")
            self.assertEqual(Path(p).read_text(encoding="utf-8"), "Ünïcode")
            self.assertFalse(Path(p + ".part").exists())

    def test_write_text_overwrites_via_replace_not_truncate(self):
        with _Roots() as r:
            p = os.path.join(r.root, "f.txt")
            Path(p).write_text("old")
            sfs.write_text(p, "new")
            self.assertEqual(Path(p).read_text(), "new")

    def test_write_bytes(self):
        with _Roots() as r:
            p = os.path.join(r.root, "f.bin")
            sfs.write_bytes(p, b"\x00\x01\x02")
            self.assertEqual(Path(p).read_bytes(), b"\x00\x01\x02")

    def test_mkdir_creates_under_a_root(self):
        with _Roots() as r:
            d = os.path.join(r.root, "sub", "deep")
            sfs.mkdir(d, parents=True, exist_ok=True)
            self.assertTrue(Path(d).is_dir())

    def test_mkdir_under_a_vault_is_refused(self):
        with _Roots() as r:
            with self.assertRaises(sfs.InsideVault):
                sfs.mkdir(os.path.join(r.vault, "x"))

    def test_unlink_removes_a_file(self):
        with _Roots() as r:
            p = os.path.join(r.root, "f")
            Path(p).write_text("x")
            sfs.unlink(p)
            self.assertFalse(Path(p).exists())

    def test_unlink_a_leaf_symlink_pointing_out_is_allowed(self):
        # follow_last=False: the link sits inside the root, so deleting it is a state op
        # even though it points outside. (VaultFS makes the same distinction; here it means
        # a stale cache symlink can be cleaned up.)
        with _Roots() as r, TemporaryDirectory() as outside:
            link = os.path.join(r.root, "stale")
            os.symlink(os.path.join(outside, "victim"), link)
            sfs.unlink(link)
            self.assertFalse(os.path.lexists(link))

    def test_unlink_missing_ok(self):
        with _Roots() as r:
            sfs.unlink(os.path.join(r.root, "nope"), missing_ok=True)  # must not raise


class TestWriteStream(unittest.TestCase):
    def test_streams_chunks_atomically_and_returns_the_count(self):
        with _Roots() as r:
            p = os.path.join(r.root, "model.gguf")
            n = sfs.write_stream(p, [b"GGUF", b"x" * 100])
            self.assertEqual(n, 104)
            self.assertEqual(Path(p).read_bytes(), b"GGUF" + b"x" * 100)
            self.assertFalse(Path(p + ".part").exists())

    def test_a_rejected_validate_removes_the_part_and_raises(self):
        with _Roots() as r:
            p = os.path.join(r.root, "model.gguf")
            with self.assertRaises(sfs.RejectedContent):
                sfs.write_stream(p, [b"<html>"], validate=lambda tmp: "not a GGUF")
            self.assertFalse(Path(p).exists())
            self.assertFalse(Path(p + ".part").exists())

    def test_a_producer_exception_removes_the_part_and_propagates(self):
        def chunks():
            yield b"data"
            raise ConnectionResetError("dropped")

        with _Roots() as r:
            p = os.path.join(r.root, "model.gguf")
            with self.assertRaises(ConnectionResetError):
                sfs.write_stream(p, chunks())
            self.assertFalse(Path(p + ".part").exists())

    def test_write_stream_under_a_vault_is_refused_before_opening(self):
        with _Roots() as r:
            with self.assertRaises(sfs.InsideVault):
                sfs.write_stream(os.path.join(r.vault, "m.gguf"), [b"x"])
            self.assertFalse(Path(r.vault, "m.gguf.part").exists())


class TestSymlinkedStateFile(unittest.TestCase):
    """C / AQ1: StateFS guards against our own code, not a crafted path, so a state file the
    USER symlinked out of the state dir (settings.yaml -> a dotfiles repo) is honoured — the
    write goes THROUGH the link and the link survives. The vault clause stays fully resolved,
    so a state file symlinked INTO a vault is still refused (it would bypass VaultFS)."""

    def test_a_state_file_symlinked_out_is_written_through_and_the_link_kept(self):
        with _Roots() as r, TemporaryDirectory() as dotfiles:
            realfile = os.path.join(dotfiles, "settings.yaml")
            Path(realfile).write_text("old")
            link = os.path.join(r.root, "settings.yaml")
            os.symlink(realfile, link)
            sfs.write_text(link, "new")
            self.assertTrue(os.path.islink(link))                 # link preserved
            self.assertEqual(Path(realfile).read_text(), "new")   # written through the link

    def test_a_state_file_symlinked_into_a_vault_is_still_refused(self):
        with _Roots() as r:
            target_in_vault = os.path.join(r.vault, "sneak.yaml")
            link = os.path.join(r.root, "settings.yaml")
            os.symlink(target_in_vault, link)
            with self.assertRaises(sfs.InsideVault):
                sfs.write_text(link, "x")
            self.assertFalse(os.path.exists(target_in_vault))     # nothing written into vault


class TestModelRootScope(unittest.TestCase):
    """AM1: a user-picked model folder (a folder chooser can point it anywhere, e.g. the
    home directory) widens containment for the DOWNLOAD only. write_text/mkdir/unlink stay
    pinned to the XDG state roots, so picking $HOME for models does not let StateFS write
    arbitrary files under it."""

    def test_model_folder_is_writable_by_download_only_not_the_other_ops(self):
        with TemporaryDirectory() as xdg, TemporaryDirectory() as models:
            with mock.patch.object(sfs, "_state_roots", return_value=[xdg]), \
                 mock.patch.object(sfs, "_model_roots", return_value=[models]), \
                 mock.patch.object(sfs, "_vault_roots", return_value=[]):
                # write_stream is allowed into the model folder...
                n = sfs.write_stream(os.path.join(models, "m.gguf"), [b"GGUF"])
                self.assertEqual(n, 4)
                # ...but no other op is: the model roots widen the guard for the download
                # alone, so a plain write/mkdir/delete into the same folder is refused.
                with self.assertRaises(sfs.OutsideAllowedRoots):
                    sfs.write_text(os.path.join(models, "notes.txt"), "x")
                with self.assertRaises(sfs.OutsideAllowedRoots):
                    sfs.mkdir(os.path.join(models, "sub"))
                # unlink pinned explicitly — it is the op that destroys data, so a scope
                # regression here (model roots leaking back in) must fail a test, not slip.
                victim = os.path.join(models, "keepme")
                Path(victim).write_text("precious")
                with self.assertRaises(sfs.OutsideAllowedRoots):
                    sfs.unlink(victim)
                self.assertEqual(Path(victim).read_text(), "precious")  # not destroyed


class TestRootsDerivation(unittest.TestCase):
    def test_state_roots_are_the_four_xdg_dirs(self):
        with TemporaryDirectory() as cfg, TemporaryDirectory() as state, \
             TemporaryDirectory() as cache, TemporaryDirectory() as data:
            with mock.patch.object(sfs.paths, "CONFIG_DIR", Path(cfg)), \
                 mock.patch.object(sfs.paths, "STATE_DIR", Path(state)), \
                 mock.patch.object(sfs.paths, "CACHE_DIR", Path(cache)), \
                 mock.patch.object(sfs.paths, "DATA_DIR", Path(data)):
                roots = sfs._state_roots()
            self.assertEqual(set(roots), {cfg, state, cache, data})

    def test_model_roots_are_the_configured_folders(self):
        with TemporaryDirectory() as data, TemporaryDirectory() as gguf:
            with mock.patch.object(sfs.paths, "DATA_DIR", Path(data)), \
                 mock.patch.object(sfs.config, "settings",
                                   return_value={"ask": {"gguf": {"dir": gguf}}}):
                roots = sfs._model_roots()
            self.assertIn(gguf, roots)                       # the configured Ask folder
            self.assertIn(os.path.join(data, "onnx"), roots)  # the ONNX default under data

    def test_an_invalid_model_root_is_dropped_not_fatal(self):
        # A hand-edited ask.gguf.dir of "" or a relative path must not break the download;
        # it is logged and dropped rather than admitted or fatal.
        with TemporaryDirectory() as data:
            with mock.patch.object(sfs.paths, "DATA_DIR", Path(data)), \
                 mock.patch.object(sfs.config, "settings",
                                   return_value={"ask": {"gguf": {"dir": "relative/bad"}}}):
                roots = sfs._model_roots()
            self.assertNotIn("relative/bad", roots)
            self.assertIn(os.path.join(data, "onnx"), roots)  # the valid one still stands


if __name__ == "__main__":
    unittest.main()
