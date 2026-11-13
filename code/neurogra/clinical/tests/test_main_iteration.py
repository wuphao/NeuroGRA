import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from neurogra.clinical.config import ClinicalConfig
from neurogra.clinical.service import prepare_patient
from neurogra.clinical.storage import RunStore
from neurogra.clinical.schemas import AgentResult, Claim, RequestContent, DataFetchResult
from neurogra.clinical.main_tools import MainToolExecutor
from neurogra.clinical.patient_data import PatientDataService
from neurogra.clinical.orchestration import MainAgent
from neurogra.clinical.iteration import run_main_loop
from neurogra.clinical.intake import ingest_patient
from neurogra.clinical.profiling import profile_case
from neurogra.clinical.utils import identity


class MainIterationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = ClinicalConfig(project_root=Path(self.temp.name), text_knowledge_config=None)
        self.preparation = prepare_patient({"患者ID": "synthetic_iteration", "病史": "记忆下降", "MMSE总分": 24},
                                          self.config, use_model=False)
        self.store = RunStore(self.config.resolve(self.config.database))
        self.calls = {}
        self.fetches = []

    def run_loop(self, *, found=True, repeat=False, max_rounds=3):
        outer = self
        class Patient(PatientDataService):
            def fetch_patient_data(inner, request, request_id):
                outer.fetches.append(request_id)
                if not found:
                    return DataFetchResult(request_id=request_id, patient_id=inner.patient_id,
                                           status="not_recorded", missing_fields=["教育年限"])
                raw = {"患者ID": inner.patient_id, "教育年限": 9}
                snapshot = profile_case(ingest_patient(raw, inner.config), [], inner.config, None)
                return DataFetchResult(request_id=request_id, patient_id=inner.patient_id, status="found",
                                       records=snapshot.records, observations=snapshot.observations)
        class Handler:
            async def analyze(inner, task, context):
                role = task.agent_type
                outer.calls[role] = outer.calls.get(role, 0) + 1
                result = AgentResult(result_id=identity("result", task.task_id), task_id=task.task_id,
                    case_version=task.case_version, status="completed",
                    claims=[Claim(claim_id=identity("claim", [task.task_id, row.observation_id]),
                        text=row.quote, kind="description", level=role, observation_ids=[row.observation_id],
                        strength="descriptive") for row in context.snapshot.observations])
                if role == "cognition" and (repeat or not any(row.name == "教育年限" for row in context.snapshot.observations)):
                    result.requests.append(RequestContent(type="patient_data", question="补取已有教育记录",
                        requested_fields=["教育年限"], source_refs=[context.snapshot.observations[0].observation_id],
                        reason="量表解释条件缺失"))
                return result
        config = self.config.model_copy(update={"max_rounds": max_rounds})
        tools = MainToolExecutor(self.preparation.snapshot, config, self.store, self.preparation.run_id,
            patient_service=Patient(self.preparation.snapshot, config))
        return run_main_loop(self.preparation, config, self.store,
            handlers={role: Handler() for role in ("history", "cognition", "laboratory", "imaging")},
            tools=tools, main_factory=lambda round_no: MainAgent(None))

    def test_real_new_data_round_only_restarts_affected_role(self):
        state = self.run_loop()
        self.assertEqual(self.calls, {"cognition": 2, "history": 1})
        self.assertEqual(len(self.fetches), 1)
        self.assertEqual(state["snapshot"]["version"], 2)
        self.assertTrue(any(row["name"] == "教育年限" for row in state["snapshot"]["observations"]))
        self.assertEqual(state["stop_reason"], "no_actionable_requests")
        current = state["results"]["cognition"]
        self.assertEqual(current["case_version"], 2)
        self.assertTrue(self.store.objects(self.preparation.run_id, "main_tool_result"))

    def test_tool_distinguishes_budget_exhaustion_before_and_after_dispatch(self):
        from neurogra.clinical.storage import BudgetExceeded

        request = RequestContent(type='knowledge', question='核查量表解释', reason='补充本地依据')
        for retrieval_limit, expected in ((0, 'budget_exhausted'), (1, 'failed')):
            with self.subTest(retrieval_limit=retrieval_limit):
                run_id = self.store.create_run({}, {'seconds': 60, 'retrieval': retrieval_limit})
                tools = MainToolExecutor(self.preparation.snapshot, self.config, self.store, run_id)
                with patch.object(tools, '_knowledge', side_effect=BudgetExceeded('embedding')) as backend:
                    result = tools.execute(request, scope='synthetic')
                self.assertEqual(result['status'], expected)
                self.assertEqual(backend.call_count, retrieval_limit)
                self.assertEqual(tools.execute(request, scope='synthetic'), result)
                calls = self.store.summary(run_id)['calls']
                self.assertEqual(sum(row['n'] for row in calls if row['kind'] == 'retrieval'), retrieval_limit)

    def test_report_graph_resumes_committed_specialists_and_records_tool_dependencies(self):
        from types import SimpleNamespace
        from neurogra.clinical.collaboration_contracts import SubjectScope
        from neurogra.clinical.collaboration_store import CollaborationStore
        from neurogra.clinical.report_collaboration import ReportScheduler, report_collaboration

        base, run_id = self.store, self.preparation.run_id
        coordination = CollaborationStore(base)
        coordination.create(run_id, SubjectScope(source_id='synthetic', project_id='1',
            primary_patient='synthetic'), entry_kind='report', entry_key='report:' + run_id,
            config_digest='synthetic-config')
        finish = CollaborationStore.finish_task
        interrupted = []

        def interrupt_after_commit(instance, fence, task_id, *args, **kwargs):
            result = finish(instance, fence, task_id, *args, **kwargs)
            node = next(row for row in instance.snapshot(run_id)['tasks'] if row['task_id'] == task_id)
            if node['kind'] == 'specialist.analyze' and not interrupted:
                interrupted.append(task_id)
                raise RuntimeError('synthetic-interruption')
            return result

        with self.assertRaisesRegex(RuntimeError, 'synthetic-interruption'):
            with coordination.owned(run_id, 'first') as fence:
                self.store = coordination.fenced_store(fence)
                scheduler = ReportScheduler(coordination, fence, 'synthetic', self.config.enabled_roles(), lambda: None)
                with report_collaboration(scheduler), patch.object(
                        CollaborationStore, 'finish_task', autospec=True, side_effect=interrupt_after_commit):
                    self.run_loop()
        with coordination.owned(run_id, 'second') as fence:
            self.store = coordination.fenced_store(fence)
            scheduler = ReportScheduler(coordination, fence, 'synthetic', self.config.enabled_roles(), lambda: None)
            with report_collaboration(scheduler):
                state = self.run_loop()
            scheduler.finish_synthesis(SimpleNamespace(run_id=run_id, status='partial', report_paths={}))
        self.store = base
        nodes = coordination.snapshot(run_id)['tasks']
        self.assertEqual(coordination.snapshot(run_id)['state'], 'partial')
        self.assertGreaterEqual(sum(row['kind'] == 'specialist.analyze' for row in nodes), 3)
        self.assertGreaterEqual(sum(row['kind'] == 'patient.read' for row in nodes), 2)
        self.assertTrue(any(row['task_id'] == 'report_synthesis' for row in nodes))
        self.assertEqual(self.calls, {'cognition': 2, 'history': 1})
        self.assertEqual(len(self.fetches), 1)
        self.assertEqual(state['snapshot']['version'], 2)

    def test_report_graph_node_limit_preserves_partial_result_and_synthesis_slot(self):
        from types import SimpleNamespace
        from neurogra.clinical.collaboration_contracts import SubjectScope
        from neurogra.clinical.collaboration_store import CollaborationStore
        from neurogra.clinical.report_collaboration import ReportScheduler, report_collaboration

        base, run_id = self.store, self.preparation.run_id
        coordination = CollaborationStore(base)
        coordination.create(run_id, SubjectScope(source_id='synthetic', project_id='1',
            primary_patient='synthetic'), entry_kind='report', entry_key='report:' + run_id,
            config_digest='synthetic-config')
        with coordination.owned(run_id, 'first') as fence:
            self.store = coordination.fenced_store(fence)
            scheduler = ReportScheduler(coordination, fence, 'synthetic', self.config.enabled_roles(), lambda: None)
            with report_collaboration(scheduler), patch('neurogra.clinical.report_collaboration.MAX_GRAPH_NODES', 3):
                state = self.run_loop()
                self.assertEqual(state['stop_reason'], 'collaboration_node_limit')
                self.assertIn('协作节点达到上限', '；'.join(state['unresolved']))
                scheduler.finish_synthesis(SimpleNamespace(run_id=run_id, status='partial', report_paths={}))
        self.store = base
        graph = coordination.snapshot(run_id)
        self.assertEqual(graph['state'], 'partial')
        self.assertLessEqual(len(graph['tasks']), 3)
        self.assertTrue(any(row['kind'] == 'answer.synthesize' for row in graph['tasks']))

    def test_missing_data_stops_without_inventing_negative_facts(self):
        state = self.run_loop(found=False)
        self.assertEqual(len(self.fetches), 1)
        self.assertEqual(self.calls["cognition"], 1)
        self.assertEqual(state["stop_reason"], "no_new_information")
        self.assertFalse(any(row["name"] == "教育年限" for row in state["snapshot"]["observations"]))
        self.assertTrue(any("not_recorded" in text for text in state["unresolved"]))

    def test_repeated_request_is_not_executed_again(self):
        state = self.run_loop(repeat=True)
        self.assertEqual(len(self.fetches), 1)
        self.assertTrue(any("不重复执行" in text for text in state["unresolved"]))

    def test_same_role_subtasks_are_preserved_and_only_requesting_task_is_reexecuted(self):
        from neurogra.clinical.schemas import TaskPlan
        original = next(task for task in self.preparation.plan.tasks if task.agent_type == 'cognition')
        first = original.model_copy(deep=True, update={'task_id': 'synthetic-cognition-first',
                                                       'questions': ['核对教育条件']})
        second = original.model_copy(deep=True, update={'task_id': 'synthetic-cognition-second',
                                                        'questions': ['分析访视记录']})
        other = [task for task in self.preparation.plan.tasks if task.agent_type != 'cognition']
        preparation = self.preparation.model_copy(deep=True, update={
            'plan': TaskPlan(tasks=[*other, first, second])})
        calls = []
        outer = self

        class Patient(PatientDataService):
            def fetch_patient_data(inner, request, request_id):
                outer.fetches.append(request_id)
                raw = {'患者ID': inner.patient_id, '教育年限': 9}
                snapshot = profile_case(ingest_patient(raw, inner.config), [], inner.config, None)
                return DataFetchResult(request_id=request_id, patient_id=inner.patient_id, status='found',
                                       records=snapshot.records, observations=snapshot.observations)

        class Handler:
            async def analyze(inner, task, context):
                calls.append((task.agent_type, task.questions[0]))
                result = AgentResult(result_id=identity('result', task.task_id), task_id=task.task_id,
                    case_version=task.case_version, status='completed', claims=[
                        Claim(claim_id=identity('claim', [task.task_id, row.observation_id]),
                              text=row.quote, kind='description', level=task.agent_type,
                              observation_ids=[row.observation_id], strength='descriptive')
                        for row in context.snapshot.observations])
                if task.questions == ['核对教育条件'] and not any(
                        row.name == '教育年限' for row in context.snapshot.observations):
                    result.requests.append(RequestContent(type='patient_data', question='补取已有教育记录',
                        requested_fields=['教育年限'], source_refs=[context.snapshot.observations[0].observation_id],
                        reason='量表解释条件缺失'))
                return result

        tools = MainToolExecutor(preparation.snapshot, self.config, self.store, preparation.run_id,
                                 patient_service=Patient(preparation.snapshot, self.config))
        state = run_main_loop(preparation, self.config, self.store,
            handlers={role: Handler() for role in ('history', 'cognition', 'laboratory', 'imaging')},
            tools=tools, main_factory=lambda _: MainAgent(None))
        cognition = [row for row in state['tasks'].values() if row['agent_type'] == 'cognition']
        self.assertEqual(len(cognition), 2)
        self.assertEqual(len({row['task_id'] for row in cognition}), 2)
        self.assertEqual(calls.count(('cognition', '核对教育条件')), 2)
        self.assertEqual(calls.count(('cognition', '分析访视记录')), 1)
        self.assertEqual(len(self.fetches), 1)
        self.assertEqual(len([row for key, row in state['results'].items()
                              if state['tasks'][key]['agent_type'] == 'cognition']), 2)
        from neurogra.clinical.workflow import continue_run, Synthesis

        def select(_gateway, _system, data, _schema):
            return Synthesis(ordered_claim_ids=[row['claim_id'] for row in data['claims']])

        with patch('neurogra.clinical.workflow.ModelGateway.generate_structured', select), \
             patch('neurogra.clinical.narrative.add_narrative'):
            report = continue_run(preparation, self.config).report
        cognition_ids = {row['task_id'] for row in cognition}
        self.assertEqual({row.task_id for row in report.agent_results if row.task_id in cognition_ids}, cognition_ids)

    def test_deduplication_preserves_different_evidence_and_conditions(self):
        from neurogra.clinical.workflow import claim_deduplication_key
        claim = Claim(claim_id='one', text='条件性解释', kind='interpretation', level='cognition',
                      observation_ids=['observation'], evidence_ids=['evidence-one'], strength='conditional',
                      limitations=['条件需核实'], applicability='条件一')
        self.assertEqual(claim_deduplication_key(claim),
                         claim_deduplication_key(claim.model_copy(update={'claim_id': 'two'})))
        self.assertNotEqual(claim_deduplication_key(claim), claim_deduplication_key(
            claim.model_copy(update={'evidence_ids': ['evidence-two']})))
        self.assertNotEqual(claim_deduplication_key(claim), claim_deduplication_key(
            claim.model_copy(update={'applicability': '条件二'})))

    def test_planner_keeps_distinct_tasks_for_one_role(self):
        from neurogra.clinical.orchestration import PlanProposal, TaskProposal
        proposals = []
        for task in self.preparation.plan.tasks:
            proposals.append(TaskProposal(agent_type=task.agent_type, input_refs=task.input_refs,
                                          questions=['核对已有资料'], why='对应实际资料'))
            if task.agent_type == 'cognition':
                proposals.append(TaskProposal(agent_type=task.agent_type, input_refs=task.input_refs,
                                              questions=['说明解释局限'], why='对应实际资料'))

        class Gateway:
            def generate_structured(inner, *_args):
                return PlanProposal(tasks=proposals)

        plan = MainAgent(Gateway()).plan(self.preparation.snapshot)
        cognition = [task for task in plan.tasks if task.agent_type == 'cognition']
        self.assertEqual(plan.mode, 'model')
        self.assertEqual(len(cognition), 2)
        self.assertEqual(len({task.task_id for task in cognition}), 2)

    def test_completed_loop_restore_has_no_new_tasks_or_budget_calls(self):
        first = self.run_loop()
        calls = dict(self.calls)
        ledger = self.store.summary(self.preparation.run_id)["calls"]
        second = self.run_loop()
        self.assertEqual(first, second)
        self.assertEqual(self.calls, calls)
        self.assertEqual(self.store.summary(self.preparation.run_id)["calls"], ledger)

    def test_interrupt_after_tool_receipt_replays_without_double_fetch(self):
        import neurogra.clinical.iteration as module
        original = module.merge_patient_data
        flag = [False]
        def crash(snapshot, data):
            if not flag[0]:
                flag[0] = True
                raise RuntimeError("simulated_interruption_after_receipt")
            return original(snapshot, data)
        with patch.object(module, "merge_patient_data", crash):
            with self.assertRaises(RuntimeError):
                self.run_loop()
        state = self.run_loop()
        self.assertEqual(len(self.fetches), 1)
        self.assertEqual(state["snapshot"]["version"], 2)
        self.assertEqual(self.calls["history"], 1)

    def test_round_limit_leaves_pending_requirements_visible(self):
        state = self.run_loop(max_rounds=1)
        self.assertEqual(state["stop_reason"], "max_rounds")
        self.assertFalse(self.fetches)
        self.assertTrue(any("未执行补查需求" in text for text in state["unresolved"]))

    def test_expired_run_preserves_source_facts_without_new_calls(self):
        with self.store.connect() as db:
            db.execute("UPDATE runs SET deadline=0 WHERE run_id=?", (self.preparation.run_id,))
        before = self.store.summary(self.preparation.run_id)["calls"]
        state = self.run_loop()
        self.assertEqual(state["stop_reason"], "run_deadline")
        self.assertTrue(state["results"]["history"]["claims"])
        self.assertEqual(self.store.summary(self.preparation.run_id)["calls"], before)

    def test_report_uses_latest_tasks_and_audits_new_snapshot(self):
        from neurogra.clinical.workflow import continue_run, Synthesis
        from neurogra.clinical.acceptance import audit_run
        state = self.run_loop()
        def select(gateway, system, data, schema):
            return Synthesis(ordered_claim_ids=[row["claim_id"] for row in data["claims"]])
        with patch("neurogra.clinical.workflow.ModelGateway.generate_structured", select), \
                patch("neurogra.clinical.narrative.add_narrative"):
            result = continue_run(self.preparation, self.config)
        self.assertEqual(result.stop_reason, "no_actionable_requests")
        task_ids = {row["task_id"] for row in state["tasks"].values()}
        self.assertEqual({row.task_id for row in result.report.agent_results}, task_ids)
        self.assertTrue(any("9" in row.text for row in result.report.claims))
        self.assertTrue(all(Path(value).is_file() for value in result.report_paths.values()))
        self.assertEqual(result.report.report_template_version, '2.2')
        self.assertTrue({'data_appendix', 'audit', 'provenance'} <= result.report_paths.keys())
        self.assertIn('、诊断结论', Path(result.report_paths['markdown']).read_text(encoding='utf-8'))
        self.assertNotIn('## 主 Agent 迭代记录', Path(result.report_paths['markdown']).read_text(encoding='utf-8'))
        _, audit = audit_run(self.config, self.preparation.run_id)
        self.assertTrue(audit["protocol_passed"], audit)

    def test_report_write_interruption_reuses_loop_and_synthesis(self):
        from neurogra.clinical.workflow import continue_run, resume_run, Synthesis
        self.run_loop()
        calls = []
        def select(gateway, system, data, schema):
            calls.append(schema)
            return Synthesis(ordered_claim_ids=[row["claim_id"] for row in data["claims"]])
        with patch("neurogra.clinical.workflow.ModelGateway.generate_structured", select), \
                patch("neurogra.clinical.narrative.add_narrative"):
            with patch("neurogra.clinical.workflow.render_report", side_effect=RuntimeError("report_write_interruption")):
                with self.assertRaises(RuntimeError):
                    continue_run(self.preparation, self.config)
            count = len(calls)
            outcome = resume_run(self.preparation.run_id, self.config)
        self.assertEqual(len(calls), count)
        self.assertTrue(outcome.report.claims)

    def test_unfinished_legacy_run_is_read_only_and_does_not_spend_budget(self):
        from neurogra.clinical.workflow import continue_run
        with self.store.connect() as db:
            db.execute("DELETE FROM objects WHERE run_id=? AND kind='execution_binding'", (self.preparation.run_id,))
        before = self.store.summary(self.preparation.run_id)['calls']
        with self.assertRaisesRegex(RuntimeError, 'legacy_run_read_only'):
            continue_run(self.preparation, self.config)
        self.assertEqual(self.store.summary(self.preparation.run_id)['calls'], before)

    def test_changed_execution_binding_cannot_resume_under_new_code(self):
        from neurogra.clinical.execution import verify_execution_binding
        with self.store.connect() as db:
            db.execute("UPDATE objects SET payload=? WHERE run_id=? AND kind='execution_binding'",
                       ('{"contract":"old","implementation":"old"}', self.preparation.run_id))
        with self.assertRaisesRegex(RuntimeError, 'implementation_changed'):
            verify_execution_binding(self.store, self.preparation.run_id)

    def test_resume_cannot_reprepare_legacy_input_before_compatibility_check(self):
        from neurogra.clinical.workflow import resume_run
        Path(self.preparation.artifact_path).unlink()
        with self.store.connect() as db:
            db.execute("DELETE FROM objects WHERE run_id=? AND kind='execution_binding'", (self.preparation.run_id,))
        before = self.store.summary(self.preparation.run_id)['calls']
        with self.assertRaisesRegex(RuntimeError, 'legacy_run_read_only'):
            resume_run(self.preparation.run_id, self.config)
        self.assertEqual(before, self.store.summary(self.preparation.run_id)['calls'])

    def test_continue_cannot_change_saved_model_or_configuration(self):
        from neurogra.clinical.workflow import continue_run
        changed = self.config.model_copy(update={'max_rounds': 1})
        with self.assertRaisesRegex(ValueError, 'run_configuration_changed'):
            continue_run(self.preparation, changed)

    def test_graph_gaps_create_code_owned_requests_with_bounded_supplement_permission(self):
        from neurogra.clinical.iteration import queue_knowledge_gaps
        state = {'seen': [], 'pending': []}
        request = RequestContent(type='knowledge', question='规则适用条件', reason='核验', source_refs=[])
        data = {'data_requests': [{'gap_id': 'code_gap1', 'fields': ['age'], 'reason': '年龄未提供'}],
                'gaps': [{'gap_id': 'code_gap2', 'kind': 'knowledge_condition', 'missing_items': ['方法条件'], 'reason': '缺原文条件'}]}
        queue_knowledge_gaps(state, data, 'cognition', {'action_id': 'code_action'}, request, self.preparation.snapshot)
        self.assertEqual(len(state['pending']), 2)
        self.assertTrue(all(row['request_ref'].startswith('request_') for row in state['pending']))
        self.assertEqual(state['pending'][0]['request']['requested_fields'], ['年龄'])
        decision = MainAgent(None).decide({'pending_requests': state['pending']})
        self.assertTrue(decision.actions[1].allow_supplement)

    def test_fresh_condition_assessment_replaces_old_support_for_same_statement(self):
        from neurogra.clinical.iteration import update_evidence
        old = {'evidence_id': 'code_old', 'version': 'code_release', 'document_id': 'code_doc',
               'span_id': 'code_span', 'statement_id': 'code_statement', 'applicability': 'applicable'}
        fresh = {**old, 'evidence_id': 'code_new', 'applicability': 'not_applicable'}
        items, changed = update_evidence([old], [fresh])
        self.assertTrue(changed)
        self.assertEqual(items, [fresh])
