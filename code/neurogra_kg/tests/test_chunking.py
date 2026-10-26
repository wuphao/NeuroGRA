import unittest

from neurogra_kg.construction.chunking import split_document_units
from neurogra_kg.construction.extraction import extraction_units
from neurogra_kg.contracts.domain import Block, Document, ParsedDocument, StructureEdge


class ChunkingTests(unittest.TestCase):
    def test_fragments_cover_original_without_truncation(self):
        text = ("Sentence with a condition. " * 80) + "Final exclusion."
        body = Block(block_id="body", document_id="d", page=1, order=0, kind="paragraph", text=text)
        note = Block(block_id="note", document_id="d", page=1, order=1, kind="footnote", text="* exclusion", marker="*")
        parsed = ParsedDocument(document=Document(document_id="d", checksum="x", source_path="x",
            snapshot_path="x", title="x", version="1"), blocks=[body, note], parser="fixture", parser_version="1",
            edges=[StructureEdge(source="body", target="note", relation="REFERS_TO", certainty="explicit", reason="marker")])
        split_document_units(parsed, 600)
        fragments = [block for block in parsed.blocks if block.fragment_of == "body"]
        self.assertGreater(len(fragments), 1)
        self.assertEqual("".join(block.text for block in fragments), text)
        for block in fragments:
            self.assertEqual(block.text, text[block.source_start:block.source_end])
        units = list(extraction_units(parsed, 600))
        self.assertEqual(len(units), len(fragments))
        self.assertTrue(all(issue is None for _, _, issue in units))
        self.assertTrue(all(any(block.block_id == "note" for block in blocks) for _, blocks, _ in units))


if __name__ == "__main__":
    unittest.main()
