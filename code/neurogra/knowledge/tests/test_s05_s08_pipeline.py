from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from neurogra.knowledge.config import BuildConfig
from neurogra.knowledge.extraction.extractor import RuleBasedExtractor, context_from_chunk
from neurogra.knowledge.extraction.linker import normalize_candidates
from neurogra.knowledge.indexing.lexical import SearchRequest, build_text_index, search_text
from neurogra.knowledge.parsing.base import ParsedDocument
from neurogra.knowledge.processing.segmenter import build_chunks
from neurogra.knowledge.schemas.common import ExtractionMethod
from neurogra.knowledge.schemas.graph import CandidateClause
from neurogra.knowledge.schemas.source import Document, ParsedBlock, SourceLocator, SourceSpan
from neurogra.knowledge.storage.sqlite import init_store


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


def span(text: str, span_id: str = "span_1") -> SourceSpan:
    return SourceSpan(
        span_id=span_id,
        parse_id="parse_1",
        document_id="doc_1",
        section_path=["诊断"],
        locators=[SourceLocator(block_id="block_1", start=0, end=len(text))],
        original_text=text,
        normalized_text=text,
    )


class S05S08PipelineTests(unittest.TestCase):
    def test_build_chunks_creates_parent_and_child(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            cfg = config(Path(tmp_dir))
            result = build_chunks([span("AD 源性MCI 与 阿尔茨海默病 相关。")], cfg.chunking)
        self.assertEqual(len(result.chunks), 2)
        roles = {chunk.role for chunk in result.chunks}
        self.assertEqual(roles, {"parent", "child"})
        child = [chunk for chunk in result.chunks if chunk.role == "child"][0]
        self.assertIsNotNone(child.parent_chunk_id)
        self.assertEqual(child.span_ids, ["span_1"])

    def test_bm25_index_returns_matching_child_chunk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            cfg = config(root)
            chunk_result = build_chunks([span("AD 源性MCI 需要关注 阿尔茨海默病。")], cfg.chunking)
            index_path = root / "index.json"
            build_text_index(chunk_result.chunks, cfg.retrieval, index_path)
            hits = search_text(SearchRequest(query="阿尔茨海默病", top_k=1), chunk_result.chunks, index_path)
        self.assertEqual(len(hits), 1)
        self.assertIn("阿尔茨海默病", hits[0].retrieval_text)

    def test_rule_extraction_and_normalization_produce_pending_clause(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            cfg = config(Path(tmp_dir))
            chunk_result = build_chunks([span("AD 源性MCI 是 阿尔茨海默病 连续疾病谱的早期阶段。")], cfg.chunking)
            child = [chunk for chunk in chunk_result.chunks if chunk.role == "child"][0]
            extraction = RuleBasedExtractor().extract(context_from_chunk(child))
            normalized = normalize_candidates(extraction.candidates)

        self.assertGreaterEqual(len(extraction.candidates), 1)
        self.assertEqual(extraction.candidates[0].subject_text, "AD 源性MCI")
        self.assertGreaterEqual(len(normalized.entities), 2)
        self.assertEqual(len(normalized.clause_revisions), len(extraction.candidates))
        self.assertEqual(normalized.clause_revisions[0].review_status, "pending")

    def test_local_pending_entity_ids_are_stable(self) -> None:
        candidate = CandidateClause(
            candidate_id="candidate_unknown",
            extraction_run_id="run_unknown",
            chunk_id="chunk_unknown",
            subject_text="罕见综合征",
            subject_type="syndrome",
            object_text="未知检查",
            object_type="examination",
            predicate="associated_with",
            direction="neutral",
            modality="unspecified",
            evidence_refs=[{"span_id": "span_1", "start": 0, "end": 4, "quote": "测试证据"}],
            assertion_text="罕见综合征 与 未知检查 在同一证据片段中相关。",
        )
        first = normalize_candidates([candidate])
        second = normalize_candidates([candidate])
        self.assertEqual(
            [entity.entity_id for entity in first.entities],
            [entity.entity_id for entity in second.entities],
        )

    def test_s05_s08_storage_roundtrip(self) -> None:
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
                block = ParsedBlock(
                    block_id="block_1",
                    parse_id="parse_1",
                    document_id="doc_1",
                    page_no=1,
                    reading_order=0,
                    block_type="paragraph",
                    raw_text="AD 源性MCI 与 阿尔茨海默病 相关。",
                    extraction_method=ExtractionMethod.NATIVE_TEXT,
                )
                parsed = ParsedDocument(
                    document_id="doc_1",
                    parse_id="parse_1",
                    parser_name="test",
                    parser_version="1",
                    config_hash="cfg",
                    blocks=[block],
                )
                repo.save_parsed_document(parsed)
                span_obj = span("AD 源性MCI 与 阿尔茨海默病 相关。")
                repo.save_spans([span_obj])
                chunk_result = build_chunks([span_obj], cfg.chunking)
                repo.save_chunks(chunk_result.chunks)
                child = [chunk for chunk in chunk_result.chunks if chunk.role == "child"][0]
                extraction = RuleBasedExtractor().extract(context_from_chunk(child))
                repo.save_candidates(extraction.candidates)
                normalized = normalize_candidates(repo.get_candidates_for_chunk_build(chunk_result.chunk_build_id))
                repo.save_normalization_result(normalized)

                self.assertEqual(len(repo.get_chunks_for_build(chunk_result.chunk_build_id)), 2)
                self.assertEqual(len(repo.get_candidates_for_chunk_build(chunk_result.chunk_build_id)), len(extraction.candidates))


if __name__ == "__main__":
    unittest.main()
