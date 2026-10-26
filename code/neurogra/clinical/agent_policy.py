"""Code-enforced knowledge boundaries for independently configured agents."""
from copy import deepcopy


def evidence_allowed(row, access):
    tags = row.get('matched_by', [])
    if row.get('statement_id') or any(t == 'kg' or t.startswith('kg_') for t in tags):
        return access.use_kg
    if any(t.startswith('kb_') for t in tags):
        return access.use_kb
    # Unknown provenance must never bypass a disabled source.
    return access.use_kb and access.use_kg


def scoped_tool_data(data, access):
    result = deepcopy(data)
    result['evidence'] = [r for r in result.get('evidence', []) if evidence_allowed(r, access)]
    if not access.use_kg:
        for key in ('gaps', 'data_requests', 'kg_release_id'):
            result.pop(key, None)
    if not access.use_kb:
        result.pop('kb_release_id', None)
    result['backend_status'] = {k: v for k, v in result.get('backend_status', {}).items()
        if not ((k == 'kb' or k.startswith('kb_')) and not access.use_kb)
        and not ((k == 'kg' or k.startswith('kg_')) and not access.use_kg)}
    return result
