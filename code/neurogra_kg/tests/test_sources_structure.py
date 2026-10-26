import json
import tempfile
import unittest
from pathlib import Path

from neurogra_kg.construction.parsing import DocumentParser
from neurogra_kg.construction.sources import resolve_quote, validate_ref
from neurogra_kg.construction.structure import build_structure
from neurogra_kg.contracts.domain import Block, DocumentInput
from neurogra_kg.contracts.tasks import QuoteRef


class SourceStructureTests(unittest.TestCase):
    def test_repeated_quote_resolves_exact_occurrence(self):
        block = Block(block_id="b", document_id="d", page=1, order=0, kind="paragraph", text="条件甲；条件甲")
        ref = resolve_quote(QuoteRef(block_id="b", quote="条件甲", occurrence=1), {"b": block})
        self.assertEqual(ref.start, 4)
        validate_ref(ref, {"b": block})
        with self.assertRaisesRegex(ValueError, "not_found"):
            resolve_quote(QuoteRef(block_id="b", quote="不存在", occurrence=0), {"b": block})

    def test_footnote_binding_does_not_cross_page(self):
        blocks = [Block(block_id="body", document_id="d", page=1, order=0, kind="paragraph",
                        text="声明*", reference_markers=["*"]),
                  Block(block_id="note", document_id="d", page=2, order=0, kind="footnote",
                        text="* 条件", marker="*")]
        edges = build_structure(blocks)
        self.assertFalse(any(edge.relation == "REFERS_TO" for edge in edges))
        self.assertIn("unresolved_footnote_marker:*", blocks[0].issues)

    def test_structured_example_explicit_footnote_and_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(__file__).resolve().parents[1] / "examples/document.json"
            parsed = DocumentParser(Path(tmp)).parse(DocumentInput(path=path))
            self.assertEqual(len(parsed.blocks), 3)
            edge = next(edge for edge in parsed.edges if edge.relation == "REFERS_TO")
            self.assertEqual(edge.certainty, "explicit")
            self.assertTrue(Path(parsed.document.snapshot_path).is_file())

    def test_layout_pdf_has_page_and_quote(self):
        import fitz
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.pdf"
            pdf = fitz.open()
            page = pdf.new_page()
            page.insert_text((50, 60), "1 Example rules", fontsize=18)
            page.insert_text((50, 100), "Marker X supports state D under method M.", fontsize=11)
            pdf.save(path)
            pdf.close()
            parsed = DocumentParser(Path(tmp) / "data").parse(DocumentInput(path=path))
            self.assertTrue(any(block.kind == "heading" for block in parsed.blocks))
            body = next(block for block in parsed.blocks if "supports" in block.text)
            self.assertEqual(body.page, 1)
            self.assertIsNotNone(body.bbox)
            ref = resolve_quote(QuoteRef(block_id=body.block_id, quote="supports state D", occurrence=0),
                                {block.block_id: block for block in parsed.blocks})
            validate_ref(ref, {body.block_id: body})


if __name__ == "__main__":
    unittest.main()
