"""Unified query over the active local release."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field

from neurogra.knowledge.indexing.lexical import SearchRequest, search_text
from neurogra.knowledge.schemas.common import SchemaModel
from neurogra.knowledge.schemas.lifecycle import ReleaseManifest
from neurogra.knowledge.schemas.text import Chunk, SearchHit
from neurogra.knowledge.storage.sqlite import Repository


class SearchResponse(SchemaModel):
    query: str
    release_id: str
    hits: list[SearchHit]
    degraded: bool = False
    warnings: list[str] = Field(default_factory=list)
    timing_ms: int = 0


def search(repository: Repository, query: str, top_k: int = 5, release_id: str | None = None) -> SearchResponse:
    manifest = repository.get_release_manifest(release_id or repository.active_release_id())
    chunks = _read_release_chunks(manifest)
    if manifest.bm25_path is None:
        raise ValueError(f"Release has no BM25 index: {manifest.release_id}")
    hits = search_text(
        SearchRequest(query=query, top_k=top_k),
        chunks,
        Path(manifest.bm25_path),
        citation_resolver=repository.citations_for_chunk,
    )
    return SearchResponse(query=query, release_id=manifest.release_id, hits=hits)


def _read_release_chunks(manifest: ReleaseManifest) -> list[Chunk]:
    if manifest.bm25_path is None:
        return []
    chunks_path = Path(manifest.bm25_path).parent / "chunks.jsonl"
    chunks: list[Chunk] = []
    for line in chunks_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            chunks.append(Chunk.model_validate_json(line))
    return chunks
