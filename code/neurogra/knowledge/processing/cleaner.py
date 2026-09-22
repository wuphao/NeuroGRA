"""Clean parsed blocks into source spans while preserving raw provenance."""

from __future__ import annotations

import re
from collections import Counter

from pydantic import Field

from neurogra.knowledge.parsing.base import ParsedDocument
from neurogra.knowledge.schemas.common import SchemaModel
from neurogra.knowledge.schemas.source import (
    NormalizationMapItem,
    SourceLocator,
    SourceSpan,
)
from neurogra.knowledge.utils import stable_hash


class SpanBuildResult(SchemaModel):
    parse_id: str
    document_id: str
    spans: list[SourceSpan]
    removed_repeated_lines: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)


def build_spans(parsed_document: ParsedDocument) -> SpanBuildResult:
    """Build one SourceSpan per useful parsed block for the first implementation."""

    repeated_lines = _find_repeated_short_lines(parsed_document)
    spans: list[SourceSpan] = []
    issues: list[str] = []
    current_section: list[str] = []

    for block in sorted(parsed_document.blocks, key=lambda item: (item.page_no, item.reading_order)):
        if not block.raw_text.strip():
            issues.append(f"{block.block_id}:empty_block_skipped")
            continue
        original_text = block.raw_text
        cleaned, normalization_map = _normalize_text(original_text, repeated_lines)
        if not cleaned.strip():
            issues.append(f"{block.block_id}:cleaned_to_empty")
            continue
        if block.block_type == "heading":
            current_section = [cleaned.strip()]
        elif _looks_like_heading(cleaned):
            current_section = [cleaned.strip()]

        span_id = stable_hash(
            {
                "parse_id": block.parse_id,
                "block_id": block.block_id,
                "start": 0,
                "end": len(original_text),
                "split_version": "block_v1",
            },
            "span",
        )
        span_issues = list(block.issues)
        if block.extraction_method.value == "ocr":
            span_issues.append("ocr_transcription")
        spans.append(
            SourceSpan(
                span_id=span_id,
                parse_id=block.parse_id,
                document_id=block.document_id,
                section_path=current_section.copy(),
                locators=[SourceLocator(block_id=block.block_id, start=0, end=len(original_text))],
                original_text=original_text,
                normalized_text=cleaned,
                normalization_map=normalization_map,
                context_span_ids=[],
                issues=span_issues,
            )
        )

    for index, span in enumerate(spans):
        context_ids: list[str] = []
        if index > 0:
            context_ids.append(spans[index - 1].span_id)
        if index + 1 < len(spans):
            context_ids.append(spans[index + 1].span_id)
        spans[index] = span.model_copy(update={"context_span_ids": context_ids})

    return SpanBuildResult(
        parse_id=parsed_document.parse_id,
        document_id=parsed_document.document_id,
        spans=spans,
        removed_repeated_lines=sorted(repeated_lines),
        issues=issues,
    )


def _find_repeated_short_lines(parsed_document: ParsedDocument) -> set[str]:
    page_count = len({block.page_no for block in parsed_document.blocks})
    if page_count < 3:
        return set()
    counter: Counter[str] = Counter()
    for block in parsed_document.blocks:
        lines = {line.strip() for line in block.raw_text.splitlines() if line.strip()}
        for line in lines:
            if _can_be_repeated_header_or_footer(line):
                counter[line] += 1
    threshold = max(3, page_count // 2)
    return {line for line, count in counter.items() if count >= threshold}


def _normalize_text(
    original_text: str,
    removed_lines: set[str],
) -> tuple[str, list[NormalizationMapItem]]:
    output: list[str] = []
    mappings: list[NormalizationMapItem] = []
    normalized_index = 0
    original_index = 0
    for raw_line in original_text.splitlines(keepends=True):
        line_without_newline = raw_line.strip()
        line_start = original_index
        line_end = original_index + len(raw_line)
        original_index = line_end
        if line_without_newline in removed_lines:
            continue
        normalized_line = re.sub(r"[ \t]+", " ", raw_line).strip()
        if not normalized_line:
            continue
        if output:
            output.append("\n")
            normalized_index += 1
        start = normalized_index
        output.append(normalized_line)
        normalized_index += len(normalized_line)
        mappings.append(
            NormalizationMapItem(
                normalized_start=start,
                normalized_end=normalized_index,
                original_start=line_start,
                original_end=line_end,
                locator_ordinal=0,
            )
        )
    return "".join(output), mappings


def _looks_like_heading(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) > 80:
        return False
    return bool(re.match(r"^(\d+(\.\d+)*|[一二三四五六七八九十]+[、.])\s*[^。；;]{1,70}$", stripped))


def _can_be_repeated_header_or_footer(line: str) -> bool:
    if not (4 <= len(line) <= 80):
        return False
    meaningful = re.findall(r"[A-Za-z0-9\u4e00-\u9fff]", line)
    return len(meaningful) >= 3
