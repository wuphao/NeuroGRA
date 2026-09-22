"""Entity linking and clause normalization."""

from __future__ import annotations

from pydantic import Field

from neurogra.knowledge.schemas.common import SchemaModel, StrictBaseModel
from neurogra.knowledge.schemas.graph import (
    CandidateClause,
    ClauseRevision,
    Entity,
    EntityMention,
    TextCondition,
)
from neurogra.knowledge.schemas.source import EvidenceRef
from neurogra.knowledge.utils import stable_hash


class TerminologyEntry(StrictBaseModel):
    canonical_name: str
    entity_type: str
    aliases: list[str] = Field(default_factory=list)
    external_ids: dict[str, str] = Field(default_factory=dict)


class Terminology(StrictBaseModel):
    terminology_version: str = "local_v1"
    entries: list[TerminologyEntry] = Field(default_factory=list)

    @classmethod
    def default(cls) -> "Terminology":
        return cls(
            entries=[
                TerminologyEntry(canonical_name="阿尔茨海默病", entity_type="disease", aliases=["AD", "Alzheimer", "Alzheimer's disease"]),
                TerminologyEntry(canonical_name="AD 源性轻度认知障碍", entity_type="syndrome", aliases=["AD 源性MCI", "AD源性MCI", "MCI due to AD"]),
                TerminologyEntry(canonical_name="轻度认知障碍", entity_type="syndrome", aliases=["MCI"]),
                TerminologyEntry(canonical_name="磁共振成像", entity_type="examination", aliases=["MRI"]),
                TerminologyEntry(canonical_name="β-淀粉样蛋白", entity_type="biomarker", aliases=["Aβ", "β⁃淀粉样蛋白"]),
                TerminologyEntry(canonical_name="认知障碍", entity_type="phenotype", aliases=[]),
                TerminologyEntry(canonical_name="记忆功能损害", entity_type="phenotype", aliases=[]),
            ]
        )


class NormalizationResult(SchemaModel):
    extraction_run_id: str
    entities: list[Entity]
    mentions: list[EntityMention]
    clause_revisions: list[ClauseRevision]
    issues: list[str] = Field(default_factory=list)


def normalize_candidates(
    candidates: list[CandidateClause],
    terminology: Terminology | None = None,
) -> NormalizationResult:
    terminology = terminology or Terminology.default()
    entity_by_key = _seed_entities(terminology)
    entities: dict[str, Entity] = {entity.entity_id: entity for entity in entity_by_key.values()}
    mentions: list[EntityMention] = []
    revisions: list[ClauseRevision] = []
    issues: list[str] = []
    extraction_run_id = candidates[0].extraction_run_id if candidates else "empty"

    for candidate in candidates:
        subject_entity, subject_mention = _link_text(candidate.subject_text, candidate.subject_type, candidate.evidence_refs[0], terminology, entity_by_key)
        object_entity, object_mention = _link_text(candidate.object_text, candidate.object_type, candidate.evidence_refs[0], terminology, entity_by_key)
        entities[subject_entity.entity_id] = subject_entity
        entities[object_entity.entity_id] = object_entity
        mentions.extend([subject_mention, object_mention])
        if subject_mention.link_status != "resolved" or object_mention.link_status != "resolved":
            issues.append(f"{candidate.candidate_id}:pending_entity_link")

        clause_id = stable_hash(
            {
                "candidate_id": candidate.candidate_id,
                "subject": subject_entity.entity_id,
                "predicate": candidate.predicate,
                "object": object_entity.entity_id,
            },
            "clause",
        )
        revision_id = f"{clause_id}:1"
        condition = candidate.condition
        condition_executable = True
        if isinstance(condition, TextCondition):
            condition_executable = False
        revisions.append(
            ClauseRevision(
                clause_id=clause_id,
                revision_id=revision_id,
                candidate_id=candidate.candidate_id,
                subject_entity_id=subject_entity.entity_id,
                object_entity_id=object_entity.entity_id,
                predicate=candidate.predicate,
                direction=candidate.direction,
                diagnostic_level=candidate.diagnostic_level,
                modality=candidate.modality,
                condition=condition,
                exceptions=candidate.exceptions,
                condition_executable=condition_executable,
                evidence_refs=candidate.evidence_refs,
                field_evidence=candidate.field_evidence,
                assertion_text=candidate.assertion_text,
                review_status="pending",
                validation_issues=candidate.issues,
                claim_group_id=None,
                extraction_run_id=candidate.extraction_run_id,
            )
        )

    return NormalizationResult(
        extraction_run_id=extraction_run_id,
        entities=sorted(entities.values(), key=lambda item: item.entity_id),
        mentions=mentions,
        clause_revisions=revisions,
        issues=issues,
    )


def _seed_entities(terminology: Terminology) -> dict[str, Entity]:
    seeded: dict[str, Entity] = {}
    for entry in terminology.entries:
        entity_id = stable_hash(
            {
                "canonical_name": entry.canonical_name,
                "entity_type": entry.entity_type,
                "terminology_version": terminology.terminology_version,
            },
            "entity",
        )
        entity = Entity(
            entity_id=entity_id,
            canonical_name=entry.canonical_name,
            entity_type=entry.entity_type,  # type: ignore[arg-type]
            aliases=entry.aliases,
            external_ids=entry.external_ids,
            terminology_version=terminology.terminology_version,
            status="approved",
        )
        for key in [entry.canonical_name, *entry.aliases]:
            seeded[_norm(key)] = entity
    return seeded


def _link_text(
    text: str,
    predicted_type: str,
    evidence: EvidenceRef,
    terminology: Terminology,
    entity_by_key: dict[str, Entity],
) -> tuple[Entity, EntityMention]:
    key = _norm(text)
    entity = entity_by_key.get(key)
    link_status = "resolved"
    link_method = "exact_alias"
    if entity is None:
        entity_id = stable_hash(
            {
                "canonical_name": text,
                "entity_type": predicted_type,
                "terminology_version": terminology.terminology_version,
                "status": "local_pending",
            },
            "entity",
        )
        entity = Entity(
            entity_id=entity_id,
            canonical_name=text,
            entity_type=predicted_type,  # type: ignore[arg-type]
            aliases=[],
            external_ids={},
            terminology_version=terminology.terminology_version,
            status="pending",
        )
        entity_by_key[key] = entity
        link_status = "local_pending"
        link_method = "local_new"

    mention = EntityMention(
        mention_id=stable_hash(
            {
                "text": text,
                "predicted_type": predicted_type,
                "span_id": evidence.span_id,
                "start": evidence.start,
                "end": evidence.end,
            },
            "mention",
        ),
        text=text,
        predicted_type=predicted_type,  # type: ignore[arg-type]
        evidence=evidence,
        selected_entity_id=entity.entity_id if link_status == "resolved" else None,
        candidate_entity_ids=[entity.entity_id],
        link_method=link_method,
        link_status=link_status,  # type: ignore[arg-type]
    )
    return entity, mention


def _norm(text: str) -> str:
    return text.lower().replace(" ", "").replace("'", "").replace("’", "")
