"""Source and semantic checks remain separate from a clinical correctness claim."""

from .sources import condition_leaves, resolve_quotes, validate_ref


REQUIRED_FIELDS = {"subject", "relation", "object", "conditions", "logic", "purpose", "source_scope"}


def apply_verification(statement, output, blocks):
    for ref in statement.source_refs:
        validate_ref(ref, blocks)
    for refs in statement.field_evidence.values():
        for ref in refs:
            validate_ref(ref, blocks)
    for leaf in condition_leaves(statement.condition):
        for ref in leaf.source_refs:
            validate_ref(ref, blocks)
    fields = [check.field for check in output.checks]
    if set(fields) != REQUIRED_FIELDS or len(fields) != len(REQUIRED_FIELDS):
        raise ValueError("incomplete_verification_fields")
    for check in output.checks:
        refs = resolve_quotes(check.evidence_refs, blocks)
        if check.state == "supported" and not refs:
            raise ValueError("supported_field_without_evidence")
    lookup = {check.field: check.state for check in output.checks}
    issues = list(statement.issues) + output.issues
    if any(lookup[field] == "unsupported" for field in ["subject", "relation", "object", "source_scope"]):
        return statement.model_copy(update={"status": "rejected", "issues": issues + ["core_statement_unsupported"]})
    core_supported = all(lookup[field] == "supported" for field in ["subject", "relation", "object", "source_scope"])
    complete = output.verdict == "supported" and all(value == "supported" for value in lookup.values())
    update = {}
    if lookup["conditions"] != "supported" or lookup["logic"] != "supported":
        update.update(condition=None, condition_status="unresolved")
        issues.append("condition_or_logic_not_source_verified")
    if lookup["purpose"] != "supported":
        update["purposes"] = []
        issues.append("purpose_not_source_verified")
    if complete and statement.condition_status != "unresolved":
        status = "approved"
    elif core_supported:
        status = "source_checked"
        if not complete:
            issues.append("semantic_verification_partial")
    else:
        status = "draft"
    return statement.model_copy(update={"status": status, "issues": issues, **update})
