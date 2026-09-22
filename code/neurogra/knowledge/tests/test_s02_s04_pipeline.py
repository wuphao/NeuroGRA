from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pypdf import PdfWriter

from neurogra.knowledge.config import BuildConfig
from neurogra.knowledge.ingestion.registry import RegistrationMetadata, register_pdf
from neurogra.knowledge.parsing.base import ParsedDocument
from neurogra.knowledge.parsing.simple_pdf import BasicPdfParser
from neurogra.knowledge.processing.cleaner import build_spans
from neurogra.knowledge.schemas.common import ExtractionMethod
from neurogra.knowledge.schemas.source import ParsedBlock
from neurogra.knowledge.storage.sqlite import init_store


def make_config(root: Path) -> BuildConfig:
    return BuildConfig.model_validate(
        {
            "schema_version": "1",
            "paths": {
                "data_root": str(root / "data" / "knowledge"),
                "output_root": str(root / "output" / "knowledge"),
            },
            "database": {"path": str(root / "data" / "knowledge" / "registry.sqlite")},
        }
    )


def write_blank_pdf(path: Path) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    with path.open("wb") as handle:
        writer.write(handle)


class S02S04PipelineTests(unittest.TestCase):
    def test_register_pdf_deduplicates_by_checksum(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            pdf_path = root / "source.pdf"
            write_blank_pdf(pdf_path)
            config = make_config(root)
            metadata = RegistrationMetadata(source_role="clinical_guideline", title="Demo")
            with init_store(config) as repo:
                first = register_pdf(pdf_path, metadata, config, repo)
                second = register_pdf(pdf_path, metadata, config, repo)

            self.assertFalse(first.is_duplicate)
            self.assertTrue(first.snapshot_written)
            self.assertTrue(second.is_duplicate)
            self.assertFalse(second.snapshot_written)
            self.assertTrue(Path(first.document.snapshot_path).exists())

    def test_basic_parser_records_empty_page_issue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            pdf_path = root / "source.pdf"
            write_blank_pdf(pdf_path)
            config = make_config(root)
            metadata = RegistrationMetadata(source_role="clinical_guideline")
            with init_store(config) as repo:
                result = register_pdf(pdf_path, metadata, config, repo)
                parsed = BasicPdfParser().parse(result.document, config.parsing)

            self.assertEqual(parsed.document_id, result.document.document_id)
            self.assertEqual(len(parsed.blocks), 1)
            self.assertIn("page_1:no_native_text", parsed.issues)
            self.assertIn("ocr_required", parsed.blocks[0].issues)

    def test_build_spans_preserves_raw_text_and_normalizes_spaces(self) -> None:
        block = ParsedBlock(
            block_id="block_1",
            parse_id="parse_1",
            document_id="doc_1",
            page_no=1,
            reading_order=0,
            block_type="paragraph",
            raw_text="  1. 标题  \n正文   内容  ",
            extraction_method=ExtractionMethod.NATIVE_TEXT,
        )
        parsed = ParsedDocument(
            document_id="doc_1",
            parse_id="parse_1",
            parser_name="test",
            parser_version="1",
            config_hash="cfg_1",
            blocks=[block],
        )
        result = build_spans(parsed)

        self.assertEqual(len(result.spans), 1)
        span = result.spans[0]
        self.assertEqual(span.original_text, block.raw_text)
        self.assertEqual(span.normalized_text, "1. 标题\n正文 内容")
        self.assertEqual(span.locators[0].block_id, "block_1")
        self.assertEqual(span.locators[0].start, 0)
        self.assertEqual(span.locators[0].end, len(block.raw_text))
        self.assertTrue(span.normalization_map)


if __name__ == "__main__":
    unittest.main()
