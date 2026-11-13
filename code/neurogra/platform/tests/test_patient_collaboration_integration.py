import asyncio
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from neurogra.clinical.collaboration_store import CollaborationStore
from neurogra.clinical.schemas import AgentResult, Claim
from neurogra.clinical.storage import RunStore
from neurogra.platform.application import Application
from neurogra.platform.async_chat import AsyncChat
from neurogra.platform.configuration import resolve_config
from neurogra.platform.mixed_collaboration import MixedCollaboration, mixed_run_id
from neurogra.platform.model_queue import ModelQueueError
from neurogra.platform.patient_agents import DialoguePlan, DialogueSpecialistTask
from neurogra.platform.patient_analysis import PatientAnalysis
from neurogra.platform.patient_chat import PatientChat, request_fingerprint
from neurogra.platform.patient_workspace import PatientWorkspace, WorkspaceError
from neurogra.platform.tests.test_patient_analysis import Reader


class PatientCollaborationIntegrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.app = Application(self.root, resolve_config(root=self.root, profile='kb-only'))
        self.workspace = PatientWorkspace(self.app.config, reader_factory=lambda *args: Reader())
        with patch('neurogra.platform.patient_workspace.obtain_token', return_value='private-token'):
            self.cookie = self.workspace.login('synthetic-account', 'private-password')
        self.session = self.workspace.session(self.cookie)
        self.chat = PatientChat(self.app, self.workspace,
            PatientAnalysis(self.app, self.workspace, starter=lambda _: None))
        self.sid = self.chat.create(self.session, '1')['id']
        self.body = {'message': '解释这个患者的 MoCA 结果', 'request_id': 'request-one', 'capabilities': []}

    @staticmethod
    async def specialist(task_plan, snapshot, **kwargs):
        task = task_plan.tasks[0]
        observation = next(item for item in snapshot.observations if item.name == '总分')
        return [AgentResult(result_id='result', task_id=task.task_id, case_version=snapshot.version,
            status='completed', claims=[Claim(claim_id='fact', text='总分 23', kind='description',
                level='cognition', observation_ids=[observation.observation_id], strength='descriptive')])]

    def test_crash_before_chat_commit_reuses_saved_run_once(self):
        plan = DialoguePlan(patient_specific=True, forms=['moca'], roles=['cognition'], knowledge=False)
        with patch('neurogra.platform.patient_agents.ModelGateway.generate_structured', return_value=plan) as planner, \
             patch('neurogra.platform.patient_agents.Executor.execute_tasks', new_callable=AsyncMock,
                   side_effect=self.specialist) as executor:
            with patch.object(self.chat.store, 'transaction', side_effect=RuntimeError('simulated_chat_crash')):
                with self.assertRaisesRegex(RuntimeError, 'simulated_chat_crash'):
                    self.chat.send(self.session, '1', self.sid, self.body, Mock())
            binding = self.chat.store.database().collaboration_binding(self.sid, 'request-one')
            clinical = RunStore(self.app.configuration('standard').resolve(self.app.configuration('standard').database))
            self.assertTrue(clinical.objects(binding['run_id'], 'collaboration_final'))
            response = self.chat.send(self.session, '1', self.sid, self.body, Mock())
        self.assertIn('23', response['reply']['text'])
        self.assertEqual(planner.call_count, 1)
        self.assertEqual(executor.call_count, 1)
        messages = self.chat.store.database().get_messages(self.sid)
        self.assertEqual(sum(item['role'] == 'assistant' for item in messages), 1)

    def test_saved_result_is_not_replayed_after_access_is_revoked(self):
        plan = DialoguePlan(patient_specific=True, forms=['moca'], roles=['cognition'], knowledge=False)
        with patch('neurogra.platform.patient_agents.ModelGateway.generate_structured', return_value=plan), \
             patch('neurogra.platform.patient_agents.Executor.execute_tasks', new_callable=AsyncMock,
                   side_effect=self.specialist):
            with patch.object(self.chat.store, 'transaction', side_effect=RuntimeError('simulated_chat_crash')):
                with self.assertRaises(RuntimeError):
                    self.chat.send(self.session, '1', self.sid, self.body, Mock())
        with patch.object(self.workspace, 'authorize_forms', side_effect=WorkspaceError('无权访问', 403)):
            with self.assertRaises(WorkspaceError) as error:
                self.chat.send(self.session, '1', self.sid, self.body, Mock())
        self.assertEqual(error.exception.status, 403)

    def test_patient_form_access_is_rechecked_after_analysis_before_chat_commit(self):
        revoked = False
        authorize = self.workspace.authorize_forms

        def guarded(session, patient_id):
            if revoked:
                raise WorkspaceError('患者表单权限已撤销。', 403)
            return authorize(session, patient_id)

        def analyze(*_args, **_kwargs):
            nonlocal revoked
            revoked = True
            return {'text': '合成患者结果', 'sources': [{'id': 'synthetic-source'}],
                    'collaboration': [], 'result_status': 'completed'}

        with patch.object(self.workspace, 'authorize_forms', side_effect=guarded), \
             patch('neurogra.platform.patient_agents.answer', side_effect=analyze):
            with self.assertRaises(WorkspaceError) as error:
                self.chat.send(self.session, '1', self.sid, self.body, Mock())
        self.assertEqual(error.exception.status, 403)
        self.assertEqual(self.chat.store.database().get_messages(self.sid), [])

    def test_focused_analysis_wording_invokes_specialist_without_creating_report(self):
        plan = DialoguePlan(patient_specific=True, forms=['moca'], roles=['cognition'], knowledge=False)
        with patch('neurogra.platform.patient_agents.ModelGateway.generate_structured', return_value=plan), \
             patch('neurogra.platform.patient_agents.Executor.execute_tasks', new_callable=AsyncMock,
                   side_effect=self.specialist) as executor, \
             patch.object(self.chat.analyses, 'submit') as submit:
            for index, text in enumerate(('分析这个患者的 MoCA 结果', '评估患者的 MoCA 结果',
                                          '解读患者的 MoCA 结果')):
                with self.subTest(text=text):
                    body = {'message': text, 'request_id': f'focused-{index}', 'capabilities': []}
                    reply = self.chat.send(self.session, '1', self.sid, body, Mock())['reply']
                    self.assertIn('23', reply['text'])
                    self.assertTrue(reply['collaboration'])
            self.assertEqual(executor.call_count, 3)
            submit.assert_not_called()

    def test_ambiguous_patient_analysis_asks_scope_without_creating_report(self):
        with patch('neurogra.platform.patient_agents.answer') as focused, \
             patch.object(self.chat.analyses, 'submit') as submit:
            body = {'message': '帮我分析一下这个患者', 'request_id': 'ambiguous', 'capabilities': []}
            reply = self.chat.send(self.session, '1', self.sid, body, Mock())['reply']
        self.assertIn('具体问题', reply['text'])
        self.assertIn('完整的患者分析报告', reply['text'])
        self.assertEqual(reply['result_status'], 'partial')
        focused.assert_not_called()
        submit.assert_not_called()

    def test_async_resume_is_authorized_idempotent_and_reuses_committed_task(self):
        manager = AsyncChat(self.chat, self.workspace)
        plan = DialoguePlan(patient_specific=True, forms=['moca'], knowledge=False, tasks=[
            DialogueSpecialistTask(role='cognition', question='核对量表', forms=['moca']),
            DialogueSpecialistTask(role='cognition', question='说明局限', forms=['moca'])])
        body = {**self.body, 'capabilities': ['async_turns_v1']}
        calls, finishes = [], []
        resume_started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        async def execute(task_plan, snapshot, **kwargs):
            calls.append([task.questions[0] for task in task_plan.tasks])
            if len(calls) > 1:
                resume_started.set()
                await asyncio.to_thread(release.wait, 5)
            observation = next(item for item in snapshot.observations if item.name == '总分')
            return [AgentResult(result_id='result_' + task.task_id, task_id=task.task_id,
                case_version=snapshot.version, status='completed', claims=[
                    Claim(claim_id='fact_' + task.task_id, text='总分 23', kind='description', level='cognition',
                          observation_ids=[observation.observation_id], strength='descriptive')])
                    for task in task_plan.tasks]

        finish = CollaborationStore.finish_task

        def interrupt_once(store, *args, **kwargs):
            finishes.append(args[1])
            if len(finishes) == 2:
                raise RuntimeError('simulated_interruption')
            return finish(store, *args, **kwargs)

        def wait_for(*states):
            deadline = time.time() + 8
            while time.time() < deadline:
                row = self.chat.store.database().turn(self.sid, 'request-one')
                if row and row['state'] in states:
                    return row
                time.sleep(.05)
            self.fail('对话状态未在时限内到达预期值')

        with patch('neurogra.platform.patient_agents.ModelGateway.generate_structured', return_value=plan) as planner, \
             patch('neurogra.platform.patient_agents.Executor.execute_tasks', new_callable=AsyncMock,
                   side_effect=execute), \
             patch.object(CollaborationStore, 'finish_task', autospec=True, side_effect=interrupt_once):
            manager.start(self.cookie, self.session, '1', self.sid, body)
            failed = wait_for('failed')
            self.assertTrue(failed['resumable'])
            public = self.chat.messages(self.session, '1', self.sid)['messages']
            self.assertTrue(public[-1]['turn']['resumable'])
            with patch.object(self.workspace, 'authorize_forms', side_effect=WorkspaceError('无权访问', 403)):
                with self.assertRaises(WorkspaceError):
                    manager.resume(self.cookie, self.session, '1', self.sid, 'request-one')
            self.assertEqual(self.chat.store.database().turn(self.sid, 'request-one')['state'], 'failed')
            first = manager.resume(self.cookie, self.session, '1', self.sid, 'request-one')
            self.assertIn(first['turn']['status'], {'accepted', 'running', 'waiting_model'})
            self.assertTrue(resume_started.wait(5))
            second = manager.resume(self.cookie, self.session, '1', self.sid, 'request-one')
            self.assertIn(second['turn']['status'], {'accepted', 'running', 'waiting_model'})
            release.set()
            wait_for('completed')
            final = manager.get(self.session, '1', self.sid, 'request-one')
        self.assertIn('23', final['reply']['text'])
        self.assertEqual(calls, [['核对量表', '说明局限'], ['说明局限']])
        self.assertEqual(planner.call_count, 1)
        messages = self.chat.store.database().get_messages(self.sid)
        self.assertEqual(sum(item['role'] == 'user' for item in messages), 1)
        self.assertEqual(sum(item['role'] == 'assistant' for item in messages), 1)

    def test_mixed_focused_analysis_and_report_are_both_processed(self):
        plan = DialoguePlan(patient_specific=True, forms=['moca'], roles=['cognition'], knowledge=False)
        body = {'message': '解释这个患者的 MoCA 结果，并生成完整分析报告', 'request_id': 'mixed-report',
                'capabilities': ['interactions_v1']}
        job = {'job_id': 'synthetic-job', 'status': 'queued'}
        with patch('neurogra.platform.patient_agents.ModelGateway.generate_structured', return_value=plan), \
             patch('neurogra.platform.patient_agents.Executor.execute_tasks', new_callable=AsyncMock,
                   side_effect=self.specialist) as executor, \
             patch.object(self.chat.analyses, 'report_status', return_value={'status': 'missing'}), \
             patch.object(self.chat.analyses, 'submit', return_value=job) as submit:
            reply = self.chat.send(self.session, '1', self.sid, body, Mock())['reply']
            repeated = self.chat.send(self.session, '1', self.sid, body, Mock())['reply']
        self.assertIn('局部分析', reply['text'])
        self.assertIn('23', reply['text'])
        self.assertIn('完整报告', reply['text'])
        self.assertEqual(reply['analysis_job_id'], 'synthetic-job')
        self.assertEqual(reply['result_status'], 'partial')  # report is still queued
        binding = self.chat.store.database().collaboration_binding(self.sid, 'mixed-report')
        clinical = RunStore(self.app.config.resolve(self.app.config.database))
        parent = clinical.summary(mixed_run_id(binding['run_id']))
        self.assertIn({'kind': 'patient_data', 'status': 'completed', 'n': 1}, parent['aggregate_calls'])
        self.assertEqual(repeated['analysis_job_id'], 'synthetic-job')
        self.assertEqual(executor.call_count, 1)
        self.assertEqual(submit.call_count, 1)

    def test_configured_budget_applies_to_both_mixed_parent_and_focused_child(self):
        self.app.config = resolve_config(root=self.root, profile='kb-only', overrides={'dialogue_budget': {
            'llm': 9, 'retrieval': 4, 'patient_data': 2, 'embedding': 7, 'seconds': 70, 'mixed_seconds': 100}})
        plan = DialoguePlan(patient_specific=True, forms=['moca'], roles=['cognition'], knowledge=False)
        body = {'message': '解释这个患者的 MoCA 结果，并生成完整分析报告', 'request_id': 'configured-mixed',
                'capabilities': ['interactions_v1']}
        original_create = RunStore.create_run
        created = {}

        def record_creation(instance, config, budget, *, run_id=None):
            before = time.time()
            identifier = original_create(instance, config, budget, run_id=run_id)
            created[identifier] = (before, time.time())
            return identifier

        with patch.object(RunStore, 'create_run', new=record_creation), \
             patch('neurogra.platform.patient_agents.ModelGateway.generate_structured', return_value=plan), \
             patch('neurogra.platform.patient_agents.Executor.execute_tasks', new_callable=AsyncMock,
                   side_effect=self.specialist), \
             patch.object(self.chat.analyses, 'report_status', return_value={'status': 'missing'}), \
             patch.object(self.chat.analyses, 'submit', return_value={'job_id': 'synthetic', 'status': 'queued'}):
            self.chat.send(self.session, '1', self.sid, body, Mock())
        binding = self.chat.store.database().collaboration_binding(self.sid, body['request_id'])
        engine = RunStore(self.app.config.resolve(self.app.config.database))
        child, parent = engine.run(binding['run_id']), engine.run(mixed_run_id(binding['run_id']))
        for run in (child, parent):
            limits = json.loads(run['limits'])
            self.assertEqual([limits[key] for key in ('llm', 'retrieval', 'patient_data', 'embedding')], [9, 4, 2, 7])
        for run, seconds in ((child, 70), (parent, 100)):
            before, after = created[run['run_id']]
            self.assertGreaterEqual(run['deadline'], before + seconds)
            self.assertLessEqual(run['deadline'], after + seconds)

    def test_mixed_task_stops_if_budget_changes_during_specialist_execution(self):
        plan = DialoguePlan(patient_specific=True, forms=['moca'], roles=['cognition'], knowledge=False)
        body = {'message': '解释这个患者的 MoCA 结果，并生成完整分析报告', 'request_id': 'budget-during-task',
                'capabilities': ['interactions_v1']}

        async def change_budget(task_plan, snapshot, **kwargs):
            budget = self.app.config.dialogue_budget.model_copy(update={'patient_data': 2})
            self.app.config = self.app.config.model_copy(update={'dialogue_budget': budget})
            return await self.specialist(task_plan, snapshot, **kwargs)

        with patch('neurogra.platform.patient_agents.ModelGateway.generate_structured', return_value=plan), \
             patch('neurogra.platform.patient_agents.Executor.execute_tasks', new_callable=AsyncMock,
                   side_effect=change_budget), \
             patch.object(self.chat.analyses, 'submit') as submit:
            with self.assertRaisesRegex(WorkspaceError, '配置.*变化') as error:
                self.chat.send(self.session, '1', self.sid, body, Mock())
        self.assertEqual(error.exception.status, 409)
        submit.assert_not_called()
        self.assertFalse(any(row['role'] == 'assistant' for row in self.chat.store.database().get_messages(self.sid)))

    def test_child_cannot_commit_after_parent_ownership_expires(self):
        plan = DialoguePlan(patient_specific=True, forms=['moca'], roles=['cognition'], knowledge=False)
        body = {'message': '解释这个患者的 MoCA 结果，并生成完整分析报告', 'request_id': 'parent-expired',
                'capabilities': ['interactions_v1']}

        async def expire_parent(task_plan, snapshot, **kwargs):
            binding = self.chat.store.database().collaboration_binding(self.sid, body['request_id'])
            engine = RunStore(self.app.config.resolve(self.app.config.database))
            with engine.connect() as db:
                db.execute('UPDATE collaboration_runs SET lease_until=0 WHERE run_id=?',
                           (mixed_run_id(binding['run_id']),))
            return await self.specialist(task_plan, snapshot, **kwargs)

        with patch('neurogra.platform.patient_agents.ModelGateway.generate_structured', return_value=plan), \
             patch('neurogra.platform.patient_agents.Executor.execute_tasks', new_callable=AsyncMock,
                   side_effect=expire_parent), \
             patch.object(self.chat.analyses, 'submit') as submit:
            with self.assertRaises(WorkspaceError) as error:
                self.chat.send(self.session, '1', self.sid, body, Mock())
        self.assertEqual(error.exception.status, 409)
        binding = self.chat.store.database().collaboration_binding(self.sid, body['request_id'])
        engine = RunStore(self.app.config.resolve(self.app.config.database))
        child_graph = CollaborationStore(engine).snapshot(binding['run_id'])
        self.assertFalse(any(node['result_id'] for node in child_graph['tasks']))
        self.assertFalse(engine.objects(binding['run_id'], 'collaboration_final'))
        self.assertFalse(any(row['role'] == 'assistant' for row in self.chat.store.database().get_messages(self.sid)))
        submit.assert_not_called()
        with patch('neurogra.platform.patient_agents.ModelGateway.generate_structured', return_value=plan), \
             patch('neurogra.platform.patient_agents.Executor.execute_tasks', new_callable=AsyncMock,
                   side_effect=self.specialist), \
             patch.object(self.chat.analyses, 'report_status', return_value={'status': 'missing'}), \
             patch.object(self.chat.analyses, 'submit', return_value={'job_id': 'recovered-job', 'status': 'queued'}) as submit:
            reply = self.chat.send(self.session, '1', self.sid, body, Mock(), resume_collaboration=True)['reply']
        self.assertIn('23', reply['text'])
        self.assertEqual(reply['analysis_job_id'], 'recovered-job')
        submit.assert_called_once()
        self.assertEqual(sum(row['role'] == 'assistant' for row in self.chat.store.database().get_messages(self.sid)), 1)

    def test_child_preparation_cannot_settle_read_after_parent_ownership_expires(self):
        plan = DialoguePlan(patient_specific=True, forms=['moca'], roles=['cognition'], knowledge=False)
        body = {'message': '解释这个患者的 MoCA 结果，并生成完整分析报告', 'request_id': 'parent-expired-read',
                'capabilities': ['interactions_v1']}
        original_form = self.workspace.form

        def expire_during_read(*args, **kwargs):
            result = original_form(*args, **kwargs)
            binding = self.chat.store.database().collaboration_binding(self.sid, body['request_id'])
            engine = RunStore(self.app.config.resolve(self.app.config.database))
            with engine.connect() as db:
                db.execute('UPDATE collaboration_runs SET lease_until=0 WHERE run_id=?',
                           (mixed_run_id(binding['run_id']),))
            return result

        with patch('neurogra.platform.patient_agents.ModelGateway.generate_structured', return_value=plan), \
             patch.object(self.workspace, 'form', side_effect=expire_during_read), \
             patch('neurogra.platform.patient_agents.Executor.execute_tasks', new_callable=AsyncMock) as executor:
            with self.assertRaises(WorkspaceError) as error:
                self.chat.send(self.session, '1', self.sid, body, Mock())
        self.assertEqual(error.exception.status, 409)
        binding = self.chat.store.database().collaboration_binding(self.sid, body['request_id'])
        engine = RunStore(self.app.config.resolve(self.app.config.database))
        self.assertFalse(engine.objects(binding['run_id'], 'dialogue_preparation'))
        calls = engine.summary(binding['run_id'])['calls']
        self.assertFalse(any(row['kind'] == 'patient_data' and row['status'] == 'completed' for row in calls))
        executor.assert_not_called()
        with patch('neurogra.platform.patient_agents.ModelGateway.generate_structured', return_value=plan), \
             patch('neurogra.platform.patient_agents.Executor.execute_tasks', new_callable=AsyncMock,
                   side_effect=self.specialist), \
             patch.object(self.chat.analyses, 'report_status', return_value={'status': 'missing'}), \
             patch.object(self.chat.analyses, 'submit', return_value={'job_id': 'recovered-read', 'status': 'queued'}):
            reply = self.chat.send(self.session, '1', self.sid, body, Mock(), resume_collaboration=True)['reply']
        self.assertIn('23', reply['text'])
        self.assertIn({'kind': 'patient_data', 'status': 'indeterminate', 'n': 1},
                      engine.summary(binding['run_id'])['calls'])

    def test_late_planner_response_cannot_commit_after_parent_expiry(self):
        plan = DialoguePlan(patient_specific=True, forms=['moca'], roles=['cognition'], knowledge=False)
        body = {'message': '解释这个患者的 MoCA 结果，并生成完整分析报告', 'request_id': 'parent-expired-plan',
                'capabilities': ['interactions_v1']}

        def expire_during_model(_client, _url, _payload, _timeout):
            binding = self.chat.store.database().collaboration_binding(self.sid, body['request_id'])
            engine = RunStore(self.app.config.resolve(self.app.config.database))
            with engine.connect() as db:
                db.execute('UPDATE collaboration_runs SET lease_until=0 WHERE run_id=?',
                           (mixed_run_id(binding['run_id']),))
            return {'message': {'content': plan.model_dump_json()}}

        with patch('neurogra.clinical.generation.GenerationClient.request', autospec=True,
                   side_effect=expire_during_model) as request, \
             patch.object(self.chat.analyses, 'submit') as submit:
            with self.assertRaises(WorkspaceError) as error:
                self.chat.send(self.session, '1', self.sid, body, Mock())
        self.assertEqual(error.exception.status, 409)
        request.assert_called_once()
        submit.assert_not_called()
        binding = self.chat.store.database().collaboration_binding(self.sid, body['request_id'])
        engine = RunStore(self.app.config.resolve(self.app.config.database))
        self.assertFalse(engine.objects(binding['run_id'], 'dialogue_plan_checkpoint'))
        self.assertIn({'kind': 'llm', 'status': 'running', 'n': 1}, engine.summary(binding['run_id'])['calls'])
        with patch('neurogra.platform.patient_agents.ModelGateway.generate_structured', return_value=plan), \
             patch('neurogra.platform.patient_agents.Executor.execute_tasks', new_callable=AsyncMock,
                   side_effect=self.specialist), \
             patch.object(self.chat.analyses, 'report_status', return_value={'status': 'missing'}), \
             patch.object(self.chat.analyses, 'submit', return_value={'job_id': 'recovered-plan', 'status': 'queued'}):
            reply = self.chat.send(self.session, '1', self.sid, body, Mock(), resume_collaboration=True)['reply']
        self.assertIn('23', reply['text'])
        self.assertIn({'kind': 'llm', 'status': 'indeterminate', 'n': 1}, engine.summary(binding['run_id'])['calls'])

    def test_mixed_report_confirmation_does_not_block_focused_analysis(self):
        plan = DialoguePlan(patient_specific=True, forms=['moca'], roles=['cognition'], knowledge=False)
        body = {'message': '解释这个患者的 MoCA 结果，并生成完整分析报告', 'request_id': 'mixed-confirm',
                'capabilities': ['interactions_v1']}
        status = {'status': 'changed', 'job': {'job_id': 'previous'}, 'input_fingerprint': 'synthetic-hash'}
        with patch('neurogra.platform.patient_agents.ModelGateway.generate_structured', return_value=plan), \
             patch('neurogra.platform.patient_agents.Executor.execute_tasks', new_callable=AsyncMock,
                   side_effect=self.specialist), \
             patch.object(self.chat.analyses, 'report_status', return_value=status), \
             patch.object(self.chat.analyses, 'submit') as submit:
            reply = self.chat.send(self.session, '1', self.sid, body, Mock())['reply']
        self.assertIn('23', reply['text'])
        self.assertEqual(reply['interaction']['type'], 'confirm_report')
        self.assertEqual(reply['result_status'], 'partial')
        submit.assert_not_called()

    def test_report_branch_continues_when_focused_model_is_unavailable(self):
        body = {'message': '解释这个患者的 MoCA 结果，并生成完整分析报告', 'request_id': 'mixed-partial',
                'capabilities': ['interactions_v1']}
        with patch('neurogra.platform.patient_agents.answer', side_effect=WorkspaceError('模型不可用。', 503)), \
             patch.object(self.chat.analyses, 'report_status', return_value={'status': 'missing'}), \
             patch.object(self.chat.analyses, 'submit', return_value={'job_id': 'job', 'status': 'queued'}):
            reply = self.chat.send(self.session, '1', self.sid, body, Mock())['reply']
        self.assertIn('局部分析未完成', reply['text'])
        self.assertEqual(reply['analysis_job_id'], 'job')
        self.assertEqual(reply['result_status'], 'partial')

    def test_focused_timeout_keeps_report_and_reuses_partial_after_chat_interruption(self):
        body = {'message': '解释这个患者的 MoCA 结果，并生成完整分析报告', 'request_id': 'mixed-timeout',
                'capabilities': ['interactions_v1']}
        job = {'job_id': 'synthetic-timeout-job', 'status': 'queued'}
        with patch('neurogra.platform.patient_agents.answer', side_effect=TimeoutError('private timeout detail')) as focused, \
             patch.object(self.chat.analyses, 'report_status', return_value={'status': 'missing'}), \
             patch.object(self.chat.analyses, 'submit', return_value=job) as submit, \
             patch.object(self.chat.analyses, 'job', return_value=job) as lookup:
            with patch.object(self.chat.store, 'transaction', side_effect=RuntimeError('chat_commit_interruption')):
                with self.assertRaisesRegex(RuntimeError, 'chat_commit_interruption'):
                    self.chat.send(self.session, '1', self.sid, body, Mock())
            reply = self.chat.send(self.session, '1', self.sid, body, Mock(), resume_collaboration=True)['reply']
        self.assertIn('局部分析未完成', reply['text'])
        self.assertIn('超时', reply['text'])
        self.assertNotIn('private timeout detail', reply['text'])
        self.assertEqual(reply['analysis_job_id'], 'synthetic-timeout-job')
        self.assertEqual(reply['result_status'], 'partial')
        focused.assert_called_once()
        submit.assert_called_once()
        lookup.assert_called_once()
        self.assertEqual(sum(item['role'] == 'assistant' for item in self.chat.store.database().get_messages(self.sid)), 1)

    def test_report_branch_continues_when_focused_model_queue_is_full(self):
        body = {'message': '解释这个患者的 MoCA 结果，并生成完整分析报告', 'request_id': 'mixed-queue-full',
                'capabilities': ['interactions_v1']}
        with patch('neurogra.platform.patient_agents.answer', side_effect=ModelQueueError('model_queue_full')), \
             patch.object(self.chat.analyses, 'report_status', return_value={'status': 'missing'}), \
             patch.object(self.chat.analyses, 'submit', return_value={'job_id': 'queue-job', 'status': 'queued'}):
            reply = self.chat.send(self.session, '1', self.sid, body, Mock())['reply']
        self.assertIn('局部分析未完成', reply['text'])
        self.assertIn('模型排队', reply['text'])
        self.assertEqual(reply['analysis_job_id'], 'queue-job')
        self.assertEqual(reply['result_status'], 'partial')

    def test_mixed_resume_rejects_changed_completed_child_then_continues_once(self):
        plan = DialoguePlan(patient_specific=True, forms=['moca'], roles=['cognition'], knowledge=False)
        body = {'message': '解释这个患者的 MoCA 结果，并生成完整分析报告', 'request_id': 'mixed-source-change',
                'capabilities': ['interactions_v1']}
        digest = request_fingerprint(body['message'], None, [])
        self.chat.store.database().begin_turn(self.sid, body['request_id'], digest, '1', 'owner', body['message'])
        original = MixedCollaboration.run
        stopped = False

        def interrupt_before_report(run, key, *args, **kwargs):
            nonlocal stopped
            if key == 'report' and not stopped:
                stopped = True
                raise RuntimeError('synthetic_interruption')
            return original(run, key, *args, **kwargs)

        with patch('neurogra.platform.patient_agents.ModelGateway.generate_structured', return_value=plan) as planner, \
             patch('neurogra.platform.patient_agents.Executor.execute_tasks', new_callable=AsyncMock,
                   side_effect=self.specialist) as specialist, \
             patch.object(self.chat.analyses, 'report_status', return_value={'status': 'missing'}), \
             patch.object(self.chat.analyses, 'submit', return_value={'job_id': 'synthetic-job', 'status': 'queued'}) as submit, \
             patch.object(MixedCollaboration, 'run', autospec=True, side_effect=interrupt_before_report):
            with self.assertRaisesRegex(RuntimeError, 'synthetic_interruption'):
                self.chat.send(self.session, '1', self.sid, body, Mock())
            progress = AsyncChat(self.chat, self.workspace).collaboration_progress(
                self.session, '1', self.sid, body['request_id'])
            self.assertEqual(progress['tasks'][0]['kind'], 'dialogue.delegate')
            self.assertTrue(any(item['task_id'].startswith('child_') for item in progress['tasks']))
            manager = AsyncChat(self.chat, self.workspace)
            cursor, events = 0, []
            for _ in range(100):
                page = manager.collaboration_progress(self.session, '1', self.sid, body['request_id'],
                                                      after=cursor, limit=2)
                events.extend(page['events'])
                cursor = page['next_cursor']
                if not page['has_more']:
                    break
            else:
                self.fail('progress cursor did not terminate')
            sequences = [event['event_seq'] for event in events]
            self.assertEqual(sequences, sorted(set(sequences)))
            self.assertEqual(sequences, [event['event_seq'] for event in progress['events']])
            self.assertTrue(any(event['payload'].get('task_id', '').startswith('child_') for event in events))
            self.assertTrue(any(event['payload'].get('task_id', '').startswith('mixed_') for event in events))
            self.assertNotIn('总分', str(events))
            with patch.object(Reader, 'score', 24):
                with self.assertRaises(WorkspaceError) as changed:
                    self.chat.send(self.session, '1', self.sid, body, Mock(), resume_collaboration=True)
                self.assertEqual(changed.exception.status, 409)
                self.assertIn('资料已变化', str(changed.exception))
            reply = self.chat.send(self.session, '1', self.sid, body, Mock(), resume_collaboration=True)['reply']
        self.assertIn('23', reply['text'])
        self.assertEqual(reply['analysis_job_id'], 'synthetic-job')
        self.assertEqual(planner.call_count, 1)
        self.assertEqual(specialist.call_count, 1)
        self.assertEqual(submit.call_count, 1)
        binding = self.chat.store.database().collaboration_binding(self.sid, body['request_id'])
        engine = RunStore(self.app.config.resolve(self.app.config.database))
        graph = CollaborationStore(engine).snapshot(mixed_run_id(binding['run_id']))
        self.assertEqual([task['kind'] for task in graph['tasks']],
                         ['dialogue.delegate', 'report.request', 'answer.synthesize'])
        self.assertEqual(graph['state'], 'partial')

    def test_mixed_report_is_not_resubmitted_after_chat_commit_interruption(self):
        plan = DialoguePlan(patient_specific=True, forms=['moca'], roles=['cognition'], knowledge=False)
        body = {'message': '解释这个患者的 MoCA 结果，并生成完整分析报告', 'request_id': 'mixed-commit-crash',
                'capabilities': ['interactions_v1']}
        job = {'job_id': 'synthetic-job', 'status': 'queued'}
        with patch('neurogra.platform.patient_agents.ModelGateway.generate_structured', return_value=plan) as planner, \
             patch('neurogra.platform.patient_agents.Executor.execute_tasks', new_callable=AsyncMock,
                   side_effect=self.specialist) as specialist, \
             patch.object(self.chat.analyses, 'report_status', return_value={'status': 'missing'}), \
             patch.object(self.chat.analyses, 'submit', return_value=job) as submit, \
             patch.object(self.chat.analyses, 'job', return_value=job) as lookup:
            with patch.object(self.chat.store, 'transaction', side_effect=RuntimeError('chat_commit_interruption')):
                with self.assertRaisesRegex(RuntimeError, 'chat_commit_interruption'):
                    self.chat.send(self.session, '1', self.sid, body, Mock())
            reply = self.chat.send(self.session, '1', self.sid, body, Mock(), resume_collaboration=True)['reply']
        self.assertEqual(reply['analysis_job_id'], 'synthetic-job')
        self.assertEqual(planner.call_count, 1)
        self.assertEqual(specialist.call_count, 1)
        self.assertEqual(submit.call_count, 1)
        lookup.assert_called_once()
        self.assertEqual(sum(item['role'] == 'assistant' for item in self.chat.store.database().get_messages(self.sid)), 1)

    def test_completed_child_before_parent_checkpoint_is_revalidated_on_resume(self):
        plan = DialoguePlan(patient_specific=True, forms=['moca'], roles=['cognition'], knowledge=False)
        body = {'message': '解释这个患者的 MoCA 结果，并生成完整分析报告', 'request_id': 'mixed-child-gap',
                'capabilities': ['interactions_v1']}
        finish, interrupted = CollaborationStore.finish_task, False

        def interrupt_parent(store, fence, task_id, *args, **kwargs):
            nonlocal interrupted
            if task_id == 'mixed_focused' and not interrupted:
                interrupted = True
                raise RuntimeError('child_finished_parent_not_committed')
            return finish(store, fence, task_id, *args, **kwargs)

        with patch('neurogra.platform.patient_agents.ModelGateway.generate_structured', return_value=plan) as planner, \
             patch('neurogra.platform.patient_agents.Executor.execute_tasks', new_callable=AsyncMock,
                   side_effect=self.specialist) as specialist, \
             patch.object(self.chat.analyses, 'report_status', return_value={'status': 'missing'}), \
             patch.object(self.chat.analyses, 'submit', return_value={'job_id': 'job', 'status': 'queued'}) as submit, \
             patch.object(CollaborationStore, 'finish_task', autospec=True, side_effect=interrupt_parent):
            with self.assertRaisesRegex(RuntimeError, 'child_finished_parent_not_committed'):
                self.chat.send(self.session, '1', self.sid, body, Mock())
            with patch.object(Reader, 'score', 24):
                with self.assertRaises(WorkspaceError) as changed:
                    self.chat.send(self.session, '1', self.sid, body, Mock(), resume_collaboration=True)
                self.assertEqual(changed.exception.status, 409)
            reply = self.chat.send(self.session, '1', self.sid, body, Mock(), resume_collaboration=True)['reply']
        self.assertIn('23', reply['text'])
        self.assertEqual(planner.call_count, 1)
        self.assertEqual(specialist.call_count, 1)
        submit.assert_called_once()

    def test_reused_report_must_still_be_current_after_chat_commit_interruption(self):
        plan = DialoguePlan(patient_specific=True, forms=['moca'], roles=['cognition'], knowledge=False)
        body = {'message': '解释这个患者的 MoCA 结果，并生成完整分析报告', 'request_id': 'mixed-old-report',
                'capabilities': ['interactions_v1']}
        job = {'job_id': 'synthetic-job', 'status': 'succeeded'}
        with patch('neurogra.platform.patient_agents.ModelGateway.generate_structured', return_value=plan), \
             patch('neurogra.platform.patient_agents.Executor.execute_tasks', new_callable=AsyncMock,
                   side_effect=self.specialist), \
             patch.object(self.chat.analyses, 'report_status',
                          side_effect=[{'status': 'unchanged', 'job': job}, {'status': 'changed', 'job': job}]), \
             patch.object(self.chat.analyses, 'job', return_value=job):
            with patch.object(self.chat.store, 'transaction', side_effect=RuntimeError('chat_commit_interruption')):
                with self.assertRaisesRegex(RuntimeError, 'chat_commit_interruption'):
                    self.chat.send(self.session, '1', self.sid, body, Mock())
            with self.assertRaises(WorkspaceError) as error:
                self.chat.send(self.session, '1', self.sid, body, Mock(), resume_collaboration=True)
        self.assertEqual(error.exception.status, 409)
        self.assertIn('报告对应的患者资料已变化', str(error.exception))
        self.assertFalse(any(item['role'] == 'assistant' for item in self.chat.store.database().get_messages(self.sid)))
