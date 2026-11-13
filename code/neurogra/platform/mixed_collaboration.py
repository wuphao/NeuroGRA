"""Durable parent graph for a single mixed patient-dialogue request.

The focused branch delegates to the existing specialist graph.  Other branches
stay independent so a partial result in one does not erase another.  This graph
records scopes; callers still re-authorize before executing or replaying data.
"""

import json
import time
import uuid
from contextlib import ExitStack

from neurogra.clinical.collaboration_contracts import CollaborationGraph, CollaborationTask, SubjectScope, TaskDependency
from neurogra.clinical.collaboration_store import CollaborationConflict, CollaborationStore
from neurogra.clinical.storage import BudgetExceeded, RunStore
from neurogra.clinical.utils import fingerprint

from .locking import ResourceBusy
from .model_queue import ModelQueueError
from .patient_workspace import WorkspaceError


def mixed_run_id(child_run_id):
    return fingerprint(['mixed_dialogue', child_run_id])[:32]


class MixedCollaboration:
    """Fence and checkpoint mixed branches while preserving their own adapters."""

    def __init__(self, config, child_run_id, scope, branches, *, allowed_forms, binding,
                 execution_digest, check, resume):
        self.config = config
        self.child_run_id = child_run_id
        self.run_id = mixed_run_id(child_run_id)
        self.scope = SubjectScope.model_validate(scope)
        self.branches = branches
        self.allowed_forms = set(allowed_forms)
        self.check = check
        self.resume = resume
        self.stack = ExitStack()
        self.store = RunStore(config.resolve(config.database))
        self.coordination = None
        self.fence = None
        self.terminal = False
        self.metadata = {'project_root': str(config.project_root), 'purpose': 'mixed_dialogue',
                         'budget_child_run_id': child_run_id,
                         'binding': binding, 'config_digest': fingerprint(config.model_dump(mode='json')),
                         'execution_digest': execution_digest}
        tasks = [CollaborationTask(task_id='mixed_' + key, kind=item['kind'], objective=item['question'][:1800],
                                   patient_keys=item['patient_keys'], form_keys=item.get('form_keys', []))
                 for key, item in branches.items()]
        tasks.append(CollaborationTask(task_id='mixed_synthesis', kind='answer.synthesize',
            objective='汇总已完成的混合任务并保留各项限制', patient_keys=[self.scope.primary_patient],
            depends_on=[TaskDependency(task_id=task.task_id, allow_partial=True) for task in tasks]))
        self.graph = CollaborationGraph(tasks=tasks)
        self.metadata['plan_digest'] = fingerprint(self.graph.model_dump(mode='json'))

    def __enter__(self):
        try:
            self.stack.enter_context(self.store.workflow_lock(self.run_id))
            try:
                previous = self.store.run(self.run_id)
            except ValueError:
                limits = self.config.dialogue_budget.model_dump(exclude={'mixed_seconds'})
                limits['seconds'] = self.config.dialogue_budget.mixed_seconds
                self.store.create_run(self.metadata, limits, run_id=self.run_id)
                previous = None
            if previous is not None:
                if json.loads(previous['config']) != self.metadata:
                    raise WorkspaceError('混合任务的患者、范围、配置或执行版本已变化，请重新提问。', 409)
            self.coordination = CollaborationStore(self.store)
            self.coordination.create(self.run_id, self.scope, entry_kind='chat',
                                     entry_key='mixed:' + self.child_run_id,
                                     config_digest=self.metadata['config_digest'])
            state = self.coordination.snapshot(self.run_id)['state']
            self.terminal = state in {'completed', 'partial'}
            if state in {'failed', 'cancelled'}:
                raise WorkspaceError('混合任务已结束，请重新提问。', 409)
            if not self.terminal:
                if previous is not None and previous['deadline'] <= time.time():
                    raise WorkspaceError('原混合任务已超过运行时限，请重新提问。', 409)
                if previous is not None and not self.resume:
                    raise WorkspaceError('上次混合分析已中断，请恢复该请求或重新提问。', 409)
                self.fence = self.stack.enter_context(self.coordination.owned(self.run_id, uuid.uuid4().hex))
                self.coordination.install_graph(self.fence, self.graph,
                    form_keys=self.allowed_forms, agent_types=set())
            return self
        except BaseException:
            self.stack.close()
            raise

    def __exit__(self, *args):
        return self.stack.__exit__(*args)

    def _saved(self, result_id):
        return next(row['payload'] for row in self.store.objects(self.run_id, 'collaboration_result')
                    if row['object_id'] == result_id)

    def read_patient(self, scope, read):
        """Charge a parent-owned logical read before dispatching it."""
        self.check()
        store = (self.store if self.terminal else
                 self.coordination.fenced_store(self.fence, check=self.check))
        call_id, _ = store.reserve(self.run_id, 'patient_data', scope='mixed:' + scope)
        try:
            value = read()
        except BaseException:
            # The source might have received a read even when its result is lost.
            self.check()
            store.settle(call_id, 'indeterminate')
            raise
        self.check()
        store.settle(call_id, 'completed')
        return value

    def run(self, key, calculate, *, replay=None, partial=None):
        """Execute once; replay must recheck the original scope and data."""
        task_id = 'mixed_' + key
        node = next((row for row in self.coordination.snapshot(self.run_id)['tasks'] if row['task_id'] == task_id), None)
        if node is None:
            raise CollaborationConflict('mixed_task_not_found')
        self.check()
        if node['state'] in {'succeeded', 'partial'} and node['result_id']:
            saved = self._saved(node['result_id'])
            if saved.get('error'):
                error = saved['error']
                if error['kind'] == 'busy':
                    raise ResourceBusy('mixed_branch_busy')
                raise WorkspaceError(error['message'], error['status'])
            value = saved['value']
            try:
                if replay is not None:
                    replay(value)
                else:
                    current = calculate()
                    if isinstance(current, dict) and current.get('budget_exhausted'):
                        raise BudgetExceeded('patient_data')
                    if fingerprint(current) != fingerprint(value):
                        raise WorkspaceError('混合任务的数据已变化，请重新提问。', 409)
            except BudgetExceeded:
                self.check()
                raise WorkspaceError('原混合任务的读取预算不足，无法核实已保存结果，请重新提问。', 409) from None
            self.check()
            return value
        if self.terminal:
            raise CollaborationConflict('mixed_terminal_task_missing')
        if node['state'] == 'interrupted':
            self.coordination.retry_interrupted(self.fence, task_id, 1)
        claimed = self.coordination.claim_next(self.fence, task_ids={task_id})
        if claimed is None or claimed['task']['task_id'] != task_id:
            raise CollaborationConflict('mixed_task_not_ready')
        try:
            value = calculate()
        except WorkspaceError as error:
            if error.status in {401, 403, 404, 409}:
                raise
            self.check()
            self.coordination.finish_task(self.fence, task_id, 1, claimed['attempt_id'], status='partial',
                result={'error': {'kind': 'workspace', 'message': str(error), 'status': error.status}})
            raise
        except BudgetExceeded:
            if key not in {'focused', 'comparison', 'cohort'}:
                raise
            self.check()
            message = '本次协作的预算已耗尽，相关资料或任务未能全部处理；请根据需要重新提问。'
            self.coordination.finish_task(self.fence, task_id, 1, claimed['attempt_id'], status='partial',
                result={'error': {'kind': 'budget', 'message': message, 'status': 503}})
            raise WorkspaceError(message, 503) from None
        except ResourceBusy:
            self.check()
            self.coordination.finish_task(self.fence, task_id, 1, claimed['attempt_id'], status='partial',
                                          result={'error': {'kind': 'busy'}})
            raise
        except TimeoutError:
            # Report submission can have an unknown external outcome; preserve its
            # interrupted state for reconciliation instead of settling it here.
            if key not in {'focused', 'comparison', 'cohort'}:
                raise
            self.check()
            message = '该任务处理超时，远端调用结果可能未知；请根据需要重新提问。'
            self.coordination.finish_task(self.fence, task_id, 1, claimed['attempt_id'], status='partial',
                result={'error': {'kind': 'timeout', 'message': message, 'status': 504}})
            raise WorkspaceError(message, 504) from None
        except ModelQueueError as error:
            # A lost owner or a duplicate in-flight request must still stop the run.
            if key != 'focused' or str(error) not in {'model_queue_full', 'model_queue_deadline'}:
                raise
            self.check()
            message = '该任务的模型排队已满或调用超时，部分远端调用结果可能未知；请稍后重新提问。'
            self.coordination.finish_task(self.fence, task_id, 1, claimed['attempt_id'], status='partial',
                result={'error': {'kind': 'model_queue', 'message': message, 'status': 503}})
            raise WorkspaceError(message, 503) from None
        self.check()
        status = 'partial' if partial and partial(value) else 'succeeded'
        self.coordination.finish_task(self.fence, task_id, 1, claimed['attempt_id'], status=status,
                                      result={'value': value})
        return value

    def finish(self, answer, *, partial=False):
        """Commit the result binding before the separate chat transaction."""
        self.check()
        digest = fingerprint(answer)
        if self.terminal:
            rows = self.store.objects(self.run_id, 'collaboration_final')
            if not rows or rows[-1]['payload'] != {'binding': self.metadata['binding'], 'answer_digest': digest}:
                raise WorkspaceError('混合任务结果已变化，请重新提问。', 409)
            return
        node = next(row for row in self.coordination.snapshot(self.run_id)['tasks']
                    if row['task_id'] == 'mixed_synthesis')
        if node['state'] in {'succeeded', 'partial'} and node['result_id']:
            if self._saved(node['result_id']).get('answer_digest') != digest:
                raise WorkspaceError('混合任务结果已变化，请重新提问。', 409)
        if node['state'] == 'interrupted':
            self.coordination.retry_interrupted(self.fence, 'mixed_synthesis', 1)
        if node['state'] not in {'succeeded', 'partial'}:
            claimed = self.coordination.claim_next(self.fence, task_ids={'mixed_synthesis'})
            if claimed is None:
                raise CollaborationConflict('mixed_synthesis_not_ready')
            self.coordination.finish_task(self.fence, 'mixed_synthesis', 1, claimed['attempt_id'],
                status='partial' if partial else 'succeeded', result={'answer_digest': digest})
        self.check()
        self.coordination.finish_run(self.fence, force_partial=partial,
                                     final={'binding': self.metadata['binding'], 'answer_digest': digest})
        self.terminal = True
