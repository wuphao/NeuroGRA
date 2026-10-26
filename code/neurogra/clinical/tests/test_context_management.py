"""Budget, source preservation, pagination and coverage behavior."""
import asyncio
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from neurogra.clinical.config import ModelConfig, ClinicalConfig, KnowledgeAccess
from neurogra.clinical.context import ContextBuilder
from neurogra.clinical.context_budget import ContextBudgetExceeded, messages_for, require_fit
from neurogra.clinical.context_views import select_evidence, tool_receipt
from neurogra.clinical.context_views import source_page
from neurogra.clinical.history import HistoryAgent, GroundedDraft
from neurogra.clinical.llm import ModelGateway, ModelFailure
from neurogra.clinical.orchestration import TaskContext
from neurogra.clinical.patient_data import PatientDataService
from neurogra.clinical.schemas import Observation, CaseSnapshot, AgentTask, EvidenceItem, RequestContent
from neurogra.clinical.service import create_context
from neurogra.clinical.narrative import validate_global_absence
from neurogra.clinical.agent_state import material_hash


def facts(count):
    return [Observation(observation_id=f'o{i}', name='MMSE总分', quote=str(i), value=i,
        domains=['cognition'], status='observed', source_refs=[]) for i in range(count)]


def case(rows, patient='p'):
    return CaseSnapshot(case_id='c', patient_id=patient, raw_input_hash='h', records=[], observations=rows)


def task(rows):
    return AgentTask(task_id='t', agent_type='cognition', case_version=1,
        input_refs=[o.observation_id for o in rows], questions=['核对记录'], why='实际资料', permitted_tools=[])


class ContextManagementTests(unittest.TestCase):
    def test_schema_repair_includes_precise_limits_without_replaying_bad_content(self):
        with tempfile.TemporaryDirectory() as root:
            store, run_id = create_context(ClinicalConfig(project_root=Path(root)))
            invalid = {'claims': [{'text': 'PRIVATE_GENERATED_TEXT', 'kind': 'description',
                'level': 'history', 'observation_ids': [f'O{i}' for i in range(10)],
                'strength': 'descriptive'}], 'uncertainties': []}
            payloads = []
            def transport(url, payload, timeout):
                payloads.append(json.loads(json.dumps(payload)))
                return {'message': {'content': json.dumps(invalid if len(payloads) == 1 else
                    {'claims': [], 'uncertainties': []})}, 'prompt_eval_count': 10}
            gateway = ModelGateway(ModelConfig(), store, run_id, transport=transport)
            gateway.generate_structured('test', {}, GroundedDraft)
            repair = payloads[1]['messages'][-1]['content']
            self.assertIn('at most 8', repair)
            self.assertNotIn('PRIVATE_GENERATED_TEXT', repair)
            self.assertEqual(len(store.objects(run_id, 'context_manifest')), 2)

    def test_main_view_is_bounded_without_mutating_full_state(self):
        from neurogra.clinical.orchestration import IterationDecision, ITERATION_PROMPT
        context = {'pending_requests': [{'request_ref': str(i), 'role': 'history',
            'request': {'question': '问题' * 500, 'source_refs': []}} for i in range(20)],
            'results': [], 'role_input_refs': {}, 'catalog': {}}
        view = ContextBuilder(ModelConfig()).decision_context(context, ITERATION_PROMPT, IterationDecision)
        require_fit(messages_for(ITERATION_PROMPT, view), IterationDecision, ModelConfig())
        self.assertEqual(len(context['pending_requests']), 20)
        self.assertGreater(view['omitted_pending_request_count'], 0)

    def test_source_pages_keep_complete_objects_and_bind_permissions(self):
        data = {'records': [{'record_id': str(i), 'text': '原始值' * 40} for i in range(11)]}
        req = RequestContent(type='source_context', question='核对原文', reason='定位',
                             source_refs=['s'], page_size=3)
        collected = []
        while True:
            page = source_page(data, req, {'patient': 'p', 'permission': 'allowed'})
            collected.extend(page.get('records', []))
            if not page['next_cursor']:
                break
            req.cursor = page['next_cursor']
        self.assertEqual(collected, data['records'])
        with self.assertRaisesRegex(ValueError, 'invalid_source_cursor'):
            source_page(data, req, {'patient': 'p', 'permission': 'disabled'})
        req.cursor = None
        with self.assertRaisesRegex(ValueError, 'source_item_exceeds'):
            source_page({'records': [{'text': 'large' * 10000}]}, req, {})

    def test_date_filter_and_cursor_bind_to_same_filter(self):
        from neurogra.clinical.schemas import TimeValue
        rows = facts(3)
        for row, day in zip(rows, ('2024-01-01', '2025-01-01', '2026-01-01')):
            row.time = TimeValue(value=day, raw=day, precision='day')
        service = PatientDataService(case(rows), ClinicalConfig())
        request = RequestContent(type='patient_data', question='按日期读取', reason='定位',
            requested_fields=['MMSE总分'], date_from='2025-01-01', page_size=1)
        result = service.fetch_patient_data(request, 'r')
        self.assertEqual(result.total_count, 2)
        self.assertEqual(result.observations[0].observation_id, 'o1')
        request.cursor = result.next_cursor
        request.date_from = '2024-01-01'
        self.assertEqual(service.fetch_patient_data(request, 'r').error, 'stale_or_invalid_cursor')

    def test_only_validated_batches_are_reused_across_scheduling_changes(self):
        from neurogra.clinical.orchestration import AnalysisRuntime
        with tempfile.TemporaryDirectory() as root:
            store, run_id = create_context(ClinicalConfig(project_root=Path(root)))
            runtime = AnalysisRuntime(task(facts(1)), store, run_id, ModelConfig())
            draft = GroundedDraft(claims=[], uncertainties=[])
            runtime.gateway.generate_structured = Mock(return_value=draft)
            data = {'observations': [{'value': 1}], 'batch': 1, 'previous_result': None}
            asyncio.run(runtime.generate_structured('system', data, GroundedDraft))
            asyncio.run(runtime.generate_structured('system', data, GroundedDraft))
            self.assertEqual(runtime.gateway.generate_structured.call_count, 2)
            runtime.commit_structured('system', data, GroundedDraft, draft)
            changed_schedule = {**data, 'batch': 99, 'continuation_ref': 'new-task',
                                'previous_result': {'version': 2}}
            asyncio.run(runtime.generate_structured('system', changed_schedule, GroundedDraft))
            self.assertEqual(runtime.gateway.generate_structured.call_count, 2)
            asyncio.run(runtime.generate_structured('system', {'observations': [{'value': 2}]}, GroundedDraft))
            self.assertEqual(runtime.gateway.generate_structured.call_count, 3)

    def test_knowledge_phase_limit_is_shared_across_tools(self):
        from neurogra.clinical.main_tools import RunModelBudget
        from neurogra.clinical.storage import BudgetExceeded
        with tempfile.TemporaryDirectory() as root:
            store, run_id = create_context(ClinicalConfig(project_root=Path(root)))
            first = RunModelBudget(store, run_id, 'tool1', max_llm_calls=2)
            second = RunModelBudget(store, run_id, 'tool2', max_llm_calls=2)
            first.reserve('llm')
            first.finish('completed')
            second.reserve('llm')
            second.finish('completed')
            with self.assertRaises(BudgetExceeded):
                RunModelBudget(store, run_id, 'tool3', max_llm_calls=2).reserve('llm')

    def test_whole_request_includes_schema_system_and_chinese(self):
        config = ModelConfig(context_tokens=5000, max_output_tokens=1000)
        with self.assertRaises(ContextBudgetExceeded):
            require_fit(messages_for('长系统提示' * 3000, {}), GroundedDraft, config)
        with self.assertRaises(ContextBudgetExceeded):
            require_fit(messages_for('', {'text': '中文' * 10000}), GroundedDraft, ModelConfig())

    def test_rejected_build_does_not_consume_model_budget(self):
        with tempfile.TemporaryDirectory() as root:
            store, run_id = create_context(ClinicalConfig(project_root=Path(root)))
            transport = Mock()
            gateway = ModelGateway(ModelConfig(max_input_chars=100), store, run_id, transport=transport)
            with self.assertRaises(ModelFailure):
                gateway.generate_structured('test', {'text': 'x' * 200}, GroundedDraft)
            transport.assert_not_called()
            self.assertEqual(store.summary(run_id)['calls'], [])
            self.assertTrue(store.objects(run_id, 'execution_binding'))

    def test_large_tool_results_do_not_reenter_each_batch(self):
        rows = facts(83)
        outcomes = [{'action_id': 'a', 'request': {'type': 'knowledge'}, 'status': 'found',
                     'data': {'evidence': [{'quote': 'large' * 20000}]}}]
        context = TaskContext(case(rows), SimpleNamespace(), request_outcomes=outcomes)
        prepared = list(ContextBuilder(ModelConfig()).iter_batches(task(rows), context, '', GroundedDraft))
        seen = []
        for batch, (data, aliases, _, manifest) in prepared:
            require_fit(messages_for('', data), GroundedDraft, ModelConfig())
            self.assertNotIn('data', data['request_outcomes'][0])
            seen.extend(o.observation_id for o in batch)
            self.assertEqual(set(manifest['observation_aliases'].values()), {o.observation_id for o in batch})
        self.assertEqual(seen, [o.observation_id for o in rows])

    def test_receipt_does_not_repeat_long_query_but_preserves_field_scope(self):
        receipt = tool_receipt({'request': {'type': 'patient_data', 'question': '长查询' * 2000,
            'requested_fields': ['年龄'], 'date_from': '2025-01-01'}, 'data': {}, 'status': 'not_recorded'})
        self.assertNotIn('question', receipt)
        self.assertEqual(receipt['requested_fields'], ['年龄'])
        self.assertEqual(receipt['date_from'], '2025-01-01')
        self.assertLess(len(json.dumps(receipt)), 1000)

    def test_oversized_value_is_explicit_and_later_values_still_processed(self):
        rows = facts(2)
        rows[0].quote = '无法省略的原文' * 10000
        prepared = list(ContextBuilder(ModelConfig()).iter_batches(task(rows), TaskContext(case(rows), None), '', GroundedDraft))
        self.assertIsInstance(prepared[0][1], ContextBudgetExceeded)
        self.assertEqual(prepared[1][0][0].observation_id, 'o1')
        self.assertEqual(rows[0].quote, '无法省略的原文' * 10000)

    def test_evidence_dedup_preserves_different_conditions(self):
        evidence = EvidenceItem(evidence_id='e1', document_id='d', span_id='s', version='v',
            quote='原文条件', matched_by=['kg_vector'])
        duplicate = evidence.model_copy(update={'evidence_id': 'e2'})
        changed = evidence.model_copy(update={'evidence_id': 'e3', 'applicability': 'applicable'})
        selected = select_evidence([evidence, duplicate, changed], facts(1), 5000)
        self.assertEqual([e.evidence_id for e in selected], ['e1', 'e3'])

    def test_pagination_covers_all_records_and_rejects_cross_patient(self):
        rows = facts(123)
        config = ClinicalConfig()
        service = PatientDataService(case(rows), config)
        request = RequestContent(type='patient_data', question='读取', requested_fields=['MMSE总分'],
            reason='核对', page_size=20)
        collected = []
        while True:
            result = service.fetch_patient_data(request, 'r')
            self.assertEqual(result.total_count, 123)
            collected.extend(o.observation_id for o in result.observations)
            if not result.next_cursor:
                break
            request.cursor = result.next_cursor
        self.assertEqual(collected, [o.observation_id for o in rows])
        other = PatientDataService(case(rows, patient='other'), config)
        self.assertEqual(other.fetch_patient_data(request, 'r').error, 'stale_or_invalid_cursor')

    def test_failed_batches_have_accurate_coverage(self):
        rows = facts(45)
        class Runtime:
            async def generate_structured(self, *args):
                raise ModelFailure('synthetic failure')
            def record_analysis_batch(self, *args): pass
        result = asyncio.run(HistoryAgent().analyze(task(rows), TaskContext(case(rows), Runtime())))
        self.assertEqual(result.coverage['total'], 45)
        self.assertEqual(result.coverage['analyzed_ids'], [])
        self.assertEqual(set(result.coverage['failed_ids']), {o.observation_id for o in rows})

    def test_budget_stop_is_deferred_not_failed_analysis(self):
        from neurogra.clinical.storage import BudgetExceeded
        rows = facts(45)
        class Runtime:
            async def generate_structured(self, *args):
                raise BudgetExceeded('task_llm')
            def record_analysis_batch(self, *args): pass
        result = asyncio.run(HistoryAgent().analyze(task(rows), TaskContext(case(rows), Runtime())))
        self.assertEqual(result.coverage['failed_ids'], [])
        self.assertEqual(result.coverage['analyzed_ids'], [])
        self.assertEqual(set(result.coverage['deferred_ids']), {o.observation_id for o in rows})

    def test_global_absence_checked_against_full_case(self):
        row = facts(1)[0].model_copy(update={'name': '患病情况', 'quote': 'MCI'})
        with self.assertRaisesRegex(ValueError, 'contradicts_recorded'):
            validate_global_absence('资料中未提供具体的患病情况描述。', case([row]))

    def test_material_binding_changes_for_question_permission_and_value(self):
        rows = facts(1)
        t = task(rows)
        access = KnowledgeAccess()
        original = material_hash(t, case(rows), [], access)
        changed = t.model_copy(update={'questions': ['新问题']})
        self.assertNotEqual(original, material_hash(changed, case(rows), [], access))
        self.assertNotEqual(original, material_hash(t, case(rows), [], KnowledgeAccess(use_kg=False)))
        rows[0].value = 99
        self.assertNotEqual(original, material_hash(t, case(rows), [], access))


if __name__ == '__main__':
    unittest.main()
