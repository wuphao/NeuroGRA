"""Build parent and child chunks from source spans."""

from __future__ import annotations

import re

from pydantic import Field

from neurogra.knowledge.config import ChunkingConfig
from neurogra.knowledge.schemas.common import SchemaModel
from neurogra.knowledge.schemas.source import SourceSpan
from neurogra.knowledge.schemas.text import Chunk
from neurogra.knowledge.utils import stable_hash

TOKENIZER_VERSION = "regex_zh_en_v1"


class ChunkBuildResult(SchemaModel):
    chunk_build_id: str
    document_id: str
    chunks: list[Chunk]
    issues: list[str] = Field(default_factory=list)


def build_chunks(spans: list[SourceSpan], config: ChunkingConfig) -> ChunkBuildResult:
    """Create conservative parent/child chunks while preserving source span IDs."""

    if not spans:
        raise ValueError("Cannot build chunks from an empty span list")
    ordered = list(spans)
    document_ids = {span.document_id for span in ordered}
    if len(document_ids) != 1:
        raise ValueError("A chunk build must contain spans from exactly one document")
    document_id = ordered[0].document_id
    chunk_build_id = stable_hash(
        {
            "document_id": document_id,
            "span_ids": [span.span_id for span in ordered],
            "config": config.model_dump(mode="json"),
            "tokenizer": TOKENIZER_VERSION,
        },
        "chunkbuild",
    )

    chunks: list[Chunk] = []
    issues: list[str] = []
    for span in ordered:
        parent_text = span.original_text
        retrieval_text = _retrieval_text(span)
        parent_chunk_id = stable_hash(
            {
                "chunk_build_id": chunk_build_id,
                "span_ids": [span.span_id],
                "role": "parent",
            },
            "chunk",
        )
        parent = Chunk(
            chunk_id=parent_chunk_id,
            chunk_build_id=chunk_build_id,
            document_id=span.document_id,
            span_ids=[span.span_id],
            role="parent",
            original_text=parent_text,
            retrieval_text=retrieval_text,
            section_path=span.section_path,
            token_count=count_tokens(retrieval_text),
            tokenizer_version=TOKENIZER_VERSION,
        )
        chunks.append(parent)

        child_texts = _split_child_text(span.normalized_text, config.max_tokens)
        if len(child_texts) > 1:
            issues.append(f"{span.span_id}:split_into_{len(child_texts)}_children")
        for ordinal, child_text in enumerate(child_texts):
            child_chunk_id = stable_hash(
                {
                    "chunk_build_id": chunk_build_id,
                    "span_ids": [span.span_id],
                    "role": "child",
                    "ordinal": ordinal,
                    "text": child_text,
                },
                "chunk",
            )
            chunks.append(
                Chunk(
                    chunk_id=child_chunk_id,
                    chunk_build_id=chunk_build_id,
                    document_id=span.document_id,
                    span_ids=[span.span_id],
                    parent_chunk_id=parent_chunk_id,
                    role="child",
                    original_text=span.original_text,
                    retrieval_text=child_text,
                    section_path=span.section_path,
                    token_count=count_tokens(child_text),
                    tokenizer_version=TOKENIZER_VERSION,
                )
            )

    return ChunkBuildResult(chunk_build_id=chunk_build_id, document_id=document_id, chunks=chunks, issues=issues)


def count_tokens(text: str) -> int:
    return len(tokenize(text))


def tokenize(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]", text.lower())


def _retrieval_text(span: SourceSpan) -> str:
    prefix = " / ".join(part for part in span.section_path if part)
    if prefix:
        return f"{prefix}\n{span.normalized_text}"
    return span.normalized_text


def _split_child_text(text: str, max_tokens: int) -> list[str]:
    if count_tokens(text) <= max_tokens:
        return [text]
    sentences = re.split(r"(?<=[。！？!?；;])\s*", text)
    children: list[str] = []
    current: list[str] = []
    current_tokens = 0
    for sentence in sentences:
        if not sentence:
            continue
        sentence_tokens = count_tokens(sentence)
        if current and current_tokens + sentence_tokens > max_tokens:
            children.append("".join(current).strip())
            current = []
            current_tokens = 0
        current.append(sentence)
        current_tokens += sentence_tokens
    if current:
        children.append("".join(current).strip())
    return children or [text]
