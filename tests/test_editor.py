"""Tests for markdown_vault.editor.editor — GtkSourceView editor widget.

Editor requires GTK widgets for full behavioral testing. These tests
verify the module structure and API surface without a display server.
"""

import os
import re
import unittest
from pathlib import Path


_SRC = Path(__file__).resolve().parent.parent / "src" / "markdown_vault" / "editor" / "editor.py"


class TestEditorModuleStructure(unittest.TestCase):
    """Verify the module exports the expected class and API."""

    def test_module_has_editor_class(self):
        source = _SRC.read_text(encoding="utf-8")
        self.assertIn("class Editor", source)

    def test_editor_has_expected_methods_in_source(self):
        source = _SRC.read_text(encoding="utf-8")
        for method in ("open_file", "save", "get_text", "scroll_to_line",
                       "update_settings", "update_color_scheme"):
            self.assertIn(f"def {method}", source)

    def test_editor_has_zoom_factor_property(self):
        source = _SRC.read_text(encoding="utf-8")
        self.assertIn("def zoom_factor", source)
        self.assertIn("_zoom_factor", source)

    def test_editor_has_base_font_size_property(self):
        source = _SRC.read_text(encoding="utf-8")
        self.assertIn("def base_font_size", source)

    def test_editor_constructor_accepts_font_params(self):
        source = _SRC.read_text(encoding="utf-8")
        self.assertIn("base_font_size", source)
        self.assertIn("tab_width", source)
        self.assertIn("wrap_text", source)

    def test_editor_uses_gtksource5(self):
        source = _SRC.read_text(encoding="utf-8")
        self.assertIn('GtkSource", "5"', source)

    def test_editor_has_signals(self):
        source = _SRC.read_text(encoding="utf-8")
        self.assertIn("file-changed", source)
        self.assertIn("modified-changed", source)
        self.assertIn("text-changed", source)


class TestEditorSearch(unittest.TestCase):
    """Behavioural tests for the in-editor GtkSource search backend."""

    def _editor(self, text):
        from markdown_vault.editor.editor import Editor
        ed = Editor()
        ed._buffer.set_text(text)
        ed._buffer.place_cursor(ed._buffer.get_start_iter())
        return ed

    def _selected(self, ed):
        lo, hi = ed._current_match_iters()
        return ed._buffer.get_text(lo, hi, False)

    def test_search_set_text_selects_first_match(self):
        ed = self._editor("bar foo baz foo")
        ed.search_set_text("foo")
        self.assertEqual(self._selected(ed), "foo")
        self.assertEqual(ed._current_match_iters()[0].get_offset(), 4)

    def test_incremental_typing_tightens_in_place(self):
        # R21.9: refining the query keeps the current match, not the next one.
        ed = self._editor("foo bar foo")
        ed.search_set_text("f")
        ed.search_set_text("fo")
        ed.search_set_text("foo")
        self.assertEqual(ed._current_match_iters()[0].get_offset(), 0)

    def test_search_next_selects_match(self):
        ed = self._editor("foo bar foo")
        ed.search_set_text("foo")
        self.assertTrue(ed.search_next())
        self.assertEqual(self._selected(ed), "foo")

    def test_search_next_advances_then_wraps(self):
        ed = self._editor("a X b X c")
        ed.search_set_text("X")  # already selects the first match
        first = ed._current_match_iters()[0].get_offset()
        ed.search_next()
        second = ed._current_match_iters()[0].get_offset()
        self.assertGreater(second, first)
        ed.search_next()  # wrap around (set_wrap_around True)
        self.assertEqual(ed._current_match_iters()[0].get_offset(), first)

    def test_search_prev_goes_backward(self):
        ed = self._editor("X y X y X")
        ed.search_set_text("X")
        ed.search_next()
        ed.search_next()
        mid = ed._current_match_iters()[0].get_offset()
        ed.search_prev()
        self.assertLess(ed._current_match_iters()[0].get_offset(), mid)

    def test_no_match_returns_false(self):
        ed = self._editor("hello world")
        ed.search_set_text("zzz")
        self.assertFalse(ed.search_next())

    def test_clear_disables_search(self):
        ed = self._editor("foo foo")
        ed.search_set_text("foo")
        ed.search_clear()
        self.assertFalse(ed.search_next())


class TestClampScroll(unittest.TestCase):
    """The pure scroll-clamp helper — the part reload_editor's pattern is built
    on: never scroll past the last page, never below zero."""

    def test_in_range_value_kept(self):
        from markdown_vault.editor.editor import _clamp_scroll
        self.assertEqual(_clamp_scroll(1234.0, upper=9016.0, page_size=500.0), 1234.0)

    def test_value_past_end_clamped_to_last_page(self):
        # Returning into a note that has since become shorter: 1234 no longer
        # exists, so land at the end (upper - page_size) instead of the void.
        from markdown_vault.editor.editor import _clamp_scroll
        self.assertEqual(_clamp_scroll(1234.0, upper=500.0, page_size=100.0), 400.0)

    def test_never_negative(self):
        from markdown_vault.editor.editor import _clamp_scroll
        self.assertEqual(_clamp_scroll(50.0, upper=80.0, page_size=200.0), 0.0)


class TestEditorScrollPosition(unittest.TestCase):
    """Capture + restore of the reader's caret and scroll — feature: the
    history restores where the reader was."""

    def _editor(self, text):
        from markdown_vault.editor.editor import Editor
        ed = Editor()
        ed._buffer.set_text(text)
        return ed

    def _cursor(self, ed):
        buf = ed._buffer
        return buf.get_iter_at_mark(buf.get_insert()).get_offset()

    def test_capture_reads_cursor_offset(self):
        ed = self._editor("hello world")
        ed._buffer.place_cursor(ed._buffer.get_iter_at_offset(6))
        _scroll, cursor = ed.capture_scroll_position()
        self.assertEqual(cursor, 6)

    def test_restore_places_cursor(self):
        ed = self._editor("hello world")
        ed.restore_scroll_position(cursor=3)
        self.assertEqual(self._cursor(ed), 3)

    def test_restore_clamps_cursor_into_shorter_buffer(self):
        # The note is now shorter than when the position was captured; the caret
        # must land at the end, not raise past the character count.
        ed = self._editor("short")  # 5 chars
        ed.restore_scroll_position(cursor=9999)
        self.assertEqual(self._cursor(ed), 5)

    def test_restore_without_cursor_leaves_caret(self):
        ed = self._editor("hello world")
        ed._buffer.place_cursor(ed._buffer.get_iter_at_offset(4))
        ed.restore_scroll_position(scroll=10.0)  # cursor omitted
        self.assertEqual(self._cursor(ed), 4)

    def _flush_idle(self):
        from gi.repository import GLib
        ctx = GLib.MainContext.default()
        while ctx.pending():
            ctx.iteration(False)

    def test_restore_instant_sets_the_adjustment_value(self):
        # A note switch (smooth=False, the default) jumps: the saved pixel offset
        # is written straight onto the adjustment.
        import unittest.mock as m
        ed = self._editor("line one\nline two\nline three")
        vadj = m.MagicMock()
        vadj.get_upper.return_value = 1000.0
        vadj.get_page_size.return_value = 200.0
        with m.patch.object(ed, "get_vadjustment", return_value=vadj):
            ed.restore_scroll_position(scroll=120.0, cursor=3, smooth=False)
            self._flush_idle()
        vadj.set_value.assert_called_once()

    def test_restore_smooth_targets_the_saved_offset_not_the_caret(self):
        # In-page back/forward (smooth=True) animates via scroll_to_iter to the
        # line at the SAVED offset — no direct adjustment jump. The caret is
        # routinely far from the viewport (a freshly opened note keeps it at 0
        # while the reader scrolls down), so aligning on it would land at the
        # caret instead of the saved spot.
        import unittest.mock as m
        ed = self._editor("line one\nline two\nline three")
        vadj = m.MagicMock()
        target = object()
        ed._view = m.MagicMock()
        ed._view.get_iter_at_location.return_value = (False, target)
        with m.patch.object(ed, "get_vadjustment", return_value=vadj):
            ed.restore_scroll_position(scroll=120.0, cursor=0, smooth=True)
            self._flush_idle()
        ed._view.get_iter_at_location.assert_called_once_with(0, 120)
        ed._view.scroll_to_iter.assert_called_once_with(target, 0.0, True, 0.0, 0.0)
        vadj.set_value.assert_not_called()

    def test_restore_smooth_needs_no_caret(self):
        # An entry may carry a scroll and no cursor; the animation no longer
        # depends on the caret, so it must still run (it used to fall back to
        # the instant jump).
        import unittest.mock as m
        ed = self._editor("line one\nline two\nline three")
        vadj = m.MagicMock()
        ed._view = m.MagicMock()
        ed._view.get_iter_at_location.return_value = (False, object())
        with m.patch.object(ed, "get_vadjustment", return_value=vadj):
            ed.restore_scroll_position(scroll=120.0, smooth=True)
            self._flush_idle()
        ed._view.scroll_to_iter.assert_called_once()
        vadj.set_value.assert_not_called()


class TestSaveIsAtomicAndGuarded(unittest.TestCase):
    """save() writes through VaultFS's atomic writer: a crash mid-save leaves the PREVIOUS
    note intact, where the direct writer truncated first and would leave an empty file. The
    resulting rename is announced to the monitor by the save sites, so it is not mistaken for
    an external change."""

    def test_save_routes_through_the_atomic_writer(self):
        from unittest.mock import patch

        from markdown_vault.editor.editor import Editor
        ed = Editor()
        ed._file_path = "/vault/note.md"
        with patch("markdown_vault.core.vault_fs.write_text_atomic") as w:
            self.assertTrue(ed.save())
        w.assert_called_once()

    def test_a_refused_write_returns_false_instead_of_raising(self):
        # VaultWriteError is not an OSError, so the existing handler would not catch it and
        # the refusal would escape into the UI. save() must report failure like any other.
        from unittest.mock import patch

        from markdown_vault.core import vault_fs
        from markdown_vault.editor.editor import Editor
        ed = Editor()
        ed._file_path = "/outside/note.md"
        with patch("markdown_vault.core.vault_fs.write_text_atomic",
                   side_effect=vault_fs.VaultWriteError("outside every vault")) as w:
            self.assertFalse(ed.save())
        # Without this the test passes for the wrong reason: the direct writer fails on the
        # non-existent path with an OSError, and the refusal path is never exercised.
        w.assert_called_once()


class TestSaveFailureReason(unittest.TestCase):
    """save() knows WHY it failed and used to drop it: the reason was logged and the user got
    "Could not save …" with no cause. For a note that is a symlink leading out of the vault
    that is unguessable — nothing about the note looks different. The reason is kept as a
    translated sentence for the caller to show."""

    def _editor(self, path="/vault/note.md"):
        from markdown_vault.editor.editor import Editor
        ed = Editor()
        ed._file_path = path
        return ed

    def test_a_refusal_leaves_a_reason(self):
        from unittest.mock import patch

        from markdown_vault.core import vault_fs
        ed = self._editor()
        with patch("markdown_vault.core.vault_fs.write_text_atomic",
                   side_effect=vault_fs.VaultWriteError("outside")):
            self.assertFalse(ed.save())
        self.assertIsNotNone(ed.last_save_error)
        self.assertIn("vault", ed.last_save_error.lower())

    def test_a_write_error_leaves_its_own_reason(self):
        from unittest.mock import patch
        ed = self._editor()
        with patch("markdown_vault.core.vault_fs.write_text_atomic",
                   side_effect=PermissionError(13, "denied")):
            self.assertFalse(ed.save())
        self.assertIsNotNone(ed.last_save_error)
        self.assertNotIn("denied", ed.last_save_error)      # translated, not the OS text

    def test_a_successful_save_clears_the_previous_reason(self):
        # Otherwise a stale reason from an earlier failure is shown after a later failure
        # of a different kind — or worse, alongside a success.
        from unittest.mock import patch

        from markdown_vault.core import vault_fs
        ed = self._editor()
        with patch("markdown_vault.core.vault_fs.write_text_atomic",
                   side_effect=vault_fs.VaultWriteError("outside")):
            ed.save()
        with patch("markdown_vault.core.vault_fs.write_text_atomic"):
            self.assertTrue(ed.save())
        self.assertIsNone(ed.last_save_error)

    def test_loading_another_file_drops_the_reason(self):
        # BL1: the reason describes a save attempt on a FILE. Without this it survives into
        # the next file loaded in the same editor, and the invariant "this reason belongs to
        # this attempt" rests on every display site happening to read it right after its own
        # save — call-site discipline instead of construction.
        import tempfile
        from unittest.mock import patch

        from markdown_vault.core import vault_fs
        ed = self._editor()
        with patch("markdown_vault.core.vault_fs.write_text_atomic",
                   side_effect=vault_fs.VaultWriteError("outside")):
            ed.save()
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as fh:
            fh.write("# other")
        ed.open_file(fh.name)
        self.assertIsNone(ed.last_save_error)

    def test_renaming_drops_the_reason(self):
        # A rename can be exactly what fixes the failure (moving the note back into the
        # vault), so keeping the old reason would describe a path that no longer applies.
        from unittest.mock import patch

        from markdown_vault.core import vault_fs
        ed = self._editor()
        with patch("markdown_vault.core.vault_fs.write_text_atomic",
                   side_effect=vault_fs.VaultWriteError("outside")):
            ed.save()
        ed.set_file_path("/vault/renamed.md")
        self.assertIsNone(ed.last_save_error)

    def test_an_unsaved_buffer_says_so(self):
        ed = self._editor(path=None)
        self.assertFalse(ed.save())
        self.assertIsNotNone(ed.last_save_error)


class TestInsertImageOutsideVault(unittest.TestCase):
    """The caller side of the attachments VaultFS migration: a note outside every configured
    vault makes store_image refuse with VaultWriteError — which is NOT an OSError. insert_image
    must catch it and insert nothing, not crash. Tests the caller, not only the receiver: a bare
    `except OSError` would let the new type through (the exact wiring the migration fixed)."""

    def test_insert_image_outside_any_vault_is_caught_not_crashing(self):
        from unittest.mock import patch

        from markdown_vault.core import vault_fs
        from markdown_vault.editor.editor import Editor
        ed = Editor()
        ed._file_path = "/loose/note.md"          # not inside any configured vault
        before = ed.get_text()
        with patch("markdown_vault.core.attachments.store_image",
                   side_effect=vault_fs.VaultWriteError("outside every vault")):
            ed.insert_image(b"PNGDATA", "pic.png")   # must not raise
        self.assertEqual(ed.get_text(), before)      # nothing inserted


class TestInsertImageThroughAnInVaultDirectorySymlink(unittest.TestCase):
    """A note reached through a directory symlink that stays INSIDE the vault.

    The path written into the note must resolve from the file's REAL location, because that is
    where every other program looks — the file physically sits there. Measured before this test
    was written, with the link three levels deeper than its target: derived from the LINK's
    directory the prefix is ``../../../attachments/sub/deep/linkdir/<stem>``, which from the real
    location resolves OUT of the vault entirely. Depths must differ, or both variants coincide
    and the test proves nothing.

    Such a note is writable today (the guard resolves, the target is inside the vault), so this
    is a live defect, not one the symlink-unlock feature introduces.
    """

    def setUp(self):
        import shutil
        import tempfile

        import markdown_vault.core.config as _cfg
        self.v = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.v, True)
        _cfg._vaults_cache = [{"name": "v", "path": self.v}]
        self.addCleanup(lambda: setattr(_cfg, "_vaults_cache", None))
        Path(self.v, "real").mkdir()
        Path(self.v, "sub", "deep").mkdir(parents=True)
        os.symlink(Path(self.v, "real"), Path(self.v, "sub", "deep", "linkdir"))
        self.note_via_link = Path(self.v, "sub", "deep", "linkdir", "n.md")
        self.note_via_link.write_text("", encoding="utf-8")
        self.note_real = Path(os.path.realpath(self.note_via_link))

    def test_the_written_link_resolves_from_the_files_real_location(self):
        from markdown_vault.editor.editor import Editor
        ed = Editor()
        ed._file_path = str(self.note_via_link)
        ed.insert_image(b"PNGDATA", "pic.png")
        m = re.search(r"!\[[^\]]*\]\(([^)]+)\)", ed.get_text())
        self.assertIsNotNone(m, "insert_image wrote no image link")
        target = Path(os.path.normpath(self.note_real.parent / m.group(1)))
        self.assertTrue(target.is_file(),
                        f"link {m.group(1)!r} does not resolve to a file from the note's real "
                        f"location {self.note_real.parent}")
        self.assertEqual(target.read_bytes(), b"PNGDATA")

    def test_classification_reports_the_images_real_path(self):
        """The second content site, and an honest scope note: classification is NOT broken here.

        Measured — the *verdict* is the same either way, because `Path.exists()` follows
        symlinks, so an image found through `linkdir/pics/` is found through `real/pics/` too.
        What differs is the SOURCE PATH reported for an adoptable image: derived from the link's
        directory it reads ``…/linkdir/pics/cat.png``, from the real one ``…/real/pics/cat.png``
        — one file, two spellings. Nothing compares those across routes today, so this pins
        consistency with the other two sites rather than fixing a demonstrated defect. Said
        plainly because an earlier version of this test claimed the image was marked broken,
        which mutation testing refuted.
        """
        from markdown_vault.editor.editor import Editor
        img_dir = Path(self.note_real.parent, "pics")
        img_dir.mkdir()
        (img_dir / "cat.png").write_bytes(b"PNGDATA")
        ed = Editor()
        ed._file_path = str(self.note_via_link)
        ed._buffer.set_text("![c](pics/cat.png)\n")
        marks = []
        ed._set_image_link_marks = marks.append
        ed._refresh_image_marks()
        sources = [m[4] for m in marks[0] if m[4]]
        self.assertEqual(sources, [str(img_dir / "cat.png")])

    def test_all_three_content_sites_go_through_the_shared_helper(self):
        # Route check: the three used to derive their roots separately, which is why the same
        # defect sat in all three. A reintroduced local derivation leaves this mock uncalled.
        from unittest.mock import patch

        from markdown_vault.editor.editor import Editor
        ed = Editor()
        ed._file_path = str(self.note_via_link)
        ed._buffer.set_text("![c](pics/cat.png)\n")
        real = (str(self.note_real), str(self.note_real.parent), self.v)
        (self.note_real.parent / "outside.png").write_bytes(b"PNGDATA")

        def adopt():
            # Set the source here, not once up front: _refresh_image_marks rebuilds
            # _adopt_sources, so the earlier subtest would wipe it and this call would return
            # before ever reaching the helper — a green result for the wrong reason.
            ed._adopt_sources[0] = str(self.note_real.parent / "outside.png")
            ed._adopt_image_on_line(0)

        # One block per entry point: a total would be brittle (insert_image also triggers a
        # reclassification) and would not say WHICH site regressed.
        for label, call in (("insert_image", lambda: ed.insert_image(b"PNGDATA", "a.png")),
                            ("_refresh_image_marks", ed._refresh_image_marks),
                            ("_adopt_image_on_line", adopt)):
            with self.subTest(site=label):
                with patch.object(Editor, "_content_roots", return_value=real) as roots:
                    call()
                self.assertTrue(roots.called, f"{label} derives its roots on its own again")

    def test_the_written_link_stays_inside_the_vault(self):
        # The sharper half: derived from the link's directory the prefix climbs out of the vault,
        # so the note would reference a file no vault operation may ever write.
        from markdown_vault.editor.editor import Editor
        ed = Editor()
        ed._file_path = str(self.note_via_link)
        ed.insert_image(b"PNGDATA", "pic.png")
        m = re.search(r"!\[[^\]]*\]\(([^)]+)\)", ed.get_text())
        target = Path(os.path.normpath(self.note_real.parent / m.group(1)))
        self.assertTrue(str(target).startswith(self.v + os.sep),
                        f"link escapes the vault: {target}")


if __name__ == "__main__":
    unittest.main()
