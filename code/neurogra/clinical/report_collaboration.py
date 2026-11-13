"""Connect the existing report loop to the durable collaboration task graph."""

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar

from .collaboration_contracts import CollaborationGraph, CollaborationTask, TaskDependency
from .collaboration_store import CollaborationConflict
from .schemas import AgentResult, TaskPlan
from .storage import BudgetExceeded
from .utils import fingerprint, identity

_current = ContextVar('report_collaboration_scheduler', default=None)
MAX_GRAPH_NODES = 64


@contextmanager
def report_collaboration(scheduler):
    token = _current.set(scheduler)
    try:
        yield
    finally:
        _current.reset(token)


def current_report_scheduler():
    return _current.get()


class ReportScheduler:
    """Persist actual report actions and specialist executions, including retries."""

    def __init__(self, coordination, fence, patient_key, agent_types, check):
        self.coordination, self.fence = coordination, fence
        self.patient_key, self.agent_types, self.check = patient_key, set(agent_types), check
        self.root_id = identity('report_patient', fence.run_id)
        self.action_targets = {}
        self.synthesis_attempt = None

    def _definitions(self):
        self.check()
        snapshot = self.coordination.snapshot(self.fence.run_id)
        if not snapshot['graph_version']:
            return snapshot, []
        with self.coordination.store.connect() as db:
            rows = db.execute('''SELECT n.definition FROM collaboration_nodes n
                JOIN collaboration_graph_nodes g ON n.run_id=g.run_id AND n.task_id=g.task_id
                    AND n.task_version=g.task_version
                WHERE g.run_id=? AND g.graph_version=? ORDER BY g.ordinal''',
                (self.fence.run_id, snapshot['graph_version'])).fetchall()
        return snapshot, [CollaborationTask.model_validate_json(row['definition']) for row in rows]

    def _append(self, nodes):
        snapshot, current = self._definitions()
        existing = {node.task_id: node for node in current}
        additions = []
        for node in nodes:
            if node.task_id in existing:
                if fingerprint(existing[node.task_id]) != fingerprint(node):
                    raise CollaborationConflict('report_task_definition_changed')
            else:
                additions.append(node)
        if additions:
            # Keep one slot for the terminal report synthesis node.
            limit = MAX_GRAPH_NODES if all(node.kind == 'answer.synthesize' for node in additions) else MAX_GRAPH_NODES - 1
            if len(current) + len(additions) > limit:
                raise BudgetExceeded('collaboration_node_limit')
            self.check()
            self.coordination.install_graph(self.fence, CollaborationGraph(
                graph_version=snapshot['graph_version'] + 1, tasks=[*current, *additions]),
                form_keys=set(), agent_types=self.agent_types)

    def _state(self):
        self.check()
        return {row['task_id']: row for row in self.coordination.snapshot(self.fence.run_id)['tasks']}

    def _claim(self, task_id, kind):
        state = self._state()[task_id]
        if state['state'] == 'interrupted':
            self.coordination.retry_interrupted(self.fence, task_id, 1)
            state = self._state()[task_id]
        if state['state'] in {'succeeded', 'partial', 'failed', 'skipped'}:
            return None
        claimed = self.coordination.claim_next(self.fence, kinds={kind}, task_ids={task_id})
        if claimed is None or claimed['task']['task_id'] != task_id:
            raise CollaborationConflict('report_task_not_ready')
        return claimed['attempt_id']

    def _result(self, task_id):
        state = self._state()[task_id]
        if not state['result_id']:
            raise CollaborationConflict('report_task_result_missing')
        rows = self.coordination.store.objects(self.fence.run_id, 'collaboration_result')
        return next(row['payload'] for row in rows if row['object_id'] == state['result_id'])

    def start(self, snapshot):
        node = CollaborationTask(task_id=self.root_id, kind='patient.read',
            objective='读取并固定本次报告的患者资料', case_version=snapshot.version,
            patient_keys=[self.patient_key])
        self._append([node])
        attempt = self._claim(self.root_id, node.kind)
        if attempt:
            self.check()
            self.coordination.finish_task(self.fence, self.root_id, 1, attempt, status='succeeded',
                result={'case_version': snapshot.version, 'snapshot_hash': snapshot.raw_input_hash})
        elif self._result(self.root_id) != {'case_version': snapshot.version, 'snapshot_hash': snapshot.raw_input_hash}:
            raise CollaborationConflict('report_patient_snapshot_changed')

    def action(self, round_id, index, request, targets, state, callback):
        action_id = identity('report_action', [round_id, index, request.model_dump(mode='json'), targets])
        kind = {'patient_data': 'patient.read', 'knowledge': 'knowledge.search',
                'source_context': 'knowledge.search', 'imaging_tool': 'imaging.predict'}[request.type]
        known = self._state()
        prior = [row['task_id'] for row in state['tasks'].values() if row['agent_type'] in targets and
                 row['task_id'] in known and known[row['task_id']]['state'] in {'succeeded', 'partial'}]
        deps = [self.root_id, *prior]
        node = CollaborationTask(task_id=action_id, kind=kind, objective=request.question,
            patient_keys=[self.patient_key], input_refs=list(dict.fromkeys(request.source_refs)),
            depends_on=[TaskDependency(task_id=ref, allow_partial=True) for ref in dict.fromkeys(deps)])
        self._append([node])
        attempt = self._claim(action_id, kind)
        if attempt is None:
            saved = self._result(action_id)
            action_key = saved.get('action_id')
            if not action_key or not any(row['object_id'] == action_key for row in
                                         self.coordination.store.objects(self.fence.run_id, 'main_tool_result')):
                raise CollaborationConflict('report_tool_receipt_missing')
            # The tool validates the saved source checksum before returning its receipt.
            result = callback()
            if fingerprint(result) != fingerprint(saved):
                raise CollaborationConflict('report_tool_receipt_changed')
        else:
            self.check()
            result = callback()
            self.check()
            status = 'succeeded' if result.get('status') in {'found', 'completed'} else 'partial'
            self.coordination.finish_task(self.fence, action_id, 1, attempt, status=status, result=result)
        self.action_targets[action_id] = set(targets)
        return result

    def specialists(self, tasks, snapshot, inputs, executor, task_slots, state):
        known = self._state()
        nodes = []
        for task in tasks:
            actions = [key for key, roles in self.action_targets.items() if task.agent_type in roles]
            previous = state['tasks'].get(task_slots[task.task_id], {}).get('task_id')
            if previous and previous in known and known[previous]['state'] in {'succeeded', 'partial'}:
                actions.append(previous)
            nodes.append(CollaborationTask(task_id=task.task_id, kind='specialist.analyze',
                agent_type=task.agent_type, objective='；'.join(task.questions)[:1800],
                case_version=task.case_version, patient_keys=[self.patient_key],
                input_refs=list(dict.fromkeys(task.input_refs + task.background_refs)),
                budget=task.budget, depends_on=[TaskDependency(task_id=ref, allow_partial=True)
                    for ref in dict.fromkeys([self.root_id, *actions])]))
        self._append(nodes)
        existing = self._state()
        for node in nodes:
            if existing[node.task_id]['state'] == 'interrupted':
                self.coordination.retry_interrupted(self.fence, node.task_id, 1)
        results = {node.task_id: AgentResult.model_validate(self._result(node.task_id)) for node in nodes
                   if existing[node.task_id]['state'] in {'succeeded', 'partial', 'failed', 'skipped'}
                   and existing[node.task_id]['result_id']}
        completed = [task for task in tasks if task.task_id in results]
        if completed:
            # Reuse still runs the executor's task-input binding check; it does not invoke agents.
            checked = asyncio.run(executor.execute_tasks(TaskPlan(tasks=completed), snapshot, inputs))
            if len(checked) != len(completed) or any(
                    fingerprint(result) != fingerprint(results[task.task_id])
                    for task, result in zip(completed, checked, strict=True)):
                raise CollaborationConflict('report_specialist_receipt_changed')
        attempts = {}
        while len(attempts) + len(results) < len(nodes):
            self.check()
            claimed = self.coordination.claim_next(self.fence, kinds={'specialist.analyze'},
                                                   task_ids={node.task_id for node in nodes})
            if claimed is None or claimed['task']['task_id'] not in {node.task_id for node in nodes}:
                raise CollaborationConflict('report_specialist_not_ready')
            attempts[claimed['task']['task_id']] = claimed['attempt_id']
        runnable = [task for task in tasks if task.task_id in attempts]
        if runnable:
            fresh = asyncio.run(executor.execute_tasks(TaskPlan(tasks=runnable), snapshot, inputs))
            if len(fresh) != len(runnable) or any(result.task_id != task.task_id
                                                  for task, result in zip(runnable, fresh, strict=True)):
                raise CollaborationConflict('report_specialist_result_mismatch')
            for task, result in zip(runnable, fresh, strict=True):
                self.check()
                status = {'completed': 'succeeded', 'partial': 'partial', 'failed': 'failed',
                          'skipped_no_data': 'skipped'}[result.status]
                self.coordination.finish_task(self.fence, task.task_id, 1, attempts[task.task_id],
                                              status=status, result=result.model_dump(mode='json'))
                results[task.task_id] = result
        self.action_targets.clear()
        return [results[task.task_id] for task in tasks]

    def begin_synthesis(self):
        states = self._state()
        dependencies = [key for key, row in states.items()
                        if key != 'report_synthesis' and row['state'] in {'succeeded', 'partial'}]
        node = CollaborationTask(task_id='report_synthesis', kind='answer.synthesize',
            objective='综合已完成的报告任务并保留未解决事项', patient_keys=[self.patient_key],
            depends_on=[TaskDependency(task_id=key, allow_partial=True) for key in dependencies])
        self._append([node])
        self.synthesis_attempt = self._claim(node.task_id, node.kind)

    def finish_synthesis(self, result):
        summary = {'run_id': result.run_id, 'status': result.status, 'report_paths': result.report_paths}
        if self.synthesis_attempt:
            self.check()
            self.coordination.finish_task(self.fence, 'report_synthesis', 1, self.synthesis_attempt,
                status='succeeded' if result.status == 'completed' else 'partial',
                result=summary)
        elif self._result('report_synthesis') != summary:
            raise CollaborationConflict('report_synthesis_receipt_changed')
        self.check()
        self.coordination.finish_run(self.fence, force_partial=result.status != 'completed')

    def has_partial_results(self):
        return any(row['state'] in {'partial', 'failed', 'skipped', 'cancelled', 'interrupted'}
                   for key, row in self._state().items() if key != 'report_synthesis')
