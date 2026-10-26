"""Optional text-to-fact boundary with strict source and field validation."""

from ..catalogs import FIELDS
from ..construction.sources import resolve_quotes
from ..contracts.domain import PatientFact
from ..contracts.tasks import FactOutput
from ..utils import identity
from ..terminology import canonical_field, canonical_value, canonical_method


def extract_patient_facts(records, reference_time, llm, budget, field_catalog=None):
    try:
        return _extract_patient_facts(records, reference_time, llm, budget, field_catalog)
    except ValueError as exc:
        if hasattr(llm, "invalidate_last"):
            llm.invalidate_last(exc)
        raise


def _extract_patient_facts(records, reference_time, llm, budget, field_catalog=None):
    fields = FIELDS if field_catalog is None else field_catalog
    blocks = {block.block_id: block for block in records}
    if len(blocks) != len(records):
        raise ValueError("duplicate_patient_record_id")
    output = llm.execute("L0", {"records": [block.model_dump(mode="json") for block in records],
                        "field_catalog": fields, "reference_time": reference_time.isoformat()}, FactOutput, budget)
    facts = []
    seen = set()
    for row in output.facts:
        original = row.model_dump(mode="json")
        row.field = canonical_field(row.field)
        row_key = identity("fact", row.model_dump(mode="json"))
        if row.field not in fields:
            raise ValueError("unrecognized_extracted_fact")
        if row_key in seen:
            continue
        seen.add(row_key)
        value_type = fields[row.field]["type"]
        valid = (value_type == "boolean" and isinstance(row.value, bool)
                 or value_type == "number" and isinstance(row.value, (int, float)) and not isinstance(row.value, bool)
                 or value_type == "string" and isinstance(row.value, str))
        if not valid:
            raise ValueError("extracted_fact_field_type_mismatch")
        refs = resolve_quotes(row.source_refs, blocks)
        facts.append(PatientFact(fact_id=identity("fact", [original]),
            field=row.field, value=canonical_value(row.field, row.value), unit=row.unit, polarity=row.polarity,
            observed_at=row.observed_at, method=canonical_method(row.method), source_ref=refs[0]))
    return facts, output.issues
