"""Deterministic comparisons and three-valued aggregation with explicit unknowns."""

import math
import re
from datetime import timezone

from ..contracts.domain import ConditionResult
from ..contracts.tasks import ApplicabilityOutput


def normalized_time(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def canonical_unit(unit):
    return {"岁": "year", "years": "year", "年": "year", "分": "score",
            "points": "score", "µg/l": "ug/l", "μg/l": "ug/l"}.get((unit or "").casefold(), (unit or "").casefold())


def convert_value(value, actual, expected):
    actual, expected = canonical_unit(actual), canonical_unit(expected)
    if actual == expected:
        return value
    scales = {"g/l": 1, "mg/l": .001, "ug/l": .000001, "mg/dl": .01,
              "mmol/l": 1, "umol/l": .001, "nmol/l": .000001}
    groups = [{"g/l", "mg/l", "ug/l", "mg/dl"}, {"mmol/l", "umol/l", "nmol/l"}]
    if isinstance(value, (float, int)) and not isinstance(value, bool) and any(actual in group and expected in group for group in groups):
        return value * scales[actual] / scales[expected]
    raise ValueError("unit_missing_or_incompatible")


def aggregate(kind, states):
    if kind == "NOT":
        return {"SATISFIED": "VIOLATED", "VIOLATED": "SATISFIED", "UNKNOWN": "UNKNOWN"}[states[0]]
    if kind == "ALL":
        return "VIOLATED" if "VIOLATED" in states else "SATISFIED" if all(s == "SATISFIED" for s in states) else "UNKNOWN"
    return "SATISFIED" if "SATISFIED" in states else "VIOLATED" if all(s == "VIOLATED" for s in states) else "UNKNOWN"


def evaluate_condition(condition, facts, reference_time, llm=None, budget=None):
    if condition.kind in {"ALL", "ANY", "NOT"}:
        children = [condition.child] if condition.kind == "NOT" else condition.children
        results = [evaluate_condition(child, facts, reference_time, llm, budget) for child in children]
        return aggregate(condition.kind, [state for state, _ in results]), [item for _, leaves in results for item in leaves]
    if condition.kind == "TEXT":
        return evaluate_text(condition, facts, reference_time, llm, budget)
    result = evaluate_atom(condition, facts, reference_time)
    return result.state, [result]


def evaluate_atom(condition, facts, reference_time):
    from ..terminology import canonical_field, canonical_method, canonical_population, canonical_value
    def result(state, reason, matched=(), missing=(), conflicts=()):
        return ConditionResult(condition_id=condition.condition_id, state=state, method="deterministic",
            used_fact_ids=list(matched), missing_fields=list(missing), conflicts=list(conflicts), reason=reason)
    reference = normalized_time(reference_time)
    field = canonical_field(condition.field)
    candidates = [fact for fact in facts if canonical_field(fact.field) == field
                  and (fact.observed_at is None or normalized_time(fact.observed_at) <= reference)]
    if not candidates:
        return result("UNKNOWN", "required_fact_not_recorded", missing=[condition.field])
    if condition.population:
        populations = [fact for fact in facts if canonical_field(fact.field) == "population" and fact.polarity == "affirmed"
                       and (fact.observed_at is None or normalized_time(fact.observed_at) <= reference)]
        if not populations or not any(str(canonical_population(fact.value)).casefold().strip() == str(canonical_population(condition.population)).casefold().strip() for fact in populations):
            return result("UNKNOWN", "population_membership_not_recorded", matched=[f.fact_id for f in candidates], missing=["population"])
    if condition.time_scope:
        match = re.fullmatch(r"max_age_days:(\d+)", condition.time_scope)
        if not match:
            return result("UNKNOWN", "unrecognized_time_scope", matched=[f.fact_id for f in candidates])
        candidates = [fact for fact in candidates if fact.observed_at is not None and
                      (reference - normalized_time(fact.observed_at)).total_seconds() <= int(match.group(1)) * 86400]
        if not candidates:
            return result("UNKNOWN", "fact_missing_in_required_time_window", missing=[condition.field])
    if condition.method:
        candidates = [fact for fact in candidates if fact.method and str(canonical_method(fact.method)).casefold().strip() == str(canonical_method(condition.method)).casefold().strip()]
        if not candidates:
            return result("UNKNOWN", "required_method_not_recorded", missing=[condition.field + ":method"])
    # Use a latest visit only if every competing record has a known time.
    if len(candidates) > 1 and all(f.observed_at is not None for f in candidates):
        latest = max(normalized_time(f.observed_at) for f in candidates)
        candidates = [fact for fact in candidates if normalized_time(fact.observed_at) == latest]
    values = []
    for fact in candidates:
        if fact.polarity == "uncertain":
            return result("UNKNOWN", "uncertain_patient_fact", matched=[f.fact_id for f in candidates])
        value = canonical_value(field, fact.value)
        if fact.polarity == "negated":
            if isinstance(value, bool):
                value = not value
            else:
                return result("UNKNOWN", "non_boolean_negation_not_comparable", matched=[fact.fact_id])
        if condition.unit is not None:
            try:
                value = convert_value(value, fact.unit, condition.unit)
            except ValueError:
                return result("UNKNOWN", "unit_missing_or_incompatible", matched=[fact.fact_id], missing=[condition.field + ":unit"])
        if isinstance(value, float) and not math.isfinite(value):
            return result("UNKNOWN", "non_finite_patient_value", matched=[fact.fact_id])
        values.append(value)
    if any(value != values[0] or type(value) is not type(values[0]) for value in values[1:]):
        return result("UNKNOWN", "conflicting_patient_facts", matched=[f.fact_id for f in candidates], conflicts=[condition.field])
    value, target = values[0], canonical_value(field, condition.value)
    try:
        if condition.operator in {"gt", "gte", "lt", "lte"}:
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ValueError("non_numeric_fact")
            answer = {"gt": lambda: value > target, "gte": lambda: value >= target,
                      "lt": lambda: value < target, "lte": lambda: value <= target}[condition.operator]()
        elif condition.operator == "exists":
            answer = bool(target)  # Record exists; absent records always stay UNKNOWN.
        elif condition.operator == "in":
            answer = value in target
        else:
            # Boolean and number equality must not equate True with 1.
            equal = value == target and isinstance(value, bool) == isinstance(target, bool)
            answer = equal if condition.operator == "eq" else not equal
        return result("SATISFIED" if answer else "VIOLATED", "explicit_fact_comparison", matched=[f.fact_id for f in candidates])
    except (TypeError, ValueError):
        return result("UNKNOWN", "condition_fact_type_mismatch", matched=[f.fact_id for f in candidates])


def evaluate_text(condition, facts, reference_time, llm, budget):
    unknown = ConditionResult(condition_id=condition.condition_id, state="UNKNOWN", method="unresolved",
                              reason="text_condition_requires_model")
    if not llm:
        return "UNKNOWN", [unknown]
    if budget is not None and budget.llm_calls >= budget.max_llm_calls:
        unknown.reason = 'text_condition_model_budget_unavailable'
        return 'UNKNOWN', [unknown]
    # Keep complete facts for atomic comparisons; text evaluation receives an
    # explicit latest-per-field view, retaining undated and same-date conflicts.
    text_facts = []
    for field in sorted({fact.field for fact in facts}):
        values = [fact for fact in facts if fact.field == field]
        if values and all(f.observed_at is not None for f in values):
            latest = max(normalized_time(f.observed_at) for f in values)
            values = [f for f in values if normalized_time(f.observed_at) == latest]
        text_facts.extend(values)
    try:
        output = llm.execute("L5", {"condition": condition.model_dump(mode="json"),
            "facts": [fact.model_dump(mode="json") for fact in text_facts],
            "fact_selection": 'latest_per_field_when_all_dates_known_else_all; same_date_conflicts_retained',
            "reference_time": reference_time.isoformat(), "allowed_fields": sorted({fact.field for fact in text_facts})},
            ApplicabilityOutput, budget)
        by_id = {fact.fact_id: fact for fact in text_facts}
        if output.condition_id != condition.condition_id or not set(output.used_fact_ids) <= set(by_id):
            raise ValueError("textual_condition_reference_mismatch")
        if output.state != "UNKNOWN" and not output.used_fact_ids:
            raise ValueError("textual_condition_without_facts")
        if output.state != "UNKNOWN" and any(by_id[id].polarity == "uncertain" or
                (by_id[id].observed_at and normalized_time(by_id[id].observed_at) > normalized_time(reference_time))
                for id in output.used_fact_ids):
            raise ValueError("textual_condition_uses_invalid_facts")
        # Quotes can only come from supplied patient excerpts or the supplied condition.
        valid_quotes = [(fact.source_ref.block_id, fact.source_ref.quote) for fact in facts]
        valid_quotes.extend((ref.block_id, ref.quote) for ref in condition.source_refs)
        if any(not any(ref.block_id == block_id and ref.quote in quote for block_id, quote in valid_quotes)
               for ref in output.evidence_refs):
            raise ValueError("textual_condition_quote_unbound")
        if output.state != "UNKNOWN" and (not output.evidence_refs or output.conflicts):
            raise ValueError("textual_condition_evidence_incomplete")
        result = ConditionResult(condition_id=condition.condition_id, state=output.state, method="llm_textual",
            used_fact_ids=output.used_fact_ids, missing_fields=output.missing_fields,
            conflicts=output.conflicts, reason=output.reason)
        return result.state, [result]
    except Exception as exc:
        if isinstance(exc, ValueError) and hasattr(llm, "invalidate_last"):
            llm.invalidate_last(exc)
        unknown.reason = "text_condition_unresolved:" + type(exc).__name__
        return "UNKNOWN", [unknown]
