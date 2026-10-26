"""Deterministic projections. Full clinical values stay in the source store."""
from .utils import dumps, fingerprint


def source_page(data, request, binding, max_chars=6000):
    """Page complete source objects; refuse an indivisible oversized object explicitly."""
    items = []
    for key, value in data.items():
        if isinstance(value, list):
            items.extend((key, row, True) for row in value)
        else:
            items.append((key, value, False))
    digest = fingerprint([binding, data])
    start = 0
    if request.cursor:
        try:
            actual, offset = request.cursor.rsplit(':', 1)
            start = int(offset)
            if actual != digest or not 0 <= start <= len(items):
                raise ValueError()
        except (ValueError, TypeError):
            raise ValueError('stale_or_invalid_source_cursor') from None
    selected, consumed = {}, 0
    for key, value, is_list in items[start:]:
        from copy import deepcopy
        candidate = deepcopy(selected)
        if is_list:
            candidate.setdefault(key, []).append(value)
        else:
            candidate[key] = value
        if len(dumps(candidate)) > max_chars:
            if not consumed:
                raise ValueError('source_item_exceeds_page_budget:read_original_artifact')
            break
        selected = candidate
        consumed += 1
        if consumed >= (request.page_size or 10):
            break
    selected.update(total_count=len(items), selected_count=consumed,
        next_cursor=f'{digest}:{start + consumed}' if start + consumed < len(items) else None,
        scope_notice='本页只包含部分来源对象；省略不代表资料不存在。')
    return selected


def bounded_rows(rows, chars, max_items=None):
    selected, omitted = [], []
    for row in rows:
        if ((max_items is None or len(selected) < max_items)
                and len(dumps(selected + [row])) <= chars):
            selected.append(row)
        else:
            omitted.append(row)
    return selected, omitted


def tool_receipt(action):
    data = action.get('data') or {}
    request = action.get('request') or {}
    return {'action_id': action.get('action_id'), 'type': request.get('type'),
            'status': action.get('status', data.get('status')),
            'question_ref': fingerprint(request.get('question')), 'purpose': request.get('purpose'),
            'requested_fields': request.get('requested_fields', []),
            'form_key': request.get('form_key'), 'date_from': request.get('date_from'),
            'date_to': request.get('date_to'),
            'error': action.get('error') or data.get('error'),
            'evidence_count': len(data.get('evidence', [])),
            'observation_count': len(data.get('observations', [])),
            'artifact_ref': action.get('action_id'),
            'content_hash': fingerprint(data)}


def evidence_row(e):
    return {'evidence_id': e.evidence_id, 'title': e.title, 'quote': e.quote,
            'source_ref': e.span_id, 'version': e.version,
            'allowed_use': e.allowed_use, 'applicability': e.applicability,
            'limitations': e.limitations,
            'condition_assessment': e.condition_assessment, 'graph_triples': e.graph_triples}


def select_evidence(evidence, facts, chars):
    words = {o.name for o in facts}
    ranked = sorted(enumerate(evidence), key=lambda item: (
        -sum(word in ((item[1].title or '') + item[1].quote) for word in words), item[0]))
    unique, seen = [], set()
    for _, e in ranked:
        row = evidence_row(e)
        key = fingerprint({k: v for k, v in row.items() if k != 'evidence_id'})
        if key not in seen:
            unique.append(e)
            seen.add(key)
    selected, _ = bounded_rows([evidence_row(e) for e in unique], chars, 5)
    ids = {r['evidence_id'] for r in selected}
    return [e for e in unique if e.evidence_id in ids]


def source_context_rows(contexts):
    """Keep actual source excerpts, not arbitrary free-form tool envelopes."""
    for data in contexts:
        if data.get('source'):
            yield {'source': data['source'], 'release_id': data.get('release_id')}
        if data.get('text'):
            yield {k: v for k, v in data.items() if k in {'text', 'source_locator', 'block_id', 'release_id'}}
        for key in ('records', 'contexts', 'context', 'blocks', 'sources', 'evidence'):
            for row in data.get(key, []):
                yield row

