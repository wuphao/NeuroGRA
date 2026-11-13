"""Bounded follow-up searches for reviewed dialogue claims."""

import asyncio
import re
from dataclasses import dataclass

from neurogra.clinical.agent_policy import evidence_allowed
from neurogra.clinical.collaboration_contracts import (
    CollaborationGraph,
    CollaborationTask,
    TaskDependency,
)
from neurogra.clinical.collaboration_store import CollaborationConflict
from neurogra.clinical.orchestration import Executor
from neurogra.clinical.profiling import observation_indexes
from neurogra.clinical.schemas import AgentResult, CaseSnapshot, RequestContent, TaskPlan
from neurogra.clinical.storage import BudgetExceeded
from neurogra.clinical.utils import fingerprint, identity

from .locking import ResourceBusy
from .dialogue_numeric import direct_values_supported, number_tokens
from .model_queue import ModelQueueError
from .patient_workspace import WorkspaceError


@dataclass(frozen=True)
class SpecialistFollowUp:
    """A validated request from a particular specialist, not a reviewer verdict."""
    request_id: str
    task_id: str
    request: RequestContent
    original_fingerprint: str

    @property
    def reason(self):
        return self.request.reason

    @property
    def basis_ids(self):
        return self.request.source_refs


def dialogue_role_access(config, role, allow_knowledge):
    """Bind each specialist to its configured local knowledge sources."""
    access = config.main_agent if role == 'general' else config.agent(role)
    return access.model_copy(update={
        'use_kb': bool(allow_knowledge and access.use_kb),
        'use_kg': bool(allow_knowledge and access.use_kg),
    })


def scope_agent_evidence(rows, access):
    return [row for row in rows if evidence_allowed(row, access)]


def unsafe_untrusted_text(text):
    text = ' '.join(text.split())
    return bool(re.search(
        r'(?:https?|ftp)://|www\.|忽略.{0,8}(?:权限|规则|指令)|其他患者|另一位患者|'
        r'另一个患者|其他病人|上传|外发|发送.{0,8}(?:资料|数据|文件)|'
        r'(?:执行|运行).{0,8}(?:命令|脚本|程序|SQL)|(?:密码|口令|密钥|令牌)', text, re.I))


def unsafe_specialist_request(request):
    return unsafe_untrusted_text(request.question + ' ' + request.reason)


def specialist_request_numbers_supported(request, observations, known_forms=None):
    references = set(request.source_refs)
    cited = [item for item in observations if item.observation_id in references or
             references.intersection(item.source_refs)]
    if cited:
        observations = cited
    material = ' '.join(str(value) for item in observations for value in
                        (item.name, item.quote, item.value, item.unit or '', item.time.raw or '', item.time.value or ''))
    allowed = number_tokens(material)
    text = request.question + ' ' + request.reason
    return number_tokens(text) <= allowed and direct_values_supported(text, observations, known_forms)


def infer_explicit_form(request, available_forms):
    """Resolve a missing key only when the request names exactly one configured form."""
    if request.type != 'patient_data' or request.form_key:
        return request
    named = []
    for key in available_forms:
        label = available_forms[key] if isinstance(available_forms, dict) else ''
        aliases = [value for value in (key, label) if isinstance(value, str) and value.strip()]
        if any(re.search(r'(?<![a-z0-9_])' + re.escape(value) + r'(?![a-z0-9_])',
                         request.question, re.I) for value in aliases):
            named.append(key)
    return request.model_copy(update={'form_key': named[0]}) if len(named) == 1 else request


def select_specialist_followups(task_plan, task_results, snapshot, task_evidence, available_forms,
                                loaded_forms, *, knowledge_allowed, maximum=3):
    """Select only bounded, source-scoped requests that have an executable adapter."""
    if maximum < 0:
        raise ValueError('negative_followup_limit')
    observations = {row.observation_id: row for row in snapshot.observations}
    images = {row.asset_id for row in snapshot.images}
    candidates, seen, unsupported = [], set(), 0
    for task, result in zip(task_plan.tasks, task_results, strict=True):
        if result.task_id != task.task_id or result.case_version != snapshot.version or task.case_version != snapshot.version:
            raise ValueError('specialist_followup_binding_mismatch')
        if result.status not in {'completed', 'partial'}:
            unsupported += len(result.requests)
            continue
        refs = set(task.input_refs + task.background_refs) & (set(observations) | images)
        scoped_observations = [observations[ref] for ref in refs if ref in observations]
        for ref in list(refs):
            if ref in observations:
                refs.update(observations[ref].source_refs)
        for item in task_evidence.get(task.task_id, []):
            refs.update(value for value in (item.get('evidence_id'), item.get('span_id')) if value)
        for original in result.requests:
            original_fingerprint = fingerprint(original.model_dump(mode='json'))
            request = infer_explicit_form(original, available_forms)
            patient_request = (request.type == 'patient_data' and request.form_key in available_forms and
                               request.form_key not in loaded_forms and request.cursor is None)
            role_can_search = (knowledge_allowed.get(task.agent_type, False)
                               if isinstance(knowledge_allowed, dict) else knowledge_allowed)
            knowledge_request = request.type == 'knowledge' and role_can_search
            if (not set(request.source_refs) <= refs or not (patient_request or knowledge_request) or
                    unsafe_specialist_request(request) or
                    not specialist_request_numbers_supported(request, scoped_observations,
                        available_forms if isinstance(available_forms, dict) else {key: key for key in available_forms})):
                unsupported += 1
                continue
            signature = fingerprint([task.task_id, request.model_dump(mode='json')])
            if signature in seen:
                continue
            seen.add(signature)
            candidates.append(SpecialistFollowUp(identity('specialist_request', [task.task_id, signature]),
                                                 task.task_id, request, original_fingerprint))
    priority = {'high': 0, 'normal': 1, 'low': 2}
    candidates.sort(key=lambda item: (priority[item.request.priority], item.request_id))
    return {'selected': candidates[:maximum], 'deferred': max(0, len(candidates) - maximum),
            'unsupported': unsupported}


def expanded_snapshot(snapshot, supplement):
    """Keep original source identities while adding separately fetched form facts."""
    additions = [item['patient_snapshot'] for item in supplement['results']
                 if item['status'] == 'found' and item.get('patient_snapshot')]
    unique = {item.raw_input_hash: item for item in additions}
    if not unique:
        return snapshot
    if any(item.patient_id != snapshot.patient_id for item in unique.values()):
        raise CollaborationConflict('supplement_patient_mismatch')

    def merge_rows(attribute, key):
        rows = {}
        for part in [snapshot, *unique.values()]:
            for row in getattr(part, attribute):
                identifier = getattr(row, key)
                if identifier in rows and fingerprint(rows[identifier]) != fingerprint(row):
                    raise CollaborationConflict('supplement_source_conflict')
                rows[identifier] = row
        return list(rows.values())

    parts = [snapshot, *unique.values()]
    domains = list(dict.fromkeys(item.domain for part in parts for item in part.inventory))
    inventory = []
    for domain in domains:
        entries = [item for part in parts for item in part.inventory if item.domain == domain]
        states = {item.availability for item in entries}
        if domain == 'unknown' and 'unclassified' in states:
            availability = 'unclassified'
        elif 'partial' in states or ('unreadable' in states and 'available' in states):
            availability = 'partial'
        elif 'available' in states:
            availability = 'available'
        elif 'unreadable' in states:
            availability = 'unreadable'
        else:
            availability = 'absent'
        inventory.append(entries[0].model_copy(update={
            'availability': availability,
            'record_ids': list(dict.fromkeys(ref for item in entries for ref in item.record_ids)),
            'observation_ids': list(dict.fromkeys(ref for item in entries for ref in item.observation_ids)),
            'limitations': list(dict.fromkeys(text for item in entries for text in item.limitations))}))
    processing = {}
    for part in parts:
        for record_id, status in part.processing.items():
            if record_id in processing and processing[record_id] != status:
                raise CollaborationConflict('supplement_processing_conflict')
            processing[record_id] = status
    observations = merge_rows('observations', 'observation_id')
    timeline, conflicts = observation_indexes(observations)
    return snapshot.model_copy(deep=True, update={
        'version': snapshot.version + 1,
        'raw_input_hash': fingerprint([snapshot.raw_input_hash, sorted(unique)]),
        'records': merge_rows('records', 'record_id'),
        'observations': observations, 'images': merge_rows('images', 'asset_id'),
        'inventory': inventory, 'processing': processing, 'timeline': timeline, 'conflict_groups': conflicts,
        'issues': snapshot.issues + [issue for part in unique.values() for issue in part.issues]})


def uncertain_after_patient_change(evidence):
    """Previously evaluated applicability is not silently carried to a new case snapshot."""
    return [{**item, 'applicability': 'unknown', 'allowed_use': 'background',
             'limitations': list(dict.fromkeys((item.get('limitations') or []) +
                                               ['补充患者资料后需重新核查适用条件']))}
            for item in evidence]


def run_review_supplements(*, issues, claim_nodes, node_by_task, task_nodes, review_node,
                           coordination, fence, store, run_id, snapshot, config, release,
                           patient_key, available_forms, handlers, tool_factory, select_evidence,
                           admission, check, before_model, resume, patient_reader=None,
                           loaded_forms=(), maximum=3, graph_nodes=None, graph_version=3,
                           claim_refs=None, shared_evidence=(), agent_access=None):
    """Execute validated follow-ups or pass existing cross-specialist objections.

    The caller retains every issue as unresolved until a specialist has revisited
    it. A returned citation is not proof that the original claim is correct.
    """
    task_for_node = {node: task_id for task_id, node in node_by_task.items()}
    roles_by_task = {node.task_id: node.agent_type for node in (graph_nodes or task_nodes)
                     if node.agent_type}
    candidates, deferred, unsupported = [], 0, 0
    seen = set()
    for issue in issues:
        request = issue.request
        specialist_request = isinstance(issue, SpecialistFollowUp)
        target_id = issue.request_id if specialist_request else issue.claim_id
        direct_review = (not specialist_request and request is None and
                         bool(set(issue.basis_ids) - set((claim_refs or {}).get(target_id, ()))))
        if request is None and not direct_review:
            continue
        if not specialist_request:
            related = set(issue.basis_ids) | set((claim_refs or {}).get(target_id, ()))
            observations = [row for row in snapshot.observations if row.observation_id in related]
            sources = [row for row in shared_evidence if row.get('evidence_id') in related]
            material = ' '.join(str(value) for row in observations for value in
                                (row.name, row.quote, row.value, row.unit or '', row.time.raw or '',
                                 row.time.value or ''))
            material += ' ' + ' '.join(str(row.get('quote', '')) for row in sources)
            forms = available_forms if isinstance(available_forms, dict) else {key: key for key in available_forms}
            if (unsafe_untrusted_text(issue.reason) or not number_tokens(issue.reason) <= number_tokens(material) or
                    not direct_values_supported(issue.reason, observations, forms)):
                unsupported += 1
                continue
        if request is not None and (unsafe_specialist_request(request) or
                not specialist_request_numbers_supported(request, snapshot.observations,
                    available_forms if isinstance(available_forms, dict) else {key: key for key in available_forms})):
            unsupported += 1
            continue
        patient_request = (request is not None and request.type == 'patient_data' and patient_reader is not None and
                           request.form_key in available_forms and request.form_key not in loaded_forms and
                           request.cursor is None)
        if request is not None and request.type != 'knowledge' and not patient_request:
            unsupported += 1
            continue
        task_id = issue.task_id if specialist_request else task_for_node[claim_nodes[target_id]]
        role = roles_by_task.get(task_id)
        if role is None:
            raise CollaborationConflict('supplement_specialist_scope_missing')
        access = (agent_access or {}).get(role) or config.agent(role)
        if request is not None and request.type == 'knowledge' and not (access.use_kb or access.use_kg):
            unsupported += 1
            continue
        material = (request.model_dump(mode='json') if request is not None else
                    {'kind': 'cross_review', 'claim_id': target_id,
                     'basis_ids': sorted(issue.basis_ids), 'reason': issue.reason})
        signature = fingerprint([task_id, material])
        if signature in seen:
            continue
        seen.add(signature)
        if len(candidates) >= maximum:
            deferred += 1
            continue
        node_id = identity('dialogue_supplement', [task_id, signature])
        dependencies = [TaskDependency(task_id=task_id, allow_partial=True)]
        if review_node is not None:
            dependencies.append(TaskDependency(task_id=review_node.task_id, allow_partial=True))
        node = CollaborationTask(task_id=node_id, kind=('evidence.review' if direct_review else
            'patient.read' if patient_request else 'knowledge.search'),
            objective=issue.reason if direct_review else request.question, case_version=snapshot.version, patient_keys=[patient_key],
            form_keys=[request.form_key] if patient_request else [],
            input_refs=list(dict.fromkeys(issue.basis_ids if direct_review else request.source_refs)),
            depends_on=dependencies)
        candidates.append((node, issue, task_id))
    if not candidates:
        return {'nodes': [], 'results': [], 'evidence': [], 'deferred': deferred, 'unsupported': unsupported}
    base_nodes = graph_nodes if graph_nodes is not None else [*task_nodes, *([review_node] if review_node else [])]
    coordination.install_graph(fence, CollaborationGraph(graph_version=graph_version,
        tasks=[*base_nodes, *(item[0] for item in candidates)]),
        form_keys=set(available_forms), agent_types=set(handlers))
    state = {row['task_id']: row for row in coordination.snapshot(run_id)['tasks']}
    if resume:
        for node, _, _ in candidates:
            if state[node.task_id]['state'] == 'interrupted':
                coordination.retry_interrupted(fence, node.task_id, node.task_version)
    by_id = {node.task_id: (node, issue, task_id) for node, issue, task_id in candidates}
    saved = {row['object_id']: row['payload'] for row in store.objects(run_id, 'collaboration_result')}
    outcomes, reused_outcomes = {}, set()
    for node, issue, _ in candidates:
        old = state[node.task_id]
        if old['state'] in {'succeeded', 'partial', 'failed'} and old['result_id']:
            outcomes[node.task_id] = saved[old['result_id']]
            reused_outcomes.add(node.task_id)
            if resume and node.kind == 'patient.read' and outcomes[node.task_id].get('data', {}).get('patient_snapshot'):
                owned = coordination.fenced_store(fence, check=check, before_model=before_model)
                try:
                    call_id, _ = owned.reserve(run_id, 'patient_data', 'revalidate:' + node.task_id)
                except BudgetExceeded:
                    raise WorkspaceError('补查资料复核已达到读取预算，请重新提问。', 409) from None
                try:
                    current, _, statuses = patient_reader(issue.request)
                    check()
                except BaseException:
                    owned.settle(call_id, 'indeterminate')
                    raise
                status = statuses[node.form_keys[0]]
                owned.settle(call_id, 'completed' if status in {'completed', 'skipped_no_data'} else 'partial')
                previous = CaseSnapshot.model_validate(outcomes[node.task_id]['data']['patient_snapshot'])
                if (current.raw_input_hash != previous.raw_input_hash or
                        status in {'failed', 'partial'}):
                    raise WorkspaceError('补查患者资料已变化或无法核实，请重新提问。', 409)
    while True:
        claimed = coordination.claim_next(fence, kinds={'knowledge.search', 'patient.read', 'evidence.review'},
                                          task_ids=set(by_id))
        if claimed is None:
            break
        node, issue, _ = by_id[claimed['task']['task_id']]
        try:
            owned = coordination.fenced_store(fence, check=check, before_model=before_model)
            if node.kind == 'evidence.review':
                check()
                payload = {'status': 'found', 'data': {'cross_review': True,
                    'evidence': [row for row in shared_evidence if row['evidence_id'] in issue.basis_ids]},
                    'error': None}
            elif node.kind == 'patient.read':
                call_id, _ = owned.reserve(run_id, 'patient_data', node.task_id)
                try:
                    supplement, gaps, statuses = patient_reader(issue.request)
                    check()
                    status = statuses[node.form_keys[0]]
                    payload = {'status': 'found' if status == 'completed' and supplement.observations else status,
                               'request': issue.request.model_dump(mode='json'),
                               'data': {'patient_snapshot': supplement.model_dump(mode='json'),
                                        'form_status': status, 'gaps': gaps}, 'error': None}
                    owned.settle(call_id, 'completed' if status in {'completed', 'skipped_no_data'} else 'partial')
                except BaseException:
                    owned.settle(call_id, 'indeterminate')
                    raise
            else:
                tools = tool_factory(snapshot, config, owned, run_id, graph_release_id=release)
                access = (agent_access or {}).get(roles_by_task[by_id[node.task_id][2]]) or config.agent(
                    roles_by_task[by_id[node.task_id][2]])
                with admission('supplement_' + node.task_id):
                    action = tools.execute(issue.request, scope=node.task_id, knowledge_access=access)
                payload = {'status': action.get('status', 'failed'), 'data': action.get('data', {}),
                           'error': action.get('error')}
        except (WorkspaceError, ResourceBusy, ModelQueueError, CollaborationConflict):
            raise
        except BudgetExceeded:
            payload = {'status': 'budget_exhausted', 'data': {}, 'error': 'budget_exhausted'}
        except Exception as error:
            payload = {'status': 'failed', 'data': {}, 'error': type(error).__name__}
        status = ('succeeded' if payload['status'] == 'found' and (
            payload['data'].get('evidence') or payload['data'].get('patient_snapshot') or
            payload['data'].get('cross_review')) else 'partial')
        coordination.finish_task(fence, node.task_id, node.task_version, claimed['attempt_id'],
                                 status=status, result=payload)
        outcomes[node.task_id] = payload
    if set(outcomes) != set(by_id):
        raise CollaborationConflict('supplement_tasks_not_ready')
    output, evidence = [], []
    for node, issue, task_id in candidates:
        outcome = outcomes[node.task_id]
        data = outcome.get('data', {})
        access = (agent_access or {}).get(roles_by_task[task_id]) or config.agent(roles_by_task[task_id])
        items = select_evidence(scope_agent_evidence(data.get('evidence', []), access))
        evidence.extend(items)
        patient_snapshot = CaseSnapshot.model_validate(data['patient_snapshot']) if data.get('patient_snapshot') else None
        output.append({'task_id': task_id,
                       'claim_id': issue.request_id if isinstance(issue, SpecialistFollowUp) else issue.claim_id,
                       'status': outcome['status'],
                       'count': len(patient_snapshot.observations) if patient_snapshot else len(items),
                       'node': node, 'issue': issue, 'evidence': items, 'patient_snapshot': patient_snapshot,
                       'cross_review': bool(data.get('cross_review')), 'reused': node.task_id in reused_outcomes})
    return {'nodes': [item[0] for item in candidates], 'results': output, 'evidence': evidence,
            'deferred': deferred, 'unsupported': unsupported}


def run_specialist_reanalysis(*, supplement, task_plan, task_results, task_evidence, task_nodes, review_node,
                              coordination, fence, store, run_id, snapshot, config, patient_key,
                              available_forms, handlers, merge_evidence, admission, check, before_model, resume,
                              graph_nodes=None, graph_version=4, shared_evidence=(), agent_access=None):
    """Re-run only affected specialists when a review search produced novel material."""
    originals = {task.task_id: (task, result) for task, result in zip(task_plan.tasks, task_results, strict=True)}
    updated_snapshot = expanded_snapshot(snapshot, supplement)
    patient_changed = updated_snapshot.version != snapshot.version
    reanalysis, rejected = [], []
    for item in supplement['results']:
        if item['status'] != 'found':
            continue
        original, previous = originals[item['task_id']]
        access = (agent_access or {}).get(original.agent_type) or config.agent(original.agent_type)
        prior = scope_agent_evidence(task_evidence.get(original.task_id, []), access)
        restricted = {ref for row in shared_evidence if not evidence_allowed(row, access)
                      for ref in (row.get('evidence_id'), row.get('span_id')) if ref}
        original_basis = list(item['issue'].basis_ids)
        basis = set(original_basis) - restricted
        reason = ('其他专科提出需要核查的差异，请依据本任务获准资料重新评估。'
                  if set(original_basis) & restricted else item['issue'].reason)
        cross_evidence = scope_agent_evidence([row for row in shared_evidence if row['evidence_id'] in basis], access)
        added_evidence = scope_agent_evidence(item['evidence'], access)
        if patient_changed:
            prior = uncertain_after_patient_change(prior)
            cross_evidence = uncertain_after_patient_change(cross_evidence)
            added_evidence = uncertain_after_patient_change(added_evidence)
        merged, conflicts = merge_evidence([prior, cross_evidence, added_evidence])
        previous_ids = {row['evidence_id'] for row in prior}
        novel = {row['evidence_id'] for row in merged} - previous_ids
        added = item.get('patient_snapshot')
        new_inputs = [row.observation_id for row in added.observations if original.agent_type in row.domains] if added else []
        new_background = [row.observation_id for row in added.observations
                          if 'background' in row.domains and row.observation_id not in new_inputs] if added else []
        cross_records = [{'kind': 'cross_specialist_observation', 'observation_id': row.observation_id,
                          'name': row.name, 'quote': row.quote, 'unit': row.unit,
                          'time': row.time.model_dump(mode='json'), 'status': row.status}
                         for row in updated_snapshot.observations if row.observation_id in basis and
                         row.observation_id not in set(original.input_refs + original.background_refs)]
        if not novel and not new_inputs and not new_background and not cross_records:
            rejected.append({'claim_id': item['claim_id'], 'reason': '补查未取得可用的新依据'})
            continue
        cross_sources = [{key: row.get(key) for key in ('evidence_id', 'title', 'quote', 'span_id',
                          'applicability', 'allowed_use', 'limitations')} for row in cross_evidence
                         if row['evidence_id'] not in conflicts and row['evidence_id'] not in previous_ids]
        task_id = identity('dialogue_reanalysis', [original.task_id, item['node'].task_id,
                                                   fingerprint(merged), fingerprint(cross_records),
                                                   updated_snapshot.raw_input_hash])
        prefix = ('针对专科补查请求重新核对；保留适用条件和资料缺口：'
                  if isinstance(item['issue'], SpecialistFollowUp) else
                  '针对该质询重新核对既有结论；保留适用条件和资料缺口：')
        question = (prefix + reason)[:500]
        task = original.model_copy(deep=True, update={'task_id': task_id,
            'case_version': updated_snapshot.version,
            'input_refs': list(dict.fromkeys(original.input_refs + new_inputs))[:160],
            'background_refs': list(dict.fromkeys(original.background_refs + new_background))[:160],
            'questions': [*original.questions, question]})
        node = CollaborationTask(task_id=task_id, kind='specialist.analyze', agent_type=task.agent_type,
            objective=question, case_version=updated_snapshot.version, patient_keys=[patient_key],
            input_refs=task.input_refs, form_keys=[], budget=task.budget,
            depends_on=[TaskDependency(task_id=original.task_id, allow_partial=True),
                        TaskDependency(task_id=item['node'].task_id, allow_partial=True)])
        reanalysis.append({'task': task, 'node': node, 'previous': previous, 'issue': item['issue'],
                           'original_task_id': original.task_id, 'evidence': merged, 'conflicts': sorted(conflicts),
                           'snapshot': updated_snapshot, 'cross_records': cross_records,
                           'cross_sources': cross_sources, 'reason': reason,
                           'basis_ids': [ref for ref in original_basis if ref not in restricted]})
    if not reanalysis:
        return {'nodes': [], 'results': [], 'rejected': rejected, 'snapshot': updated_snapshot}
    base_nodes = (graph_nodes if graph_nodes is not None else
                  [*task_nodes, *([review_node] if review_node else []), *supplement['nodes']])
    coordination.install_graph(fence, CollaborationGraph(graph_version=graph_version,
        tasks=[*base_nodes, *(row['node'] for row in reanalysis)]),
        form_keys=set(available_forms), agent_types=set(handlers))
    state = {row['task_id']: row for row in coordination.snapshot(run_id)['tasks']}
    if resume:
        for item in reanalysis:
            node = item['node']
            if state[node.task_id]['state'] == 'interrupted':
                coordination.retry_interrupted(fence, node.task_id, node.task_version)
    saved = {row['object_id']: row['payload'] for row in store.objects(run_id, 'collaboration_result')}
    completed = {item['task'].task_id: AgentResult.model_validate(saved[state[item['task'].task_id]['result_id']])
                 for item in reanalysis if state[item['task'].task_id]['state'] in {'succeeded', 'partial', 'failed', 'skipped'}
                 and state[item['task'].task_id]['result_id']}
    reused_results = set(completed)
    attempts = {}
    while True:
        claimed = coordination.claim_next(fence, kinds={'specialist.analyze'},
                                          task_ids={item['task'].task_id for item in reanalysis})
        if claimed is None:
            break
        attempts[claimed['task']['task_id']] = claimed['attempt_id']
    runnable = [item for item in reanalysis if item['task'].task_id in attempts]
    if set(attempts) | set(completed) != {item['task'].task_id for item in reanalysis}:
        raise CollaborationConflict('reanalysis_tasks_not_ready')
    if runnable:
        owned = coordination.fenced_store(fence, check=check, before_model=before_model)
        executor = Executor(owned, run_id, max_parallel=min(2, config.max_parallel_tasks),
            model_config=config.model, reserved_calls=1,
            agent_configs={item['task'].agent_type: ((agent_access or {}).get(item['task'].agent_type)
                           or config.agent(item['task'].agent_type)) for item in runnable},
            fatal_exceptions=(WorkspaceError, CollaborationConflict))
        for item in runnable:
            executor.register(item['task'].agent_type, handlers[item['task'].agent_type]())
        inputs = {item['task'].task_id: {'evidence': item['evidence'],
                  'previous_result': item['previous'].model_dump(mode='json'),
                  'additional_context': [{'kind': ('specialist_request' if isinstance(item['issue'], SpecialistFollowUp)
                                                   else 'review_issue'), 'reason': item['reason'],
                                          'basis_ids': item['basis_ids'],
                                          'records': item['cross_records'], 'evidence': item['cross_sources']}]}
                  for item in runnable}
        with admission('specialist_reanalysis'):
            results = asyncio.run(executor.execute_tasks(TaskPlan(tasks=[item['task'] for item in runnable]),
                                                         updated_snapshot, task_inputs=inputs))
        if (len(results) != len(runnable) or any(result.task_id != item['task'].task_id
                                                  for item, result in zip(runnable, results, strict=True))):
            raise CollaborationConflict('reanalysis_result_binding_mismatch')
        for item, result in zip(runnable, results, strict=True):
            node = item['node']
            status = {'completed': 'succeeded', 'partial': 'partial', 'failed': 'failed',
                      'skipped_no_data': 'skipped'}[result.status]
            coordination.finish_task(fence, node.task_id, node.task_version, attempts[node.task_id],
                                     status=status, result=result.model_dump(mode='json'))
            completed[node.task_id] = result
    for item in reanalysis:
        item['result'] = completed[item['task'].task_id]
        item['reused'] = item['task'].task_id in reused_results
    return {'nodes': [item['node'] for item in reanalysis], 'results': reanalysis, 'rejected': rejected,
            'snapshot': updated_snapshot}


def run_specialist_followup_stage(*, task_plan, task_results, task_evidence, task_nodes, node_by_task,
                                  coordination, fence, store, run_id, snapshot, evidence, config, release,
                                  patient_key, available_forms, handlers, tool_factory, select_evidence,
                                  merge_evidence, admission, check, before_model, resume, patient_reader,
                                  loaded_forms, knowledge_allowed, maximum=3, agent_access=None,
                                  graph_version=2):
    """Execute scoped specialist requests, then re-run only their requesting tasks."""
    selection = select_specialist_followups(task_plan, task_results, snapshot, task_evidence,
                                            available_forms, loaded_forms,
                                            knowledge_allowed=knowledge_allowed, maximum=maximum)
    if not selection['selected']:
        return {'selection': selection, 'nodes': [], 'reanalysis': None, 'supplement': None,
                'evidence': evidence, 'conflicts': set(), 'snapshot': snapshot}
    supplement = run_review_supplements(issues=selection['selected'], claim_nodes={}, node_by_task=node_by_task,
        task_nodes=task_nodes, review_node=None, coordination=coordination, fence=fence, store=store,
        run_id=run_id, snapshot=snapshot, config=config, release=release, patient_key=patient_key,
        available_forms=available_forms, handlers=handlers, tool_factory=tool_factory,
        select_evidence=select_evidence, admission=admission, check=check, before_model=before_model,
        resume=resume, patient_reader=patient_reader, loaded_forms=loaded_forms, maximum=maximum,
        graph_nodes=list(task_nodes), graph_version=graph_version, agent_access=agent_access)
    if not supplement['nodes']:
        return {'selection': selection, 'nodes': [], 'reanalysis': None, 'supplement': supplement,
                'evidence': evidence, 'conflicts': set(), 'snapshot': snapshot}
    merged, conflicts = merge_evidence([supplement['evidence'], evidence])
    reanalysis = run_specialist_reanalysis(supplement=supplement, task_plan=task_plan, task_results=task_results,
        task_evidence=task_evidence, task_nodes=task_nodes, review_node=None, coordination=coordination,
        fence=fence, store=store, run_id=run_id, snapshot=snapshot, config=config, patient_key=patient_key,
        available_forms=available_forms, handlers=handlers, merge_evidence=merge_evidence,
        admission=admission, check=check, before_model=before_model, resume=resume,
        graph_nodes=[*task_nodes, *supplement['nodes']], graph_version=graph_version + 1, shared_evidence=merged,
        agent_access=agent_access)
    current = reanalysis['snapshot']
    if current.version != snapshot.version:
        merged = uncertain_after_patient_change(merged)
    return {'selection': selection, 'nodes': [*supplement['nodes'], *reanalysis['nodes']],
            'reanalysis': reanalysis, 'supplement': supplement, 'evidence': merged,
            'conflicts': conflicts, 'snapshot': current}
