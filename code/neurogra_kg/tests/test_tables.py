import json
import tempfile
import unittest
from pathlib import Path

from neurogra_kg.construction.chunking import split_document_units
from neurogra_kg.construction.extraction import extraction_units
from neurogra_kg.construction.parsing import DocumentParser
from neurogra_kg.construction.alignment import candidate_pairs
from neurogra_kg.contracts.domain import Block, Document, DocumentInput, ParsedDocument, StructureEdge, TableCell
from neurogra_kg.contracts.tasks import ExtractedStatement, ExtractedCondition


class TableTests(unittest.TestCase):
    def test_merged_row_span_is_never_split_between_windows(self):
        from neurogra_kg.construction.table_chunks import split_table
        parsed = self.document()
        table = parsed.blocks[0]
        next(cell for cell in table.cells if cell.row == 1 and cell.column == 0).row_span = 4
        fragments, _ = split_table(table, 80)
        bodies = [b for b in fragments if b.kind == 'table']
        owner = next(b for b in bodies if any(c.row == 1 for c in b.cells))
        self.assertTrue({1, 2, 3, 4} <= {c.row for c in owner.cells})
        self.assertEqual(''.join(b.text for b in bodies), table.text)
        self.assertEqual(next(c for c in owner.cells if c.row == 1 and c.column == 0).row_span, 4)

    def test_condition_in_merged_cell_covers_each_spanned_row(self):
        from neurogra_kg.construction.table_chunks import referenced_rows
        parsed = self.document()
        table = parsed.blocks[0]
        cell = next(c for c in table.cells if c.row == 1 and c.column == 1)
        cell.row_span = 4
        from neurogra_kg.contracts.tasks import QuoteRef
        rows, headers = referenced_rows(table, [QuoteRef(block_id='table', quote=cell.text, occurrence=0)])
        self.assertEqual(rows, {1, 2, 3, 4})
        self.assertFalse(headers)

    def document(self):
        cells = [TableCell(row=row, column=col, text=value, is_header=row == 0)
            for row, values in enumerate([["Marker", "Condition"], *[["Marker" + str(i), "condition " + "x" * 55] for i in range(10)]])
            for col, value in enumerate(values)]
        text = "\n".join("\t".join(c.text for c in cells if c.row == row) for row in range(11))
        table = Block(block_id="table", document_id="d", page=1, order=0, kind="table", text=text, cells=cells)
        note = Block(block_id="note", document_id="d", page=1, order=1, kind="footnote", text="* method M", marker="*")
        return ParsedDocument(document=Document(document_id="d", checksum="x", source_path="x", snapshot_path="x", title="x", version="1"),
            blocks=[table, note], parser="fixture", parser_version="1",
            edges=[StructureEdge(source="table", target="note", relation="NOTE_OF", certainty="explicit", reason="fixture")])

    def test_whole_rows_cells_headers_and_notes_survive_chunking(self):
        parsed = self.document()
        original = parsed.blocks[0].text
        split_document_units(parsed, 600)
        fragments = [b for b in parsed.blocks if b.kind == "table" and b.fragment_of]
        self.assertGreater(len(fragments), 1)
        self.assertEqual("".join(b.text for b in fragments), original)
        self.assertEqual(sum(len(b.cells) for b in fragments), 22)
        for fragment in fragments:
            self.assertEqual(fragment.text, original[fragment.source_start:fragment.source_end])
        units = list(extraction_units(parsed, 600))
        self.assertTrue(all(issue is None for _, _, issue in units))
        self.assertTrue(all(any("table_header" in b.issues for b in blocks) for _, blocks, _ in units))
        self.assertTrue(all(any(b.block_id == "note" for b in blocks) for _, blocks, _ in units))
        count = len(parsed.blocks)
        split_document_units(parsed, 600)
        self.assertEqual(len(parsed.blocks), count)

    def test_different_data_rows_do_not_become_candidate_pairs(self):
        parsed = self.document(); block = parsed.blocks[0]
        ref = lambda quote: {"block_id": "table", "quote": quote, "occurrence": 0}
        statement = ExtractedStatement(local_id="s", subject_text="Marker0", subject_type="other", predicate="supports",
            object_text="state", object_type="other", assertion_text="fixture", purposes=[], condition_status="unresolved",
            source_refs=[ref("Marker0")], field_evidence={})
        other = ExtractedCondition(local_id="c", condition_text="other-row", category="other", scope_hint="table",
            source_refs=[ref("Marker1")])
        header = ExtractedCondition.model_validate({**other.model_dump(mode="json"), "source_refs": [ref("Condition")]})
        self.assertFalse(candidate_pairs([statement], [other], {"table": block}, []))
        self.assertTrue(candidate_pairs([statement], [header], {"table": block}, []))

    def test_row_marker_is_not_promoted_to_fixed_reference_on_other_windows(self):
        parsed = self.document(); table = parsed.blocks[0]
        table.cells[2].text += "*"
        table.text = "\n".join("\t".join(c.text for c in table.cells if c.row == row) for row in range(11))
        table.reference_markers = ["*"]
        parsed.edges[0].relation = "REFERS_TO"
        split_document_units(parsed, 600)
        fragments = [b for b in parsed.blocks if b.kind == "table" and b.fragment_of]
        marked = [b for b in fragments if "*" in b.reference_markers]
        self.assertEqual(len(marked), 1)
        for block in fragments:
            edges = [e for e in parsed.edges if e.source == block.block_id and e.target == "note"]
            if block not in marked:
                self.assertTrue(edges and all(e.certainty == "heuristic" and e.relation == "NOTE_OF" for e in edges))

    def test_structured_cross_page_edge_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "doc.json"
            path.write_text(json.dumps({"blocks": [
                {"block_id": "a", "page": 1, "kind": "paragraph", "text": "A ends."},
                {"block_id": "b", "page": 2, "kind": "paragraph", "text": "Continued constraint."}],
                "edges": [{"source": "a", "target": "b", "relation": "CONTINUES", "certainty": "explicit", "reason": "source continuation"}]}))
            parsed = DocumentParser(Path(tmp) / "data").parse(DocumentInput(path=path))
            units = list(extraction_units(parsed, 600))
            self.assertEqual(len(units[1][1]), 2)

    def test_real_pdf_table_headers_notes_and_cross_page_context(self):
        import fitz
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tables.pdf"
            pdf = fitz.open()
            for index, y in enumerate([560, 40]):
                page = pdf.new_page(width=595, height=842)
                columns = [40, 180, 350, 550]
                values = [["Marker", "Threshold", "Meaning"],
                    ["X" if index == 0 else "Y", "> 10", "Supports D"], ["Z", ">= 18", "Supports E"]]
                for row in range(4):
                    page.draw_line((40, y+row*35), (550, y+row*35))
                for x in columns:
                    page.draw_line((x, y), (x, y+105))
                for row, cells in enumerate(values):
                    for col, text in enumerate(cells):
                        page.insert_text((columns[col]+8, y+row*35+22), text, fontsize=11)
                if index == 0:
                    page.insert_text((45, y+123), "* Method M is required.", fontsize=9)
            pdf.save(path); pdf.close()
            parsed = DocumentParser(Path(tmp) / "data").parse(DocumentInput(path=path))
            tables = [b for b in parsed.blocks if b.kind == "table"]
            self.assertEqual(len(tables), 2)
            self.assertTrue(all(len(b.cells) == 9 for b in tables))
            self.assertTrue(any(e.relation == "NOTE_OF" for e in parsed.edges))
            self.assertTrue(any(e.relation == "CONTINUES" for e in parsed.edges))
            headers = [e for e in parsed.edges if e.relation == "HEADER_OF"]
            self.assertTrue(headers and all(e.certainty == "heuristic" for e in headers))
            units = list(extraction_units(parsed, 6000))
            second = next(blocks for id, blocks, issue in units if id == tables[1].block_id)
            self.assertTrue(any(b.page == 1 and "table_header" in b.issues for b in second))
            self.assertTrue(any(b.page == 1 and b.kind == "footnote" for b in second))
            self.assertTrue(all(e.certainty == "heuristic" for e in parsed.edges
                if "cross_page_" in e.reason))
