"""Candidate clause extraction boundary and local rule-based implementation."""

from __future__ import annotations

import re

from pydantic import Field

from neurogra.knowledge.schemas.common import SchemaModel, StrictBaseModel
from neurogra.knowledge.schemas.graph import CandidateClause
from neurogra.knowledge.schemas.source import EvidenceRef
from neurogra.knowledge.schemas.text import Chunk
from neurogra.knowledge.utils import stable_hash


class ExtractionInput(StrictBaseModel):
    chunk_id: str
    span_ids: list[str]
    text: str
    context_text: str | None = None
    prompt_version: str = "rules_v1"


class ExtractionResult(SchemaModel):
    extraction_run_id: str
    chunk_id: str
    candidates: list[CandidateClause]
    raw_response: dict[str, object] = Field(default_factory=dict)
    issues: list[str] = Field(default_factory=list)


class RuleBasedExtractor:
    """Conservative local extractor for smoke tests before model integration."""

    prompt_version = "rules_v1"

    def extract(self, context: ExtractionInput) -> ExtractionResult:
        extraction_run_id = stable_hash(
            {
                "chunk_id": context.chunk_id,
                "span_ids": context.span_ids,
                "text": context.text,
                "prompt_version": context.prompt_version,
            },
            "extractrun",
        )
        mentions = _find_known_mentions(context.text)
        candidates: list[CandidateClause] = []
        if len(mentions) >= 2:
            subject = mentions[0]
            for mention in mentions[1:4]:
                if mention["text"] == subject["text"]:
                    continue
                evidence = _evidence_for_pair(context.span_ids[0], context.text, subject["text"], mention["text"])
                candidate_id = stable_hash(
                    {
                        "extraction_run_id": extraction_run_id,
                        "chunk_id": context.chunk_id,
                        "subject": subject["text"],
                        "object": mention["text"],
                        "evidence": evidence.model_dump(mode="json"),
                    },
                    "candidate",
                )
                candidates.append(
                    CandidateClause(
                        candidate_id=candidate_id,
                        extraction_run_id=extraction_run_id,
                        chunk_id=context.chunk_id,
                        subject_text=subject["text"],
                        subject_type=subject["type"],
                        object_text=mention["text"],
                        object_type=mention["type"],
                        predicate=_predicate_for(subject["type"], mention["type"]),
                        direction="neutral",
                        modality="unspecified",
                        condition=None,
                        evidence_refs=[evidence],
                        field_evidence={
                            "subject_text": [evidence],
                            "object_text": [evidence],
                            "predicate": [evidence],
                        },
                        assertion_text=f"{subject['text']} 与 {mention['text']} 在同一证据片段中相关。",
                        raw_payload={"method": "known_term_cooccurrence"},
                    )
                )
        issues = [] if candidates else ["no_rule_candidate"]
        return ExtractionResult(
            extraction_run_id=extraction_run_id,
            chunk_id=context.chunk_id,
            candidates=candidates,
            raw_response={"mention_count": len(mentions), "method": "rule_based"},
            issues=issues,
        )


def context_from_chunk(chunk: Chunk, context_text: str | None = None) -> ExtractionInput:
    return ExtractionInput(
        chunk_id=chunk.chunk_id,
        span_ids=chunk.span_ids,
        text=chunk.retrieval_text,
        context_text=context_text,
    )


def _find_known_mentions(text: str) -> list[dict[str, str]]:
    lexicon = [
        ("AD 源性MCI", "syndrome"),
        ("AD源性MCI", "syndrome"),
        ("轻度认知障碍", "syndrome"),
        ("MCI", "syndrome"),
        ("阿尔茨海默病", "disease"),
        ("Alzheimer", "disease"),
        ("AD", "disease"),
        ("磁共振成像", "examination"),
        ("MRI", "examination"),
        ("Aβ", "biomarker"),
        ("β-淀粉样蛋白", "biomarker"),
        ("β⁃淀粉样蛋白", "biomarker"),
        ("记忆功能损害", "phenotype"),
        ("认知障碍", "phenotype"),
    ]
    matches: list[dict[str, str | int]] = []
    for term, entity_type in lexicon:
        for match in re.finditer(re.escape(term), text, flags=re.IGNORECASE):
            matches.append(
                {
                    "text": match.group(0),
                    "type": entity_type,
                    "start": match.start(),
                    "end": match.end(),
                }
            )
    matches.sort(key=lambda item: (int(item["start"]), -(int(item["end"]) - int(item["start"]))))
    found: list[dict[str, str]] = []
    occupied: list[tuple[int, int]] = []
    for item in matches:
        span = (int(item["start"]), int(item["end"]))
        if any(max(span[0], used[0]) < min(span[1], used[1]) for used in occupied):
            continue
        occupied.append(span)
        found.append({"text": str(item["text"]), "type": str(item["type"])})
    found.sort(key=lambda item: text.lower().find(item["text"].lower()))
    unique: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in found:
        key = item["text"].lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append({"text": item["text"], "type": item["type"]})
    return unique


def _predicate_for(subject_type: str, object_type: str) -> str:
    if subject_type == "disease" and object_type in {"phenotype", "biomarker", "examination"}:
        return "associated_with"
    if subject_type == "syndrome" and object_type in {"disease", "phenotype", "examination", "biomarker"}:
        return "associated_with"
    return "associated_with"


def _evidence_for_pair(span_id: str, text: str, first: str, second: str) -> EvidenceRef:
    positions = [pos for pos in (text.lower().find(first.lower()), text.lower().find(second.lower())) if pos >= 0]
    if not positions:
        quote = text[: min(len(text), 300)]
        return EvidenceRef(span_id=span_id, start=0, end=len(quote), quote=quote)
    start = max(0, min(positions) - 80)
    end = min(len(text), max(pos + 80 for pos in positions))
    quote = text[start:end]
    return EvidenceRef(span_id=span_id, start=start, end=end, quote=quote)
