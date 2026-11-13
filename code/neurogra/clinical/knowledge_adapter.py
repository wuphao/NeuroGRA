"""Clinical boundary to the independent KG SDK; no legacy storage access."""
from datetime import datetime, timezone

from neurogra_kg.api import KnowledgeGraphService
from neurogra_kg.catalogs import FIELDS
from neurogra_kg.config import load_config
from neurogra_kg.contracts.domain import PatientFact, SourceRef
from neurogra_kg.contracts.domain import RetrievalRequest as KGRequest
from neurogra_kg.terminology import canonical_field, canonical_method, canonical_value

from .generation import patient_endpoint_allowed
from .schemas import EvidenceItem
from .utils import identity


def settings(config):
    result = config.kg or load_config(config.resolve(config.knowledge_config))
    return result.model_copy(update={"root": config.project_root})


def service(config):
    return KnowledgeGraphService(settings(config), read_only=True)


def patient_facts(snapshot, requested_refs):
    """Map only explicitly named fields with literal source support; retain conflicts."""
    facts, audit = map_patient_facts(snapshot, requested_refs)
    return facts, [row['observation_id'] for row in audit if row['status'] != 'mapped']


def mapped_field(observation):
    """Disambiguate totals by authoritative form, never by a numeric threshold."""
    name = observation.name
    form = str(observation.context.get('名称') or observation.context.get('量表名称') or '')
    table = {
        ('MMSE量表', 'MMSE总分'): 'mmse', ('MOCA量表', 'MoCA 汇总得分'): 'moca',
        ('CDR量表', '总体评分'): 'cdr', ('CDR量表', 'CDRSB总分'): 'cdr_sb',
        ('FAQ量表', '总分'): 'faq',
        ('ADAS-Cog量表', 'ADAS-Cog 11项总分'): 'adas_cog_11',
        ('ADAS-Cog量表', 'ADAS-Cog 13项总分'): 'adas_cog_13',
    }
    # Version must be explicit in the field name; different totals stay separate.
    if 'ADAS' in form.upper() and name.replace(' ', '') in {'ADAS-Cog11项总分', 'ADAS-Cog13项总分'}:
        return 'adas_cog_11' if '11' in name else 'adas_cog_13'
    return table.get((form, name), canonical_field(name))


def map_patient_facts(snapshot, requested_refs=()):
    """Return field-level receipts without changing the observations or source values."""
    if snapshot is None:
        return [], []
    records = {r.record_id: r for r in snapshot.records}
    facts, audit = [], []
    for observation in snapshot.observations:
        row = {'observation_id': observation.observation_id, 'name': observation.name,
               'form': str(observation.context.get('名称') or ''), 'status': 'omitted'}
        audit.append(row)
        if requested_refs and not ({observation.observation_id, *observation.source_refs} & set(requested_refs)):
            row['reason'] = 'outside_requested_scope'
            continue
        field = mapped_field(observation)
        row['canonical_field'] = field
        if field not in FIELDS or observation.status not in {"observed", "present", "absent", "conflicting"}:
            row['reason'] = 'unsupported_field' if field not in FIELDS else 'value_not_observed'
            continue
        record = next((records[r] for r in observation.source_refs if r in records
                       and observation.quote and observation.quote in records[r].text), None)
        if record is None:
            row['reason'] = 'literal_source_not_bound'
            continue
        value = observation.value
        expected = FIELDS[field]["type"]
        if expected == "boolean" and observation.status in {"present", "absent"}:
            value = observation.status == "present"
        valid = (expected == "boolean" and isinstance(value, bool)
                 or expected == "number" and isinstance(value, (int, float)) and not isinstance(value, bool)
                 or expected == "string" and isinstance(value, str))
        if not valid:
            row['reason'] = 'field_type_mismatch'
            continue
        observed_at = None
        if observation.time.value and observation.time.precision == "day":
            observed_at = datetime.fromisoformat(observation.time.value).replace(tzinfo=timezone.utc)
        start = record.text.index(observation.quote)
        unit = observation.unit or observation.context.get('单位')
        row.update({'status': 'mapped', 'source_unit': unit, 'reason': 'literal_source_verified'})
        if not unit and field in {'age', 'education_years', 'mmse', 'moca', 'cdr', 'cdr_sb', 'faq', 'adas_cog_11', 'adas_cog_13'}:
            unit = FIELDS[field]['unit']
            row['unit_origin'] = 'canonical_field_definition'
        else:
            row['unit_origin'] = 'source' if unit else 'unknown'
        row['canonical_unit'] = unit
        facts.append(PatientFact(fact_id=observation.observation_id, field=field, value=canonical_value(field, value),
            unit=unit, observed_at=observed_at,
            polarity="uncertain" if observation.status == "conflicting" else "affirmed",
            method=canonical_method(observation.context.get("method")), source_ref=SourceRef(block_id=record.record_id,
                start=start, end=start + len(observation.quote), quote=observation.quote)))
    return facts, audit


def retrieve_backend(config, release_id, request, backend, remaining, snapshot=None):
    import time
    if remaining <= 0:
        raise TimeoutError("retrieval_deadline")
    if config.kg_representation == 'plain_triples':
        from .triple_knowledge import retrieve_triples
        return retrieve_triples(config, release_id, request.question, backend, request.top_k)
    if backend == "vector" and not config.vector_enabled:
        return "not_configured", [], {}
    cfg = settings(config)
    if backend == 'vector' and not patient_endpoint_allowed(
            config.project_root, cfg.embedding.base_url, cfg.embedding.model):
        return 'blocked_patient_data_outbound', [], {}
    if backend == 'graph' and cfg.graph.provider != 'memory' and not patient_endpoint_allowed(
            config.project_root, cfg.graph.uri):
        return 'blocked_patient_data_outbound', [], {}
    facts, omitted = patient_facts(snapshot, request.patient_fact_refs)
    reference = request.reference_time
    # The SDK requires a time. Without a supplied patient analysis time no patient
    # facts enter comparison, and all returned material remains background.
    reference_known = reference is not None
    if reference is None:
        facts = []
        reference = datetime.min.replace(tzinfo=timezone.utc)
    deadline = time.monotonic() + remaining
    name = "lexical" if backend == "bm25" else backend
    with service(config) as kg:
        result = kg.retrieve(KGRequest(question=request.question, purpose=request.purpose,
            reference_time=reference, facts=facts, release_id=release_id, top_k=request.top_k,
            backends=[name], max_seconds=remaining, max_llm_calls=0, max_supplement_rounds=0))
        items = []
        for evidence in [*result.applicable, *result.unknown, *result.excluded]:
            statement = evidence.statement
            for ref in statement.source_refs:
                source = kg.get_source(ref.block_id, release_id)
                block, document = source["block"], source["document"]
                if block["text"][ref.start:ref.end] != ref.quote:
                    raise ValueError("knowledge_source_quote_mismatch")
                state = evidence.state if reference_known else "UNKNOWN"
                limitations = [c.reason for c in evidence.condition_results if c.state != "SATISFIED"]
                if statement.zh_annotation and statement.zh_annotation.status != "verified":
                    limitations.append("中文辅助表达未通过核验，保留原文依据")
                if not reference_known:
                    limitations.append("未提供患者分析时间，未执行患者适用性判断")
                if state == "VIOLATED":
                    limitations.append("当前患者违反适用条件，不得作为该患者的支持证据")
                items.append(EvidenceItem(
                    evidence_id=identity("evidence", [evidence.evidence_id, ref.model_dump(mode="json")]),
                    statement_id=statement.statement_id, document_id=block["document_id"],
                    span_id=ref.block_id, clause_ids=[statement.statement_id], title=document["title"],
                    quote=ref.quote, context=block["text"], page=block.get("page"), version=release_id,
                    explanation_zh=statement.zh_annotation.text if statement.zh_annotation and statement.zh_annotation.status == "verified" else None,
                    source_language=document.get("language", "und"),
                    matched_by=[backend], review_provenance=statement.status,
                    applicability={"SATISFIED": "applicable", "UNKNOWN": "unknown", "VIOLATED": "not_applicable"}[state],
                    allowed_use="conditional_support" if state == "SATISFIED" else "background",
                    limitations=limitations, condition_assessment=[c.model_dump(mode="json") for c in evidence.condition_results]))
        if time.monotonic() >= deadline:
            raise TimeoutError("retrieval_deadline")
        return result.backend_status.get(name, "failed"), items, {backend: {
            "module": "neurogra_kg", "release_id": result.release_id,
            "reference_time_known": reference_known, "omitted_observations": omitted,
            "data_requests": [r.model_dump(mode="json") for r in result.data_requests],
            "gaps": [g.model_dump(mode="json") for g in result.gaps], "warnings": result.warnings,
            "llm_calls_allowed": 0}}
