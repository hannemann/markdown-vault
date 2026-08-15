"""Tests for document_import — local document/audio -> Markdown note.

Pure logic (dispatch, note assembly, table building) runs everywhere; the
format round-trips need the optional AI stack and are skipUnless-guarded.
"""

import datetime
import re
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

    def test_odf_suffixes_route_to_odf_handler(self):
        for s in (".odt", ".ods", ".odp"):
            self.assertIn(s, di.SUPPORTED_SUFFIXES)
            self.assertIs(di._HANDLERS[s], di._convert_odf)

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


class TestImageStorage(unittest.TestCase):
    """save_to_vault stores extracted images into the attachments tree and
    rewrites their `mvattach:N` placeholder links — reusing the same layout and
    plumbing (attachments.store_image) as the web importer."""

    def _result(self, md, images):
        return di.DocumentResult(path="/src/doc.docx", title="Doc",
                                 markdown=md, images=images)

    def test_image_is_stored_and_link_rewritten(self):
        png = b"\x89PNG\r\n\x1a\nDATA"
        res = self._result(
            "Body text.\n\n![a photo](mvattach:0)\n",
            [di.ExtractedImage(token="mvattach:0", filename="photo.png", data=png)])
        with tempfile.TemporaryDirectory() as d:
            note = di.save_to_vault(res, d, vault_root=d)
            body = note.read_text()
            self.assertNotIn("mvattach:", body)                 # placeholder gone
            m = re.search(r"!\[a photo\]\(([^)]+)\)", body)
            self.assertIsNotNone(m)
            link = m.group(1)
            self.assertIn("attachments/", link)                 # into the managed tree
            stored = Path(d) / link                             # note sits at vault root
            self.assertTrue(stored.exists())
            self.assertEqual(stored.read_bytes(), png)

    def test_no_images_leaves_body_untouched(self):
        res = self._result("Just text.\n", [])
        with tempfile.TemporaryDirectory() as d:
            note = di.save_to_vault(res, d, vault_root=d)
            self.assertIn("Just text.", note.read_text())

    def test_each_of_several_images_is_stored(self):
        imgs = [di.ExtractedImage(f"mvattach:{i}", f"img{i}.png", bytes([i] * 4))
                for i in range(3)]
        md = "".join(f"![p{i}](mvattach:{i})\n\n" for i in range(3))
        res = self._result(md, imgs)
        with tempfile.TemporaryDirectory() as d:
            body = di.save_to_vault(res, d, vault_root=d).read_text()
            self.assertNotIn("mvattach:", body)
            self.assertEqual(body.count("attachments/"), 3)

    def test_convert_result_defaults_to_no_images(self):
        r = di.DocumentResult(path="/x", title="t", markdown="b")
        self.assertEqual(r.images, [])

    def test_decode_data_uri_tolerates_malformed_base64(self):
        # R106.1: a malformed data: URI (e.g. a producer wrote bad base64 into an ODF)
        # must not raise — it returns None so the image is skipped and the rest of the
        # document still imports, matching the xlsx path's posture.
        self.assertIsNone(di._decode_data_uri("data:image/png;base64,iVBORw0KGgo"))  # bad padding
        self.assertIsNone(di._decode_data_uri("not-a-data-uri"))
        # R107.1: b64decode ignores non-alphabet chars (validate=False), so garbage or an
        # empty payload decodes to zero bytes — treat that as a miss, not an empty file.
        self.assertIsNone(di._decode_data_uri("data:image/png;base64,!!!!"))         # garbage
        self.assertIsNone(di._decode_data_uri("data:image/png;base64,"))             # empty
        ok = di._decode_data_uri("data:image/png;base64,iVBORw0KGgo=")
        self.assertIsNotNone(ok)
        self.assertEqual(ok[1], "png")

    def test_identical_images_are_stored_once(self):
        # Dedup: two tokens with byte-identical data share one stored file (a logo
        # repeated across pages should not litter the tree).
        data = b"\x89PNG\r\n\x1a\nSAME-BYTES"
        res = self._result(
            "![a](mvattach:0)\n\n![b](mvattach:1)\n",
            [di.ExtractedImage("mvattach:0", "x.png", data),
             di.ExtractedImage("mvattach:1", "y.png", data)])
        with tempfile.TemporaryDirectory() as d:
            body = di.save_to_vault(res, d, vault_root=d).read_text()
            links = re.findall(r"!\[[ab]\]\(([^)]+)\)", body)
            self.assertEqual(len(links), 2)
            self.assertEqual(links[0], links[1])            # both point at the same file
            stored = list((Path(d) / "attachments").rglob("*.*"))
            self.assertEqual(len(stored), 1)                # written only once


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

    def test_pdf_embedded_image_is_extracted_and_stored(self):
        import pymupdf
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 24, 24))
        pix.clear_with(128)                              # a real, non-trivial image
        png = pix.tobytes("png")
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "withimg.pdf"
            doc = pymupdf.open()
            page = doc.new_page()
            page.insert_image(pymupdf.Rect(20, 20, 140, 140), stream=png)
            doc.save(str(path))
            doc.close()
            r = di.convert(path)
            self.assertGreaterEqual(len(r.images), 1)
            with tempfile.TemporaryDirectory() as vault:
                body = di.save_to_vault(r, vault, vault_root=vault).read_text()
                self.assertNotIn("mvattach:", body)
                self.assertNotIn("data:", body)          # no base64 left inline
                m = re.search(r"!\[[^\]]*\]\(([^)]+)\)", body)
                self.assertIsNotNone(m, body)
                stored = Path(vault) / m.group(1)
                self.assertTrue(stored.exists())
                self.assertGreater(stored.stat().st_size, 0)

    def test_pdf_vector_graphics_are_not_imaged(self):
        # Vector charts are left as text (see the module note): only genuine embedded
        # rasters become images. A page of pure vector drawing yields no images, but its
        # text is still extracted.
        import pymupdf
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "vector.pdf"
            doc = pymupdf.open()
            page = doc.new_page()
            pr = page.rect
            page.insert_text((72, 72), "Some report text here.")
            page.draw_rect(pymupdf.Rect(pr.width * 0.2, pr.height * 0.45,
                                        pr.width * 0.8, pr.height * 0.8), fill=(0.2, 0.6, 0.8))
            doc.save(str(path))
            doc.close()
            r = di.convert(path)
            self.assertEqual(len(r.images), 0)              # vector graphic not imaged
            self.assertIn("Some report text", r.markdown)   # text still extracted

    def test_xlsx_embedded_image_is_extracted_and_stored(self):
        import io
        import openpyxl
        from openpyxl.drawing.image import Image as XLImage
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "sheet.xlsx"
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "Data"
            ws.append(["Name", "Val"])
            ws.append(["A", 1])
            ws.add_image(XLImage(io.BytesIO(self._PNG_1x1)), "D2")
            wb.save(str(path))
            r = di.convert(path)
            self.assertGreaterEqual(len(r.images), 1)
            self.assertIn("| Name | Val |", r.markdown)      # table still present
            with tempfile.TemporaryDirectory() as vault:
                body = di.save_to_vault(r, vault, vault_root=vault).read_text()
                self.assertNotIn("mvattach:", body)
                m = re.search(r"!\[[^\]]*\]\(([^)]+)\)", body)
                self.assertIsNotNone(m, body)
                self.assertTrue((Path(vault) / m.group(1)).exists())

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

    def _make_docx_with_image(self, path, png_bytes):
        import zipfile
        ct = ('<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/'
              'package/2006/content-types"><Default Extension="rels" ContentType='
              '"application/vnd.openxmlformats-package.relationships+xml"/><Default '
              'Extension="xml" ContentType="application/xml"/><Default Extension="png"'
              ' ContentType="image/png"/><Override PartName="/word/document.xml" '
              'ContentType="application/vnd.openxmlformats-officedocument.'
              'wordprocessingml.document.main+xml"/></Types>')
        root_rels = ('<?xml version="1.0"?><Relationships xmlns="http://schemas.'
                     'openxmlformats.org/package/2006/relationships"><Relationship '
                     'Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument'
                     '/2006/relationships/officeDocument" Target="word/document.xml"/>'
                     '</Relationships>')
        doc_rels = ('<?xml version="1.0"?><Relationships xmlns="http://schemas.'
                    'openxmlformats.org/package/2006/relationships"><Relationship '
                    'Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/'
                    '2006/relationships/image" Target="media/image1.png"/>'
                    '</Relationships>')
        drawing = (
            '<w:p><w:r><w:drawing><wp:inline xmlns:wp="http://schemas.openxmlformats.'
            'org/drawingml/2006/wordprocessingDrawing"><wp:extent cx="100" cy="100"/>'
            '<wp:docPr id="1" name="img"/><a:graphic xmlns:a="http://schemas.'
            'openxmlformats.org/drawingml/2006/main"><a:graphicData uri="http://'
            'schemas.openxmlformats.org/drawingml/2006/picture"><pic:pic xmlns:pic='
            '"http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:nvPicPr>'
            '<pic:cNvPr id="1" name="img"/><pic:cNvPicPr/></pic:nvPicPr><pic:blipFill>'
            '<a:blip r:embed="rId1"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
            '<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="100" cy="100"/></a:xfrm>'
            '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic>'
            '</a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>')
        doc = ('<?xml version="1.0"?><w:document xmlns:w="http://schemas.'
               'openxmlformats.org/wordprocessingml/2006/main" xmlns:r="http://'
               'schemas.openxmlformats.org/officeDocument/2006/relationships">'
               f'<w:body>{drawing}</w:body></w:document>')
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("[Content_Types].xml", ct)
            z.writestr("_rels/.rels", root_rels)
            z.writestr("word/_rels/document.xml.rels", doc_rels)
            z.writestr("word/document.xml", doc)
            z.writestr("word/media/image1.png", png_bytes)

    def test_docx_embedded_image_is_extracted_and_stored(self):
        png = b"\x89PNG\r\n\x1a\nFAKE-DOCX-IMAGE"
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "withimg.docx"
            self._make_docx_with_image(path, png)
            r = di.convert(path)
            self.assertEqual(len(r.images), 1)
            self.assertEqual(r.images[0].data, png)
            with tempfile.TemporaryDirectory() as vault:
                note = di.save_to_vault(r, vault, vault_root=vault)
                body = note.read_text()
                self.assertNotIn("mvattach:", body)
                m = re.search(r"!\[[^\]]*\]\(([^)]+)\)", body)
                self.assertIsNotNone(m, body)
                stored = Path(vault) / m.group(1)
                self.assertTrue(stored.exists())
                self.assertEqual(stored.read_bytes(), png)

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

    def test_odt(self):
        from odf.opendocument import OpenDocumentText
        from odf.text import H, P
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "doc.odt"
            doc = OpenDocumentText()
            doc.text.addElement(H(outlinelevel=1, text="Report"))
            doc.text.addElement(P(text="A body paragraph with content."))
            doc.save(str(path))
            r = di.convert(path)
            self.assertIn("Report", r.markdown)
            self.assertIn("A body paragraph with content.", r.markdown)

    def test_odt_embedded_image_is_extracted_and_stored(self):
        from odf.opendocument import OpenDocumentText
        from odf.draw import Frame, Image
        from odf.text import P
        png = self._PNG_1x1
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "withimg.odt"
            doc = OpenDocumentText()
            href = doc.addPictureFromString(png, "image/png")
            para = P()
            frame = Frame(width="2cm", height="2cm")
            frame.addElement(Image(href=href))
            para.addElement(frame)
            doc.text.addElement(para)
            doc.save(str(path))
            r = di.convert(path)
            self.assertEqual(len(r.images), 1)
            self.assertEqual(r.images[0].data, png)
            with tempfile.TemporaryDirectory() as vault:
                body = di.save_to_vault(r, vault, vault_root=vault).read_text()
                self.assertNotIn("mvattach:", body)
                m = re.search(r"!\[[^\]]*\]\(([^)]+)\)", body)
                self.assertIsNotNone(m, body)
                self.assertTrue((Path(vault) / m.group(1)).exists())

    def test_ods_table(self):
        from odf.opendocument import OpenDocumentSpreadsheet
        from odf.table import Table, TableRow, TableCell
        from odf.text import P
        def cell(v):
            c = TableCell(); c.addElement(P(text=v)); return c
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "sheet.ods"
            doc = OpenDocumentSpreadsheet()
            table = Table(name="Sales")
            for cells in (["Month", "Revenue"], ["July", "4800"]):
                row = TableRow()
                for v in cells:
                    row.addElement(cell(v))
                table.addElement(row)
            doc.spreadsheet.addElement(table)
            doc.save(str(path))
            md = di.convert(path).markdown
            self.assertIn("Month", md)
            self.assertIn("Revenue", md)
            self.assertIn("4800", md)

    # A valid 1x1 PNG — python-pptx parses the header for dimensions, so the bytes
    # must be a real image, not a stub.
    _PNG_1x1 = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
        "890000000d4944415478da6360000002000154a24f5f0000000049454e44ae426082")

    def test_pptx_embedded_image_is_extracted_and_stored(self):
        import io
        from pptx import Presentation
        from pptx.util import Inches
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "deck.pptx"
            prs = Presentation()
            slide = prs.slides.add_slide(prs.slide_layouts[6])   # blank
            slide.shapes.add_picture(io.BytesIO(self._PNG_1x1), Inches(1), Inches(1))
            prs.save(str(path))
            r = di.convert(path)
            self.assertEqual(len(r.images), 1)
            self.assertEqual(r.images[0].data, self._PNG_1x1)
            with tempfile.TemporaryDirectory() as vault:
                body = di.save_to_vault(r, vault, vault_root=vault).read_text()
                self.assertNotIn("mvattach:", body)
                m = re.search(r"!\[[^\]]*\]\(([^)]+)\)", body)
                self.assertIsNotNone(m, body)
                self.assertTrue((Path(vault) / m.group(1)).exists())

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
