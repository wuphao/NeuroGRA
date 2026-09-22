"""Immutable local release builder."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import Field

from neurogra.knowledge.config import BuildConfig
from neurogra.knowledge.graph_store.neo4j import Neo4jReleaseWriter, build_neo4j_payload
from neurogra.knowledge.indexing.lexical import build_text_index
from neurogra.knowledge.schemas.graph import ClauseRevision
from neurogra.knowledge.schemas.common import SchemaModel
from neurogra.knowledge.schemas.lifecycle import ReleaseManifest
from neurogra.knowledge.schemas.text import Chunk
from neurogra.knowledge.storage.artifacts import write_jsonl
from neurogra.knowledge.storage.sqlite import Repository
from neurogra.knowledge.utils import sha256_file, stable_hash


class ReleaseSelection(SchemaModel):
    chunk_build_id: str
    terminology_version: str = "local_v1"


class ReleaseBuildResult(SchemaModel):
    release_id: str
    manifest: ReleaseManifest
    activated: bool = False
    issues: list[str] = Field(default_factory=list)


class ActivationResult(SchemaModel):
    release_id: str
    active: bool


def build_release(selection: ReleaseSelection, config: BuildConfig, repository: Repository) -> ReleaseBuildResult:
    approved_chunks = [
        chunk
        for chunk in repository.get_chunks_for_build(selection.chunk_build_id)
        if chunk.role == "child" and chunk.text_review_status == "approved"
    ]
    approved_revisions = [
        revision
        for revision in repository.get_clause_revisions_for_chunk_build(selection.chunk_build_id)
        if revision.review_status == "approved"
    ]
    issues: list[str] = []
    if not approved_chunks:
        issues.append("no_approved_child_chunks")
    release_id = stable_hash(
        {
            "chunk_ids": [chunk.chunk_id for chunk in approved_chunks],
            "revision_ids": [revision.revision_id for revision in approved_revisions],
            "config_hash": config.fingerprint(),
        },
        "release",
    )
    release_root = config.resolve_path(config.paths.data_root) / "releases" / release_id
    text_path = release_root / "text" / "chunks.jsonl"
    graph_path = release_root / "graph" / "clause_revisions.jsonl"
    index_path = release_root / "text" / "bm25_index.json"
    manifest_path = release_root / "manifest.json"
    checks_path = release_root / "checks.json"
    release_root.mkdir(parents=True, exist_ok=True)
    write_jsonl(text_path, approved_chunks)
    write_jsonl(graph_path, approved_revisions)
    if approved_chunks:
        build_text_index(approved_chunks, config.retrieval, index_path)
    checks = {
        "has_approved_chunks": bool(approved_chunks),
        "all_revision_evidence_spans_exist": all(
            repository.get_span(evidence.span_id) is not None
            for revision in approved_revisions
            for evidence in revision.evidence_refs
        ),
        "all_revision_evidence_in_approved_chunks": _all_revision_evidence_in_chunks(
            approved_revisions,
            approved_chunks,
        ),
        "all_revision_entities_exist": all(
            repository.entity_exists(revision.subject_entity_id) and repository.entity_exists(revision.object_entity_id)
            for revision in approved_revisions
        ),
    }
    issues.extend(f"check_failed:{name}" for name, passed in checks.items() if not passed)
    checks_path.write_text(json.dumps(checks, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    artifact_checksums = {
        "text_chunks": sha256_file(text_path),
        "graph_clauses": sha256_file(graph_path),
        "checks": sha256_file(checks_path),
    }
    if index_path.exists():
        artifact_checksums["bm25"] = sha256_file(index_path)
    document_ids = sorted({chunk.document_id for chunk in approved_chunks})
    manifest = ReleaseManifest(
        release_id=release_id,
        document_ids=document_ids,
        parse_ids=repository.parse_ids_for_documents(document_ids),
        clause_revision_ids=[revision.revision_id for revision in approved_revisions],
        chunk_ids=[chunk.chunk_id for chunk in approved_chunks],
        terminology_version=selection.terminology_version,
        parser_versions=repository.parser_versions_for_documents(document_ids),
        extraction_model="rule_based",
        embedding_model=None,
        tokenizer_version="regex_zh_en_v1",
        config_hash=config.fingerprint(),
        artifact_checksums=artifact_checksums,
        bm25_path=str(index_path) if index_path.exists() else None,
        qdrant_collection=None,
        neo4j_release_id=None,
        counts={"chunks": len(approved_chunks), "clause_revisions": len(approved_revisions)},
        checks=checks,
    )
    if config.graph_store.provider == "neo4j" and all(checks.values()) and not issues:
        manifest = _publish_release_to_neo4j(
            manifest,
            approved_chunks,
            approved_revisions,
            config,
            repository,
        )
    elif config.graph_store.provider == "neo4j":
        issues.append("neo4j_publish_skipped_due_to_failed_release_checks")
    manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    repository.save_release(manifest, "validated" if all(checks.values()) and not issues else "failed")
    return ReleaseBuildResult(release_id=release_id, manifest=manifest, issues=issues)


def activate_release(repository: Repository, release_id: str) -> ActivationResult:
    repository.activate_release(release_id)
    return ActivationResult(release_id=release_id, active=True)


def _all_revision_evidence_in_chunks(
    approved_revisions: list[ClauseRevision],
    approved_chunks: list[Chunk],
) -> bool:
    approved_span_ids = {span_id for chunk in approved_chunks for span_id in chunk.span_ids}
    return all(
        evidence.span_id in approved_span_ids
        for revision in approved_revisions
        for evidence in revision.evidence_refs
    )


def _publish_release_to_neo4j(
    manifest: ReleaseManifest,
    approved_chunks: list[Chunk],
    approved_revisions: list[ClauseRevision],
    config: BuildConfig,
    repository: Repository,
) -> ReleaseManifest:
    entity_ids = sorted(
        {
            entity_id
            for revision in approved_revisions
            for entity_id in (revision.subject_entity_id, revision.object_entity_id)
        }
    )
    span_ids = sorted(
        {
            span_id
            for chunk in approved_chunks
            for span_id in chunk.span_ids
        }
        | {
            evidence.span_id
            for revision in approved_revisions
            for evidence in revision.evidence_refs
        }
    )
    spans = [span for span_id in span_ids if (span := repository.get_span(span_id)) is not None]
    documents = [repository.get_document(document_id) for document_id in manifest.document_ids]
    payload = build_neo4j_payload(
        manifest=manifest,
        chunks=approved_chunks,
        revisions=approved_revisions,
        entities=repository.get_entities(entity_ids),
        spans=spans,
        documents=documents,
        span_sources=repository.span_source_contexts(span_ids),
        candidate_chunk_ids=repository.chunk_ids_for_candidates(
            [revision.candidate_id for revision in approved_revisions]
        ),
    )
    with Neo4jReleaseWriter(config.graph_store) as writer:
        neo4j_release_id = writer.publish(payload)
    return manifest.model_copy(update={"neo4j_release_id": neo4j_release_id})
