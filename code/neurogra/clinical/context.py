"""Build complete bounded specialist requests, with full material manifests."""
from .batching import observation_view
from .context_budget import ContextBudgetExceeded, measure, messages_for
from .context_views import bounded_rows, evidence_row, select_evidence, tool_receipt, source_context_rows
from .utils import dumps, fingerprint


class ContextBuilder:
    def __init__(self, model):
        self.model, self.policy = model, model.context

    def decision_context(self, context, system, schema):
        from copy import deepcopy
        view = deepcopy(context)
        catalog = view.get('catalog', {})
        catalog['total_observations'] = catalog.get('total_observations', len(catalog.get('observations', [])))
        catalog['observations'] = catalog.get('observations', [])[:16]
        catalog['field_count'] = len(catalog.get('fields', []))
        catalog['fields'] = catalog.get('fields', [])[:16]
        pending, omitted = bounded_rows(view.get('pending_requests', []), 6000, 8)
        view['pending_requests'] = pending
        view['omitted_pending_request_count'] = len(omitted)
        for result in view.get('results', []):
            selected, omitted_claims = bounded_rows(result.get('claims', []), 1600, 2)
            result['claims'] = selected
            result['omitted_claim_count'] = result.get('omitted_claim_count', 0) + len(omitted_claims)
        view['role_input_refs'] = {role: refs[:8] for role, refs in view.get('role_input_refs', {}).items()}
        visible = {o['observation_id'] for o in catalog.get('observations', [])}
        visible.update(ref for refs in view['role_input_refs'].values() for ref in refs)
        visible.update(ref for row in pending for ref in row['request'].get('source_refs', []))
        view['allowed_source_refs'] = sorted(visible)
        while not measure(messages_for(system, view), schema, self.model)['fits']:
            if view['pending_requests']:
                view['pending_requests'].pop()
                view['omitted_pending_request_count'] += 1
            elif any(r.get('claims') for r in view.get('results', [])):
                result = next(r for r in view['results'] if r.get('claims'))
                result['claims'].pop()
                result['omitted_claim_count'] += 1
            else:
                raise ContextBudgetExceeded('main_decision_fixed_context_exceeds_budget')
        return view

    def build(self, task, context, batch, batch_index):
        policy = self.policy
        facts = context.snapshot.observations
        aliases = {f'O{i + 1}': o for i, o in enumerate(batch)}
        background = [o for o in facts if 'background' in o.domains
                      and o.observation_id not in {r.observation_id for r in batch}]
        background.sort(key=lambda o: (o.name not in {'患病情况', '年龄', '性别'}, o.name))
        rows, _ = bounded_rows([observation_view(o) for o in background], policy.background_chars)
        selected_background = {r['observation_id'] for r in rows}
        background_aliases = {f'B{i + 1}': o for i, o in enumerate(background)
                              if o.observation_id in selected_background}
        evidence = select_evidence(context.evidence, batch, policy.evidence_chars)
        evidence_aliases = {f'E{i + 1}': e for i, e in enumerate(evidence)}
        aliases.update(background_aliases)
        receipts, omitted_receipts = bounded_rows(
            [tool_receipt(r) for r in reversed(context.request_outcomes)], policy.receipt_chars)
        excerpts, omitted_excerpts = bounded_rows(list(source_context_rows(context.additional_context)),
                                                  policy.source_context_chars)
        source_pages, _ = bounded_rows([{
            'artifact_ref': row.get('artifact_ref'), 'next_cursor': row.get('next_cursor'),
            'total_count': row.get('total_count')} for row in context.additional_context
            if row.get('next_cursor')], 1000, 4)
        data = {'observations': [observation_view(o) | {'observation_id': alias}
                                 for alias, o in aliases.items() if alias.startswith('O')],
                'background': [observation_view(o) | {'observation_id': alias}
                               for alias, o in background_aliases.items()],
                'evidence': [evidence_row(e) | {'evidence_id': alias}
                             for alias, e in evidence_aliases.items()],
                'questions': task.questions, 'batch': batch_index + 1,
                'request_outcomes': receipts, 'additional_context': excerpts,
                'source_pages': source_pages,
                'scope': '仅分析本批；省略不代表未检查或阴性。完整资料可由主Agent按引用回读。',
                'availability': {'total_observations': len(facts), 'selected_observations': len(aliases),
                    'omitted_observations': len(facts) - len(aliases),
                    'total_evidence': len(context.evidence), 'selected_evidence': len(evidence),
                    'omitted_receipts': len(omitted_receipts), 'omitted_source_excerpts': len(omitted_excerpts)},
                'previous_result': None}
        if context.previous_result:
            obs_alias = {o.observation_id: alias for alias, o in aliases.items()}
            e_alias = {e.evidence_id: alias for alias, e in evidence_aliases.items()}
            previous = []
            for claim in context.previous_result.claims:
                if set(claim.observation_ids) <= obs_alias.keys() and set(claim.evidence_ids) <= e_alias.keys():
                    previous.append({**claim.model_dump(exclude={'claim_id'}),
                        'observation_ids': [obs_alias[r] for r in claim.observation_ids],
                        'evidence_ids': [e_alias[r] for r in claim.evidence_ids]})
            selected, omitted = bounded_rows(previous, policy.previous_chars, 6)
            data['previous_result'] = {'version': context.previous_result.version,
                'status': context.previous_result.status, 'claims': selected,
                'omitted_claim_count': len(context.previous_result.claims) - len(selected)}
        manifest = {'patient_id': context.snapshot.patient_id, 'case_version': task.case_version,
            'task_id': task.task_id, 'role': task.agent_type, 'questions': task.questions,
            'policy_version': policy.version, 'input_hash': fingerprint(data),
            'observation_aliases': {a: o.observation_id for a, o in aliases.items()},
            'evidence_aliases': {a: e.evidence_id for a, e in evidence_aliases.items()},
            'omitted_observation_ids': [o.observation_id for o in facts
                                      if o.observation_id not in {r.observation_id for r in aliases.values()}],
            'omitted_evidence_ids': [e.evidence_id for e in context.evidence if e not in evidence],
            'source_context_hashes': [fingerprint(row) for row in omitted_excerpts]}
        data['continuation_ref'] = task.task_id
        manifest['input_hash'] = fingerprint(data)
        return data, aliases, evidence_aliases, manifest

    def iter_batches(self, task, context, system, schema):
        facts = context.snapshot.observations
        current, index = [], 0
        for item in facts:
            candidate = current + [item]
            data, *rest = self.build(task, context, candidate, index)
            metrics = measure(messages_for(system, data), schema, self.model)
            fits = metrics['fits'] and metrics['estimated_input_tokens'] <= metrics['input_token_limit'] - 256
            if current and (len(candidate) > self.policy.max_batch_items
                            or len(dumps(data)) > min(self.policy.soft_chars, self.model.max_input_chars)
                            or not fits):
                yield current, self.build(task, context, current, index)
                index += 1
                current = []
                data, *rest = self.build(task, context, [item], index)
                fits = measure(messages_for(system, data), schema, self.model)['fits']
            if not fits:
                # The caller records this item as failed and can still process later items.
                yield [item], ContextBudgetExceeded('single_item_or_fixed_context_exceeds_budget:' + item.observation_id)
                continue
            current.append(item)
        if current:
            yield current, self.build(task, context, current, index)

