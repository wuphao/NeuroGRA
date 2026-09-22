"""Small local BM25 index for development retrieval."""

from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from typing import Callable
from typing import Literal

from pydantic import Field

from neurogra.knowledge.config import RetrievalConfig
from neurogra.knowledge.processing.segmenter import TOKENIZER_VERSION, tokenize
from neurogra.knowledge.schemas.common import SchemaModel, SourceRole, StrictBaseModel
from neurogra.knowledge.schemas.text import Citation, Chunk, SearchHit
from neurogra.knowledge.utils import stable_hash


class TextIndexManifest(SchemaModel):
    build_id: str
    chunk_build_id: str
    mode: Literal["development"] = "development"
    tokenizer_version: str
    chunk_count: int
    index_path: str
    retrieval_config: dict[str, object]


class SearchRequest(StrictBaseModel):
    query: str
    top_k: int = Field(default=5, ge=1, le=100)


def build_text_index(
    chunks: list[Chunk],
    config: RetrievalConfig,
    output_path: Path,
) -> TextIndexManifest:
    child_chunks = [chunk for chunk in chunks if chunk.role == "child"]
    if not child_chunks:
        raise ValueError("Cannot build text index without child chunks")
    chunk_build_ids = {chunk.chunk_build_id for chunk in child_chunks}
    if len(chunk_build_ids) != 1:
        raise ValueError("All indexed chunks must come from one chunk build")

    documents = []
    document_frequency: Counter[str] = Counter()
    for chunk in child_chunks:
        tokens = tokenize(chunk.retrieval_text)
        term_counts = Counter(tokens)
        document_frequency.update(term_counts.keys())
        documents.append(
            {
                "chunk_id": chunk.chunk_id,
                "tokens": tokens,
                "term_counts": dict(term_counts),
                "length": len(tokens),
            }
        )

    build_id = stable_hash(
        {
            "chunk_ids": [chunk.chunk_id for chunk in child_chunks],
            "retrieval_config": config.model_dump(mode="json"),
            "tokenizer": TOKENIZER_VERSION,
        },
        "textindex",
    )
    payload = {
        "build_id": build_id,
        "chunk_build_id": child_chunks[0].chunk_build_id,
        "tokenizer_version": TOKENIZER_VERSION,
        "avg_doc_len": max(1.0, sum(item["length"] for item in documents) / len(documents)),
        "document_frequency": dict(document_frequency),
        "documents": documents,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return TextIndexManifest(
        build_id=build_id,
        chunk_build_id=child_chunks[0].chunk_build_id,
        tokenizer_version=TOKENIZER_VERSION,
        chunk_count=len(child_chunks),
        index_path=str(output_path),
        retrieval_config=config.model_dump(mode="json"),
    )


def search_text(
    request: SearchRequest,
    chunks: list[Chunk],
    index_path: Path,
    source_role: SourceRole = SourceRole.CLINICAL_GUIDELINE,
    citation_resolver: Callable[[Chunk], list[Citation]] | None = None,
) -> list[SearchHit]:
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    query_tokens = tokenize(request.query)
    if not query_tokens:
        return []
    chunk_map = {chunk.chunk_id: chunk for chunk in chunks}
    scores: list[tuple[str, float]] = []
    total_docs = len(payload["documents"])
    avg_doc_len = float(payload["avg_doc_len"]) or 1.0
    doc_freq = payload["document_frequency"]
    for doc in payload["documents"]:
        score = _bm25_score(query_tokens, doc["term_counts"], doc["length"], doc_freq, total_docs, avg_doc_len)
        if score > 0:
            scores.append((doc["chunk_id"], score))
    scores.sort(key=lambda item: item[1], reverse=True)
    hits: list[SearchHit] = []
    for chunk_id, score in scores[: request.top_k]:
        chunk = chunk_map[chunk_id]
        citations = citation_resolver(chunk) if citation_resolver is not None else []
        if not citations:
            citations = [
                Citation(
                    document_id=chunk.document_id,
                    source_role=source_role,
                    parse_id="",
                    span_id=chunk.span_ids[0],
                    page_no=1,
                    quote=chunk.original_text[:300],
                    extraction_method="native_text",
                )
            ]
        hits.append(
            SearchHit(
                chunk_id=chunk.chunk_id,
                score=score,
                matched_by=["bm25"],
                original_text=chunk.original_text,
                retrieval_text=chunk.retrieval_text,
                context_text=None,
                citations=citations,
                clause_ids=chunk.linked_clause_ids,
                source_role=source_role,
            )
        )
    return hits


def _bm25_score(
    query_tokens: list[str],
    term_counts: dict[str, int],
    doc_len: int,
    doc_freq: dict[str, int],
    total_docs: int,
    avg_doc_len: float,
) -> float:
    k1 = 1.5
    b = 0.75
    score = 0.0
    for token in query_tokens:
        tf = term_counts.get(token, 0)
        if tf == 0:
            continue
        df = doc_freq.get(token, 0)
        idf = math.log(1 + (total_docs - df + 0.5) / (df + 0.5))
        denom = tf + k1 * (1 - b + b * doc_len / avg_doc_len)
        score += idf * (tf * (k1 + 1) / denom)
    return score
