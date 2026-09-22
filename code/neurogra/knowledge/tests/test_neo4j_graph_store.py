from __future__ import annotations

import unittest

from neurogra.knowledge.graph_store.neo4j import build_neo4j_payload
from neurogra.knowledge.schemas.common import SourceRole
from neurogra.knowledge.schemas.graph import ClauseRevision, Entity
from neurogra.knowledge.schemas.lifecycle import ReleaseManifest
from neurogra.knowledge.schemas.source import Document, EvidenceRef, SourceLocator, SourceSpan
from neurogra.knowledge.schemas.text import Chunk


class Neo4jGraphStoreTests(unittest.TestCase):
    def test_build_payload_uses_domain_graph_without_release_or_document_nodes(self) -> None:
        document = Document(
            document_id="doc_1",
            checksum="checksum",
            source_path="source.pdf",
            snapshot_path="snapshot.pdf",
            source_role=SourceRole.CLINICAL_GUIDELINE,
            title="指南",
        )
        span = SourceSpan(
            span_id="span_1",
            parse_id="parse_1",
            document_id="doc_1",
            locators=[SourceLocator(block_id="block_1", start=0, end=4)],
            original_text="AD 源性MCI",
            normalized_text="AD 源性MCI",
        )
        chunk = Chunk(
            chunk_id="chunk_1",
            chunk_build_id="build_1",
            document_id="doc_1",
            span_ids=["span_1"],
            role="child",
            original_text="AD 源性MCI 是早期阶段。",
            retrieval_text="AD 源性MCI 是早期阶段。",
            token_count=10,
            tokenizer_version="test",
            text_review_status="approved",
        )
        subject = Entity(
            entity_id="entity_subject",
            canonical_name="AD 源性轻度认知障碍",
            entity_type="syndrome",
            terminology_version="local_v1",
            status="approved",
        )
        object_entity = Entity(
            entity_id="entity_object",
            canonical_name="阿尔茨海默病",
            entity_type="disease",
            terminology_version="local_v1",
            status="approved",
        )
        revision = ClauseRevision(
            clause_id="clause_1",
            revision_id="clause_1:1",
            candidate_id="candidate_1",
            subject_entity_id=subject.entity_id,
            object_entity_id=object_entity.entity_id,
            predicate="supports",
            direction="supporting",
            modality="recommended",
            condition_executable=True,
            evidence_refs=[EvidenceRef(span_id="span_1", start=0, end=4, quote="AD 源性MCI")],
            assertion_text="AD 源性MCI 支持阿尔茨海默病诊断。",
            review_status="approved",
            extraction_run_id="run_1",
        )
        manifest = ReleaseManifest(
            release_id="release_1",
            document_ids=["doc_1"],
            parse_ids=["parse_1"],
            clause_revision_ids=[revision.revision_id],
            chunk_ids=[chunk.chunk_id],
            terminology_version="local_v1",
            config_hash="cfg",
            checks={"has_approved_chunks": True},
        )

        payload = build_neo4j_payload(
            manifest=manifest,
            chunks=[chunk],
            revisions=[revision],
            entities=[subject, object_entity],
            spans=[span],
            documents=[document],
            span_sources={
                "span_1": {
                    "page_no": 3,
                    "printed_page_label": "3",
                    "block_id": "block_1",
                }
            },
            candidate_chunk_ids={"candidate_1": "chunk_1"},
        )

        self.assertFalse(hasattr(payload, "release"))
        self.assertFalse(hasattr(payload, "documents"))
        self.assertEqual(payload.medical_entities[0]["name"], "阿尔茨海默病源性轻度认知障碍")
        self.assertEqual(payload.medical_entities[0]["entity_type"], "综合征")
        self.assertEqual(payload.knowledge_claims[0]["claim_text"], "AD 源性MCI 支持阿尔茨海默病诊断。")
        self.assertNotIn("source_chunk_id", payload.knowledge_claims[0])
        self.assertNotIn("release_id", payload.knowledge_claims[0])
        self.assertNotIn("terminology_version", payload.medical_entities[0])
        self.assertNotIn("curation_status", payload.medical_entities[0])
        self.assertNotIn("release_id", payload.medical_entities[0])
        self.assertEqual(payload.evidence_items[0]["source_title"], "指南")
        self.assertEqual(payload.evidence_items[0]["page_no"], 3)
        self.assertNotIn("release_id", payload.evidence_items[0])
        self.assertEqual(payload.text_chunks[0]["page_numbers"], [3])
        self.assertNotIn("release_id", payload.text_chunks[0])
        self.assertEqual(payload.entity_assertions[0]["claim_id"], "clause_1:1")
        self.assertNotIn("release_id", payload.entity_assertions[0])
        self.assertEqual(payload.entity_assertions[0]["relation_type"], "支持")
        self.assertEqual(payload.claim_evidence, [{"claim_id": "clause_1:1", "evidence_id": "span_1"}])
        self.assertEqual(payload.chunk_evidence, [{"chunk_id": "chunk_1", "evidence_id": "span_1"}])
        self.assertEqual(payload.claim_chunks, [{"claim_id": "clause_1:1", "chunk_id": "chunk_1"}])


if __name__ == "__main__":
    unittest.main()
