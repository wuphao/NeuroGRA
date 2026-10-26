"""Bounded graph retrieval with verbatim, offset-traceable source excerpts."""
import json
import re
from collections import defaultdict

def compact_patient(prepared):
    """Factor repeated context out, retaining each fact's identifier, path and value."""
    groups = {}
    items = []
    for fact in prepared['sources']:
        parent, field = fact['pointer'].rsplit('/', 1)
        key = json.dumps([parent, fact['context']], ensure_ascii=False, sort_keys=True)
        group = groups.setdefault(key, {'path': parent, 'context': fact['context'], 'facts': []})
        group['facts'].append([fact['id'], field, fact['value']])
        items.append({'ref': fact['id'], 'text': json.dumps(
            {'context': fact['context'], 'field': field, 'value': fact['value']},
            ensure_ascii=False, separators=(',', ':'))})
    return {'fact_columns': ['ref', 'field', 'value'], 'groups': list(groups.values()),
            'input_limitations': prepared['visible'].get('data_quality', {}),
            'attachments': 'Only supplied textual imaging reports are used; image files are not interpreted.'}, items


def summary_preview(text, limit):
    if len(text) <= limit:
        return [{'start': 0, 'end': len(text), 'text': text}]
    width = limit // 3
    starts = [0, (len(text) - width) // 2, len(text) - width]
    return [{'start': s, 'end': s + width, 'text': text[s:s + width]} for s in starts]


def prompt_graph(context):
    """Short graph-local aliases avoid repeating long internal IDs in the prompt."""
    aliases = {(e['gid'], e['id']): 'N' + str(i + 1) for i, e in enumerate(context['entities'])}
    return {'entities': [{'id': aliases[(e['gid'], e['id'])],
                          **{k: e[k] for k in ('name', 'type', 'refs')}} for e in context['entities']],
            'relationships': [{'source': aliases[(r['gid'], r['source'])],
                               'target': aliases[(r['gid'], r['target'])],
                               **{k: r[k] for k in ('kind', 'refs')}} for r in context['relationships']]}


def prompt_references(refs):
    return [{'ref': ref, 'page': entry['page'], 'text': entry['text']} for ref, entry in refs.items()]


def score_trends(facts):
    """Arithmetic only: no cutoffs, disease interpretation or interpolation."""
    groups = defaultdict(list)
    for fact in facts:
        field = fact['pointer'].rsplit('/', 1)[-1]
        date = fact['context'].get('date')
        if (date and type(fact['value']) in (int, float) and
                any(term in field for term in ('总分', '汇总得分', '总体评分'))):
            groups[(fact['context'].get('form'), field)].append(fact)
    result = []
    for (form, field), rows in groups.items():
        if len(rows) < 2:
            continue
        rows.sort(key=lambda f: (f['context']['date'], f['id']))
        low, high = min(rows, key=lambda f: f['value']), max(rows, key=lambda f: f['value'])
        result.append([form, field, rows[0]['id'], rows[0]['value'], rows[-1]['id'], rows[-1]['value'],
                       low['id'], low['value'], high['id'], high['value'],
                       rows[-1]['value'] - rows[0]['value']])
    return {'columns': ['form', 'field', 'first_ref', 'first_value', 'last_ref', 'last_value',
                        'min_ref', 'observed_min', 'max_ref', 'observed_max', 'last_minus_first'],
            'rows': result, 'note': 'Dated numeric supplied visits only; dates resolve via patient facts. '
                                    'Arithmetic, not clinical interpretation.'}


def graph_candidates(graphs, chosen, top, links, vector):
    import numpy as np
    linked = {}
    for link in links:
        if link['from_gid'] == top['gid']:
            key = (link['to_gid'], link['target'])
            linked[key] = max(linked.get(key, -1), link['similarity'])
    ranked = []
    for graph in graphs:
        if graph['gid'] not in chosen:
            continue
        entities = graph['entities']
        if not entities:
            continue
        matrix = np.asarray([e['embedding'] for e in entities], dtype=float)
        query = np.asarray(vector, dtype=float)
        scores = matrix @ query / (np.maximum(np.linalg.norm(matrix, axis=1), 1e-12) *
                                  max(float(np.linalg.norm(query)), 1e-12))
        for entity, score in zip(entities, scores):
            link = linked.get((graph['gid'], entity['id']), 0)
            ranked.append((0.7 * float(score) + 0.3 * link, graph, entity))
    return sorted(ranked, key=lambda row: (-row[0], row[1]['gid'], row[2]['id']))


def retrieve(graphs, chosen, top, links, vector, refs, query_text, config, fits, embed=None):
    ranked = graph_candidates(graphs, chosen, top, links, vector)
    scores = defaultdict(float)
    for score, graph, entity in ranked:
        for ref in entity['refs']:
            if ref in refs:
                scores[ref] = max(scores[ref], score)
    # Evidence comes from actual graph nodes, with neighborhood relations retained below.
    words = set(re.findall(r'[a-zA-Z][a-zA-Z0-9-]{2,}', query_text.casefold()))
    candidates = []
    def similarity(other):
        import numpy as np
        a, b = np.asarray(vector, dtype=float), np.asarray(other, dtype=float)
        return float(a @ b) / max(float(np.linalg.norm(a) * np.linalg.norm(b)), 1e-12)
    # Re-rank only graph-reachable source blocks; graph links still contribute to each score.
    block_scores = [(0.4 * score + 0.6 * similarity(embed(refs[ref]['text'])) if embed else score, ref)
                    for ref, score in scores.items()]
    block_scores.sort(key=lambda row: (-row[0], row[1]))
    for score, ref in block_scores[:40]:
        text = refs[ref]['text']
        windows = [(s, text[s:s + config.evidence_excerpt_chars])
                   for s in range(0, len(text), max(1, config.evidence_excerpt_chars // 2))]
        if embed:
            window_scores = [(similarity(embed(text)), start, text) for start, text in windows]
            window_score, start, excerpt = max(window_scores, key=lambda row: (row[0], -row[1]))
            score = 0.5 * score + 0.5 * window_score
        else:
            start, excerpt = max(windows, key=lambda pair: (
                sum(w in pair[1].casefold() for w in words), -pair[0]))
        lexical = sum(w in excerpt.casefold() for w in words) / max(1, len(words))
        candidates.append((score + 0.1 * lexical, ref, start, excerpt))
    candidates.sort(key=lambda row: (-row[0], row[1]))

    def view(selected):
        available = set(selected)
        nodes, relations = [], []
        by_gid = {}
        eligible = []
        for score, graph, entity in ranked:
            overlap = sorted(set(entity['refs']) & available)
            if not overlap:
                continue
            node = {'gid': graph['gid'], 'id': entity['id'], 'name': entity['name'],
                    'type': entity['type'], 'refs': overlap}
            eligible.append((score, node))
        node_map = {(n['gid'], n['id']): n for _, n in eligible}
        def add(node):
            ids = by_gid.setdefault(node['gid'], set())
            if node['id'] not in ids:
                nodes.append(node)
                ids.add(node['id'])
        for _, node in eligible[:max(1, config.graph_entities // 2)]:
            add(node)
        edges = []
        for graph in graphs:
            for rel in graph['relationships']:
                overlap = sorted(set(rel['refs']) & available)
                if (overlap and (graph['gid'], rel['source']) in node_map and
                        (graph['gid'], rel['target']) in node_map):
                    edges.append({'source': rel['source'], 'target': rel['target'],
                                  'kind': rel['kind'], 'gid': graph['gid'], 'refs': overlap})
        for rel in edges:
            ids = by_gid.get(rel['gid'], set())
            if not ({rel['source'], rel['target']} & ids):
                continue
            needed = {rel['source'], rel['target']} - ids
            if len(nodes) + len(needed) > config.graph_entities or len(relations) >= config.graph_relationships:
                continue
            for entity_id in sorted(needed):
                add(node_map[(rel['gid'], entity_id)])
            relations.append(rel)
        for _, node in eligible:
            if len(nodes) >= config.graph_entities:
                break
            add(node)
        return {'entities': nodes, 'relationships': relations}

    selected, decisions = {}, []
    for score, ref, start, excerpt in candidates:
        if len(selected) >= config.evidence_blocks:
            break
        original = refs[ref]
        entry = {'ref': ref, 'document_id': original['document_id'], 'page': original['page'],
                 'excerpt_start': start, 'excerpt_end': start + len(excerpt),
                 'page_text_offset': original.get('offset', 0) + start, 'text': excerpt}
        trial = {**selected, ref: entry}
        context = view(trial)
        accepted = fits(context, trial)
        decisions.append({'ref': ref, 'score': score, 'accepted': accepted})
        if accepted:
            selected = trial
    if not selected:
        raise ValueError('no_graph_evidence_fits_context: use_larger_local_context_or_explicit_input_policy')
    return view(selected), selected, {'graph_source_candidates': len(scores),
        'reranked_blocks': len(candidates), 'decisions': decisions,
        'selection': 'graph_entity_and_patient_links_then_local_dense_source_and_window_reranking' if embed
                     else 'graph_entity_and_patient_links_then_lexical_excerpt_windows',
        'excerpt_offsets': 'Python character indices relative to original source block; page_text_offset includes block offset',
        'full_sources': 'knowledge_sources.json', 'patient_facts_omitted': 0}
