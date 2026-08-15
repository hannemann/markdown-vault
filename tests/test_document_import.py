"""Tests for document_import — local document/audio -> Markdown note.

Pure logic (dispatch, note assembly, table building) runs everywhere; the
format round-trips need the optional AI stack and are skipUnless-guarded.
"""

import datetime
import tempfile
import unittest
from pathlib import Path

from markdown_vault import document_import as di

_HAS_STACK = di.is_available() is None


class TestAvailability(unittest.TestCase):
    def test_none_or_install_hint(self):
        r = di.is_available()
        self.assertTrue(r is None or "install-ai" in r)

    def test_installed_checks_presence_without_executing(self):
        # find_spec-based: true for a real module, false for a missing one, and it
        # never imports/executes the target (json here just proves the truthy path).
        self.assertTrue(di._installed("json"))
        self.assertFalse(di._installed("no_such_module_xyz_123"))

    def test_unknown_suffix_needs_no_backend(self):
        self.assertIsNone(di.is_available(".zzz"))

    @unittest.skipUnless(_HAS_STACK, "needs the optional AI stack (make install-ai)")
    def test_is_available_per_format(self):
        self.assertIsNone(di.is_available(".pdf"))
        self.assertIsNone(di.is_available(".mp3"))

    def test_whisper_model_dir_under_app_models(self):
        p = di.whisper_model_dir("base")
        self.assertEqual(p.name, "whisper-base")
        self.assertEqual(p.parent.name, "models")            # beside GGUF/ONNX
        self.assertEqual(di.whisper_model_dir("Systran/faster-whisper-x").name,
                         "whisper-Systran--faster-whisper-x")

    def test_whisper_model_ready_is_a_file_check(self):
        # No faster_whisper load — just model.bin present in the app-local folder.
        from unittest import mock
        with tempfile.TemporaryDirectory() as d:
            mdir = Path(d) / "whisper-base"
            with mock.patch.object(di, "whisper_model_dir", return_value=mdir):
                self.assertFalse(di.whisper_model_ready())
                mdir.mkdir(parents=True)
                (mdir / "model.bin").write_bytes(b"x")
                self.assertTrue(di.whisper_model_ready())


class TestDispatch(unittest.TestCase):
    def test_supported_suffixes_cover_every_class(self):
        for s in (".pdf", ".docx", ".pptx", ".xlsx", ".mp3", ".wav"):
            self.assertIn(s, di.SUPPORTED_SUFFIXES)

    def test_audio_suffixes_route_to_audio_handler(self):
        for s in (".mp3", ".wav", ".m4a", ".flac"):
            self.assertIs(di._HANDLERS[s], di._convert_audio)

    def test_docx_routes_to_docx_handler(self):
        self.assertIs(di._HANDLERS[".docx"], di._convert_docx)

    def test_unsupported_suffix_raises(self):
        with self.assertRaises(ValueError):
            di.convert("/tmp/whatever.xyz")

    def test_needs_transcription_model_only_for_audio(self):
        for s in (".mp3", ".WAV", ".m4a", ".flac"):
            self.assertTrue(di.needs_transcription_model(s), s)
        for s in (".pdf", ".docx", ".pptx", ".xlsx", ".zzz"):
            self.assertFalse(di.needs_transcription_model(s), s)


class TestPipeTable(unittest.TestCase):
    def test_builds_header_and_rows(self):
        md = di._rows_to_pipe_table([["Name", "Age"], ["Alice", "30"]])
        self.assertIn("| Name | Age |", md)
        self.assertIn("| --- | --- |", md)
        self.assertIn("| Alice | 30 |", md)

    def test_empty_rows_give_empty_string(self):
        self.assertEqual(di._rows_to_pipe_table([]), "")
        self.assertEqual(di._rows_to_pipe_table([["", " "]]), "")

    def test_pipes_in_cells_are_escaped_and_ragged_rows_padded(self):
        md = di._rows_to_pipe_table([["a|b"], ["c", "d"]])
        self.assertIn("a\\|b", md)
        self.assertIn("| c | d |", md)          # first row widened to 2 columns


class TestNoteAssembly(unittest.TestCase):
    def test_frontmatter_has_title_source_date(self):
        r = di.DocumentResult(path="/docs/report.pdf", title="Q3 Report",
                              markdown="Body text.")
        note = di.to_note(r, today=datetime.date(2026, 8, 15))
        self.assertIn("title: Q3 Report", note)
        self.assertIn("source: /docs/report.pdf", note)
        self.assertIn("imported: '2026-08-15'", note)
        self.assertTrue(note.rstrip().endswith("Body text."))

    def test_awkward_title_round_trips(self):
        import yaml
        r = di.DocumentResult(path="/x", title="- weird: title #1", markdown="b")
        fm = yaml.safe_load(di.to_note(r).split("---")[1])
        self.assertEqual(fm["title"], "- weird: title #1")


class TestSaveToVault(unittest.TestCase):
    def _res(self, title="My Doc", md="content"):
        return di.DocumentResult(path="/src/a.pdf", title=title, markdown=md)

    def test_writes_slugged_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = di.save_to_vault(self._res(), d)
            self.assertEqual(p, Path(d) / "my-doc.md")
            self.assertIn("content", p.read_text())

    def test_name_overrides_title(self):
        with tempfile.TemporaryDirectory() as d:
            p = di.save_to_vault(self._res(), d, name="Custom Name")
            self.assertEqual(p.stem, "custom-name")

    def test_collision_gets_suffix(self):
        with tempfile.TemporaryDirectory() as d:
            di.save_to_vault(self._res(), d)
            p2 = di.save_to_vault(self._res(), d)
            self.assertEqual(p2.stem, "my-doc-2")

    def test_blank_name_falls_back_to_title(self):
        with tempfile.TemporaryDirectory() as d:
            p = di.save_to_vault(self._res(), d, name="   ")
            self.assertEqual(p.stem, "my-doc")


@unittest.skipUnless(_HAS_STACK, "needs the optional AI stack (make install-ai)")
class TestFormatRoundTrips(unittest.TestCase):
    """Convert tiny real files created with the installed backends."""

    def test_pdf(self):
        import pymupdf
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "doc.pdf"
            doc = pymupdf.open()
            doc.new_page().insert_text((72, 72), "Hello PDF world")
            doc.save(str(path))
            doc.close()
            r = di.convert(path)
            self.assertIn("Hello PDF world", r.markdown)
            self.assertEqual(r.title, "doc")          # no metadata title -> stem

    def test_xlsx(self):
        import openpyxl
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "sheet.xlsx"
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "Data"
            ws.append(["Name", "Age"])
            ws.append(["Alice", 30])
            wb.save(str(path))
            r = di.convert(path)
            self.assertIn("## Data", r.markdown)
            self.assertIn("| Name | Age |", r.markdown)
            self.assertIn("Alice", r.markdown)

    def _make_docx(self, path, body):
        import zipfile
        ct = ('<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/'
              'package/2006/content-types"><Default Extension="rels" ContentType='
              '"application/vnd.openxmlformats-package.relationships+xml"/><Default '
              'Extension="xml" ContentType="application/xml"/><Override PartName='
              '"/word/document.xml" ContentType="application/vnd.openxmlformats-'
              'officedocument.wordprocessingml.document.main+xml"/></Types>')
        rels = ('<?xml version="1.0"?><Relationships xmlns="http://schemas.'
                'openxmlformats.org/package/2006/relationships"><Relationship Id="rId1"'
                ' Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                'relationships/officeDocument" Target="word/document.xml"/>'
                '</Relationships>')
        doc = ('<?xml version="1.0"?><w:document xmlns:w="http://schemas.'
               f'openxmlformats.org/wordprocessingml/2006/main"><w:body>{body}'
               '</w:body></w:document>')
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("[Content_Types].xml", ct)
            z.writestr("_rels/.rels", rels)
            z.writestr("word/document.xml", doc)

    def test_docx_table_survives(self):
        # Regression: mammoth's Markdown writer drops tables; the HTML route keeps
        # them. A .docx table must come out as a pipe table, not flattened text.
        cell = lambda t: f'<w:tc><w:p><w:r><w:t>{t}</w:t></w:r></w:p></w:tc>'
        row = lambda *c: "<w:tr>" + "".join(cell(x) for x in c) + "</w:tr>"
        body = f'<w:tbl>{row("Metric", "Value")}{row("Revenue", "100")}</w:tbl>'
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "t.docx"
            self._make_docx(path, body)
            md = di.convert(path).markdown
            self.assertIn("| Metric | Value |", md)
            self.assertIn("| Revenue | 100 |", md)
            self.assertIn("| --- |", md)

    def test_pptx(self):
        from pptx import Presentation
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "deck.pptx"
            prs = Presentation()
            slide = prs.slides.add_slide(prs.slide_layouts[5])
            slide.shapes.title.text = "My Slide Title"
            prs.save(str(path))
            r = di.convert(path)
            self.assertIn("## Slide 1", r.markdown)
            self.assertIn("My Slide Title", r.markdown)


if __name__ == "__main__":
    unittest.main()
