from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from neurogra.knowledge.config import BuildConfig
from neurogra.knowledge.extraction.extractor import RuleBasedExtractor, context_from_chunk
from neurogra.knowledge.extraction.linker import normalize_candidates
from neurogra.knowledge.ingestion.registry import RegistrationMetadata, register_pdf
from neurogra.knowledge.parsing.base import ParsedDocument
from neurogra.knowledge.processing.segmenter import build_chunks
from neurogra.knowledge.release.service import ReleaseSelection, activate_release, build_release
from neurogra.knowledge.retrieval.service import search
from neurogra.knowledge.review.service import apply_decisions, export_review_batch
from neurogra.knowledge.schemas.common import ExtractionMethod
from neurogra.knowledge.schemas.lifecycle import ReviewDecision
from neurogra.knowledge.schemas.source import Document, ParsedBlock, SourceLocator, SourceSpan
from neurogra.knowledge.storage.sqlite import init_store
from neurogra.knowledge.validation.validator import validate_revisions


def config(root: Path) -> BuildConfig:
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


class S09S12PipelineTests(unittest.TestCase):
    def test_validate_review_release_and_search(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            cfg = config(root)
            with init_store(cfg) as repo:
                document = Document(
                    document_id="doc_1",
                    checksum="checksum",
                    source_path="source.pdf",
                    snapshot_path="snapshot.pdf",
                    source_role="clinical_guideline",
                )
                repo.upsert_document(document)
                text = "AD 源性MCI 是 阿尔茨海默病 连续疾病谱的早期阶段。"
                block = ParsedBlock(
                    block_id="block_1",
                    parse_id="parse_1",
                    document_id="doc_1",
                    page_no=1,
                    reading_order=0,
                    block_type="paragraph",
                    raw_text=text,
                    extraction_method=ExtractionMethod.NATIVE_TEXT,
                )
                parsed_document = ParsedDocument(
                    document_id="doc_1",
                    parse_id="parse_1",
                    parser_name="test",
                    parser_version="1",
                    config_hash="cfg",
                    blocks=[block],
                )
                repo.save_parsed_document(parsed_document)
                span = SourceSpan(
                    span_id="span_1",
                    parse_id="parse_1",
                    document_id="doc_1",
                    locators=[SourceLocator(block_id="block_1", start=0, end=len(text))],
                    original_text=text,
                    normalized_text=text,
                )
                repo.save_spans([span])
                chunk_result = build_chunks([span], cfg.chunking)
                repo.save_chunks(chunk_result.chunks)
                child = [chunk for chunk in chunk_result.chunks if chunk.role == "child"][0]
                extraction = RuleBasedExtractor().extract(context_from_chunk(child))
                repo.save_candidates(extraction.candidates)
                normalization = normalize_candidates(extraction.candidates)
                repo.save_normalization_result(normalization)

                reports = validate_revisions(normalization.clause_revisions, repo)
                self.assertFalse(any(report.has_errors for report in reports))

                batch = export_review_batch(repo, chunk_result.chunk_build_id, "tester")
                review_result = apply_decisions(repo, batch.decisions)
                self.assertEqual(review_result.blocked_count, 0)

                release_result = build_release(
                    ReleaseSelection(chunk_build_id=chunk_result.chunk_build_id),
                    cfg,
                    repo,
                )
                self.assertFalse(release_result.issues)
                activate_release(repo, release_result.release_id)
                activate_release(repo, release_result.release_id)
                second_release_result = build_release(
                    ReleaseSelection(chunk_build_id=chunk_result.chunk_build_id),
                    cfg,
                    repo,
                )
                self.assertFalse(second_release_result.issues)
                self.assertEqual(repo.release_status(release_result.release_id), "active")
                response = search(repo, "AD 源性MCI", top_k=1)
                repo.save_parsed_document(parsed_document)
                self.assertEqual(repo.get_spans_for_parse("parse_1"), [])

        self.assertEqual(response.release_id, release_result.release_id)
        self.assertEqual(len(response.hits), 1)
        self.assertEqual(response.hits[0].citations[0].parse_id, "parse_1")

    def test_chunk_amend_review_decision_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            cfg = config(Path(tmp_dir))
            with init_store(cfg) as repo:
                decision = ReviewDecision(
                    decision_id="d1",
                    object_type="chunk",
                    object_id="missing_chunk",
                    action="amend",
                    reviewer_id="tester",
                    decided_at="2026-09-21T00:00:00Z",
                )
                result = apply_decisions(repo, [decision])

        self.assertEqual(result.applied_count, 0)
        self.assertEqual(result.blocked_count, 1)


if __name__ == "__main__":
    unittest.main()
