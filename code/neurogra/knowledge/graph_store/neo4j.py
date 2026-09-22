"""Neo4j writer for the minimal business knowledge graph.

The graph contains only four Chinese labels: ``医学实体``, ``知识断言``,
``证据`` and ``文本块``. Build metadata, review metadata and release
metadata stay in SQLite/artifacts; they are deliberately not copied into the
graph. Source title/path/page/section/text remain on ``Evidence`` and
``TextChunk`` so every claim can still be traced back to the PDF.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from neurogra.knowledge.config import GraphStoreConfig
from neurogra.knowledge.schemas.graph import ClauseRevision, Entity
from neurogra.knowledge.schemas.lifecycle import ReleaseManifest
from neurogra.knowledge.schemas.source import Document, SourceSpan
from neurogra.knowledge.schemas.text import Chunk
from neurogra.knowledge.utils import stable_json_dumps


NODE_LABELS = {
    "entity": "医学实体",
    "claim": "知识断言",
    "evidence": "证据",
    "chunk": "文本块",
}

RELATION_TYPES = {
    "subject": "主体",
    "object": "客体",
    "supported_by": "有证据",
    "extracted_from": "来源于",
    "contains_evidence": "包含证据",
    "asserts": "表达",
}


@dataclass(frozen=True)
class Neo4jGraphPayload:
    medical_entities: list[dict[str, Any]]
    knowledge_claims: list[dict[str, Any]]
    evidence_items: list[dict[str, Any]]
    text_chunks: list[dict[str, Any]]
    entity_assertions: list[dict[str, Any]]
    claim_evidence: list[dict[str, str]]
    chunk_evidence: list[dict[str, str]]
    claim_chunks: list[dict[str, str]]
    release_id: str


def build_neo4j_payload(
    manifest: ReleaseManifest,
    chunks: list[Chunk],
    revisions: list[ClauseRevision],
    entities: list[Entity],
    spans: list[SourceSpan],
    documents: list[Document],
    span_sources: dict[str, dict[str, Any]] | None = None,
    candidate_chunk_ids: dict[str, str] | None = None,
) -> Neo4jGraphPayload:
    """Build Neo4j rows with domain-centered labels and meaningful properties."""

    span_sources = span_sources or {}
    candidate_chunk_ids = candidate_chunk_ids or {}
    documents_by_id = {document.document_id: document for document in documents}
    spans_by_id = {span.span_id: span for span in spans}
    chunks_by_id = {chunk.chunk_id: chunk for chunk in chunks}

    medical_entities = [_entity_row(entity) for entity in entities]
    evidence_items = [
        _evidence_row(span, documents_by_id.get(span.document_id), span_sources.get(span.span_id, {}))
        for span in spans
    ]
    text_chunks = [
        _chunk_row(chunk, documents_by_id.get(chunk.document_id), span_sources)
        for chunk in chunks
    ]
    knowledge_claims = [
        _claim_row(
            revision,
        )
        for revision in revisions
    ]
    entity_assertions = [
        {
            "claim_id": revision.revision_id,
            "subject_entity_id": revision.subject_entity_id,
            "object_entity_id": revision.object_entity_id,
            "predicate": revision.predicate,
            "relation_type": _predicate_zh(revision.predicate),
            "direction": revision.direction,
            "diagnostic_level": revision.diagnostic_level,
            "modality": revision.modality,
            "claim_text": revision.assertion_text,
            "condition_json": _model_or_none_to_json(revision.condition),
            "exceptions_json": _models_to_json(revision.exceptions),
        }
        for revision in revisions
    ]
    claim_evidence = [
        {"claim_id": revision.revision_id, "evidence_id": evidence.span_id}
        for revision in revisions
        for evidence in revision.evidence_refs
        if evidence.span_id in spans_by_id
    ]
    chunk_evidence = [
        {"chunk_id": chunk.chunk_id, "evidence_id": span_id}
        for chunk in chunks
        for span_id in chunk.span_ids
        if span_id in spans_by_id
    ]
    claim_chunks = [
        {"claim_id": revision.revision_id, "chunk_id": chunk_id}
        for revision in revisions
        if (chunk_id := candidate_chunk_ids.get(revision.candidate_id)) in chunks_by_id
    ]
    return Neo4jGraphPayload(
        medical_entities=medical_entities,
        knowledge_claims=knowledge_claims,
        evidence_items=evidence_items,
        text_chunks=text_chunks,
        entity_assertions=entity_assertions,
        claim_evidence=claim_evidence,
        chunk_evidence=chunk_evidence,
        claim_chunks=claim_chunks,
        release_id=manifest.release_id,
    )


class Neo4jReleaseWriter:
    """Idempotently publish a validated knowledge graph to Neo4j."""

    def __init__(
        self,
        config: GraphStoreConfig,
        driver_factory: Callable[..., Any] | None = None,
    ):
        if config.provider != "neo4j":
            raise ValueError("Neo4jReleaseWriter requires graph_store.provider=neo4j")
        if driver_factory is None:
            try:
                from neo4j import GraphDatabase
            except ImportError as exc:
                raise RuntimeError(
                    "Neo4j graph_store is configured, but the 'neo4j' Python package is not installed. "
                    "Install project dependencies before running release-build with Neo4j."
                ) from exc
            driver_factory = GraphDatabase.driver
        password = config.resolve_password()
        if password is None:
            raise ValueError("Neo4j password is not available from graph_store.password/password_env")
        self._driver = driver_factory(config.uri, auth=(config.username, password))
        self._database = config.database

    def close(self) -> None:
        self._driver.close()

    def __enter__(self) -> "Neo4jReleaseWriter":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def publish(self, payload: Neo4jGraphPayload) -> str:
        with self._driver.session(database=self._database) as session:
            session.execute_write(_ensure_constraints)
            session.execute_write(_cleanup_legacy_shape)
            session.execute_write(_write_payload, payload)
        return payload.release_id


def _entity_row(entity: Entity) -> dict[str, Any]:
    return {
        "entity_id": entity.entity_id,
        "name": _chinese_entity_name(entity.canonical_name),
        "entity_type": _entity_type_zh(entity.entity_type),
        "aliases": entity.aliases,
        "external_ids_json": json.dumps(entity.external_ids, ensure_ascii=False, sort_keys=True),
    }


def _claim_row(
    revision: ClauseRevision,
) -> dict[str, Any]:
    return {
        "claim_id": revision.revision_id,
        "name": _chinese_display_text(revision.assertion_text),
        "clause_id": revision.clause_id,
        "candidate_id": revision.candidate_id,
        "subject_entity_id": revision.subject_entity_id,
        "object_entity_id": revision.object_entity_id,
        "predicate": revision.predicate,
        "direction": revision.direction,
        "diagnostic_level": revision.diagnostic_level,
        "modality": revision.modality,
        "claim_text": revision.assertion_text,
        "condition_executable": revision.condition_executable,
        "condition_json": _model_or_none_to_json(revision.condition),
        "exceptions_json": _models_to_json(revision.exceptions),
        "evidence_refs_json": _models_to_json(revision.evidence_refs),
        "field_evidence_json": json.dumps(
            {
                field: [_model_to_plain(evidence) for evidence in evidence_refs]
                for field, evidence_refs in revision.field_evidence.items()
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
    }


def _evidence_row(
    span: SourceSpan,
    document: Document | None,
    source: dict[str, Any],
) -> dict[str, Any]:
    return {
        "evidence_id": span.span_id,
        "name": _evidence_name(document, source.get("page_no")),
        "span_id": span.span_id,
        "parse_id": span.parse_id,
        "document_id": span.document_id,
        "source_title": None if document is None else document.title,
        "source_path": None if document is None else document.source_path,
        "source_role": None if document is None else document.source_role.value,
        "page_no": source.get("page_no"),
        "printed_page_label": source.get("printed_page_label"),
        "block_id": source.get("block_id"),
        "section_path": span.section_path,
        "text": span.normalized_text,
        "original_text": span.original_text,
    }


def _chunk_row(
    chunk: Chunk,
    document: Document | None,
    span_sources: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    pages = sorted(
        {
            int(source["page_no"])
            for span_id in chunk.span_ids
            if (source := span_sources.get(span_id)) and source.get("page_no") is not None
        }
    )
    return {
        "chunk_id": chunk.chunk_id,
        "name": _chunk_name(document, pages),
        "document_id": chunk.document_id,
        "source_title": None if document is None else document.title,
        "source_path": None if document is None else document.source_path,
        "role": chunk.role,
        "text": chunk.retrieval_text,
        "original_text": chunk.original_text,
        "section_path": chunk.section_path,
        "page_numbers": pages,
    }


def _ensure_constraints(tx: Any) -> None:
    for statement in (
        "CREATE CONSTRAINT medical_entity_id IF NOT EXISTS FOR (n:`医学实体`) REQUIRE n.entity_id IS UNIQUE",
        "CREATE CONSTRAINT knowledge_claim_id IF NOT EXISTS FOR (n:`知识断言`) REQUIRE n.claim_id IS UNIQUE",
        "CREATE CONSTRAINT evidence_id IF NOT EXISTS FOR (n:`证据`) REQUIRE n.evidence_id IS UNIQUE",
        "CREATE CONSTRAINT text_chunk_id IF NOT EXISTS FOR (n:`文本块`) REQUIRE n.chunk_id IS UNIQUE",
    ):
        tx.run(statement)


def _cleanup_legacy_shape(tx: Any) -> None:
    """Remove properties/nodes from the previous graph projection.

    MERGE + SET n += row does not remove properties that existed on a node
    from an earlier run. Clear those fields so rerunning the builder actually
    produces the minimal schema, and remove the old build-only node labels.
    """
    tx.run(
        """
        MATCH (n)
        WHERE any(label IN labels(n) WHERE label IN [
            'KGRelease', 'KGDocument', 'Document', 'Entity', 'KGChunk',
            'KGClauseRevision', 'KGEntity', 'KGEvidenceSpan', 'KgRagDemo',
            'MedicalEntity', 'KnowledgeClaim', 'Evidence', 'TextChunk'
        ])
        DETACH DELETE n
        """
    )
    tx.run("MATCH ()-[r:ASSERTS|表达]-() DELETE r")
    tx.run(
        """
        MATCH (n:`医学实体`)
        REMOVE n.terminology_version, n.curation_status, n.release_id
        """
    )
    tx.run(
        """
        MATCH (n:`知识断言`)
        REMOVE n.release_id, n.review_status, n.claim_group_id,
               n.extraction_run_id, n.source_chunk_id
        """
    )
    tx.run(
        """
        MATCH (n:`证据`)
        REMOVE n.release_id
        """
    )
    tx.run(
        """
        MATCH (n:`文本块`)
        REMOVE n.release_id, n.chunk_build_id, n.token_count,
               n.tokenizer_version, n.review_status
        """
    )
    tx.run(
        """
        MATCH ()-[r:ASSERTS|表达]-()
        REMOVE r.release_id, r.subject_entity_id, r.object_entity_id, r.claim_id
        """
    )


def _write_payload(tx: Any, payload: Neo4jGraphPayload) -> None:
    _merge_nodes(tx, NODE_LABELS["entity"], "entity_id", payload.medical_entities)
    _merge_nodes(tx, NODE_LABELS["claim"], "claim_id", payload.knowledge_claims)
    _merge_nodes(tx, NODE_LABELS["evidence"], "evidence_id", payload.evidence_items)
    _merge_nodes(tx, NODE_LABELS["chunk"], "chunk_id", payload.text_chunks)

    tx.run(
        """
        UNWIND $rows AS row
        MATCH (claim:`知识断言` {claim_id: row.claim_id})
        MATCH (subject:`医学实体` {entity_id: row.subject_entity_id})
        MATCH (object:`医学实体` {entity_id: row.object_entity_id})
        MERGE (claim)-[:主体]->(subject)
        MERGE (claim)-[:客体]->(object)
        MERGE (subject)-[assertion:表达]->(object)
        SET assertion.claim_ids = CASE
            WHEN row.claim_id IN coalesce(assertion.claim_ids, []) THEN assertion.claim_ids
            ELSE coalesce(assertion.claim_ids, []) + row.claim_id END,
            assertion.relation_types = CASE
            WHEN row.relation_type IN coalesce(assertion.relation_types, []) THEN assertion.relation_types
            ELSE coalesce(assertion.relation_types, []) + row.relation_type END,
            assertion.claim_texts = CASE
            WHEN row.claim_text IN coalesce(assertion.claim_texts, []) THEN assertion.claim_texts
            ELSE coalesce(assertion.claim_texts, []) + row.claim_text END
        """,
        rows=payload.entity_assertions,
    )
    tx.run(
        """
        UNWIND $rows AS row
        MATCH (claim:`知识断言` {claim_id: row.claim_id})
        MATCH (evidence:`证据` {evidence_id: row.evidence_id})
        MERGE (claim)-[:有证据]->(evidence)
        """,
        rows=payload.claim_evidence,
    )
    tx.run(
        """
        UNWIND $rows AS row
        MATCH (chunk:`文本块` {chunk_id: row.chunk_id})
        MATCH (evidence:`证据` {evidence_id: row.evidence_id})
        MERGE (chunk)-[:包含证据]->(evidence)
        """,
        rows=payload.chunk_evidence,
    )
    tx.run(
        """
        UNWIND $rows AS row
        MATCH (claim:`知识断言` {claim_id: row.claim_id})
        MATCH (chunk:`文本块` {chunk_id: row.chunk_id})
        MERGE (claim)-[:来源于]->(chunk)
        """,
        rows=payload.claim_chunks,
    )


def _merge_nodes(tx: Any, label: str, key: str, rows: list[dict[str, Any]]) -> None:
    tx.run(
        f"""
        UNWIND $rows AS row
        MERGE (n:`{label}` {{{key}: row.{key}}})
        SET n += row
        """,
        rows=rows,
    )


def _model_or_none_to_json(value: Any) -> str | None:
    if value is None:
        return None
    return stable_json_dumps(value)


def _models_to_json(values: list[Any]) -> str:
    return json.dumps([_model_to_plain(value) for value in values], ensure_ascii=False, sort_keys=True)


def _model_to_plain(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _entity_type_zh(entity_type: str) -> str:
    return {
        "disease": "疾病",
        "syndrome": "综合征",
        "phenotype": "表型",
        "examination": "检查",
        "biomarker": "生物标志物",
    }.get(entity_type, "医学实体")


def _predicate_zh(predicate: str) -> str:
    return {
        "supports": "支持",
        "weakens": "削弱",
        "associated_with": "相关",
        "distinguishes": "区分",
        "limits": "限制",
    }.get(predicate, "相关")


def _chinese_entity_name(name: str) -> str:
    """Use a Chinese display name for common biomedical abbreviations."""
    replacements = {
        "AD 源性轻度认知障碍": "阿尔茨海默病源性轻度认知障碍",
        "AD源性轻度认知障碍": "阿尔茨海默病源性轻度认知障碍",
        "AD": "阿尔茨海默病",
        "MCI": "轻度认知障碍",
        "MRI": "磁共振成像",
        "PET": "正电子发射断层显像",
        "Aβ": "β淀粉样蛋白",
    }
    result = name.strip()
    for source, target in replacements.items():
        result = result.replace(source, target)
    return result


def _chinese_display_text(text: str) -> str:
    replacements = {
        "Alzheimer": "阿尔茨海默病",
        "alzheimer": "阿尔茨海默病",
        "AD": "阿尔茨海默病",
        "Ad": "阿尔茨海默病",
        "ad": "阿尔茨海默病",
        "MCI": "轻度认知障碍",
        "mci": "轻度认知障碍",
        "Aβ": "β淀粉样蛋白",
    }
    result = text
    for source, target in replacements.items():
        result = result.replace(source, target)
    return result


def _evidence_name(document: Document | None, page_no: Any) -> str:
    title = "原文证据" if document is None or not document.title else document.title
    return f"{title} 第{page_no}页" if page_no is not None else title


def _chunk_name(document: Document | None, pages: list[int]) -> str:
    title = "检索文本" if document is None or not document.title else document.title
    if pages:
        return f"{title} 第{','.join(str(page) for page in pages)}页文本"
    return f"{title} 文本"
