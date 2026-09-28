"""Regressions for the four-batch code review; no clinical model equivalence claims."""
import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from neurogra.clinical.tests import test_third_batch as third_batch
from neurogra.clinical.diamond import DiamondTool, DiamondModelProfile


class DiamondReuseTests(unittest.TestCase):
    def setUp(self):
        self.fixture = third_batch.ThirdBatchTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.snapshot = self.fixture.snapshot('imaging')
        self.snapshot.images = self.fixture.pair()
        self.profile = DiamondModelProfile(fingerprint='verified', hashes={}, validation_status='validated')
        self.calls = 0
        profile_patch = patch('neurogra.clinical.diamond.model_profile', return_value=self.profile)
        self.profile_mock = profile_patch.start()
        self.addCleanup(profile_patch.stop)
        process_patch = patch('neurogra.clinical.diamond.run_process', self.execute)
        process_patch.start()
        self.addCleanup(process_patch.stop)

    def execute(self, args, timeout, log):
        if '--output' in args:
            path = Path(args[args.index('--output') + 1])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{"eligible":true}', encoding='utf-8')
        else:
            self.calls += 1
            self.fixture.write_csv(Path(args[args.index('--output-csv') + 1]),
                                   self.snapshot.images[0].path, self.snapshot.images[1].path)
        return 0

    def tool(self):
        return DiamondTool(self.fixture.config, self.fixture.store, self.fixture.run)

    def test_identical_binding_reuses_without_inference(self):
        first = self.tool().predict(self.snapshot)
        self.assertEqual(self.tool().predict(self.snapshot), first)
        self.assertEqual(self.calls, 1)

    def test_changed_file_blocks_same_run_and_preserves_history(self):
        first = self.tool().predict(self.snapshot)
        Path(self.snapshot.images[0].path).write_bytes(b'new-volume')
        with self.assertRaisesRegex(ValueError, 'diamond_reuse_blocked'):
            self.tool().predict(self.snapshot)
        self.assertEqual(self.fixture.store.objects(self.fixture.run, 'diamond_result')[0]['payload']['prediction'], first.prediction)
        self.assertEqual(self.calls, 1)

    def test_revoked_validation_blocks_even_existing_tool_instance(self):
        tool = self.tool()
        tool.predict(self.snapshot)
        self.profile_mock.return_value = self.profile.model_copy(update={'validation_status': 'blocked_validation'})
        with self.assertRaisesRegex(ValueError, 'diamond_reuse_blocked'):
            tool.predict(self.snapshot)

    def test_changed_device_blocks_reuse(self):
        self.tool().predict(self.snapshot)
        self.fixture.config.diamond_device = 'cuda'
        with self.assertRaisesRegex(ValueError, 'diamond_reuse_blocked'):
            self.tool().predict(self.snapshot)

    def test_changed_model_and_legacy_binding_block(self):
        first = self.tool().predict(self.snapshot)
        self.profile_mock.return_value = self.profile.model_copy(update={'fingerprint': 'replacement'})
        with self.assertRaisesRegex(ValueError, 'model_changed'):
            self.tool().predict(self.snapshot)
        self.profile_mock.return_value = self.profile
        with self.assertRaisesRegex(ValueError, 'input_or_configuration_changed'):
            self.tool().assert_reusable(first.model_copy(update={'input_binding': {}}), self.snapshot)

    def test_completed_report_cannot_bypass_resume_validation(self):
        from neurogra.clinical.service import PreparationResult
        from neurogra.clinical.orchestration import fallback_plan
        from neurogra.clinical.workflow import continue_run
        self.tool().predict(self.snapshot)
        self.fixture.config.database = Path('test.sqlite')
        self.fixture.store.save(self.fixture.run, 'run_result', 'main', {'sentinel': 'historical report'})
        prep = PreparationResult(run_id=self.fixture.run, status='prepared', snapshot=self.snapshot,
            plan=fallback_plan(self.snapshot), knowledge_release_id=None, issues=[], artifact_path='unused')
        Path(self.snapshot.images[0].path).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'diamond_reuse_blocked'):
            continue_run(prep, self.fixture.config)

    def test_changed_eligibility_blocks_saved_prediction(self):
        self.tool().predict(self.snapshot)
        self.snapshot.images[1].tracer = None
        with self.assertRaisesRegex(ValueError, 'input_ineligible'):
            self.tool().predict(self.snapshot)

    def test_revocation_during_inference_clears_prediction(self):
        def execute(args, timeout, log):
            code = self.execute(args, timeout, log)
            if '--output-csv' in args:
                self.profile_mock.return_value = self.profile.model_copy(update={'validation_status': 'blocked_validation'})
            return code
        with patch('neurogra.clinical.diamond.run_process', execute):
            result = self.tool().predict(self.snapshot)
        self.assertEqual(result.status, 'failed')
        self.assertIsNone(result.prediction)
        self.assertEqual(result.scores, {})


class RetrievalDeadlineTests(unittest.TestCase):
    def setUp(self):
        from neurogra.clinical.config import ClinicalConfig
        from neurogra.clinical.storage import RunStore
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = ClinicalConfig(project_root=Path(self.tmp.name))
        self.store = RunStore(self.config.resolve(self.config.database))
        self.run = self.store.create_run({}, {'retrieval': 5, 'llm': 0, 'image': 0, 'seconds': 30})

    def test_proxy_passes_task_deadline(self):
        from neurogra.clinical.orchestration import ToolProxy
        from neurogra.clinical.schemas import AgentTask, CaseSnapshot, RetrievalRequest, EvidenceBundle
        seen = []
        class Knowledge:
            def search_knowledge(self, request, deadline=None):
                seen.append(deadline)
                return EvidenceBundle(bundle_id='b', request_id=request.request_id, status='empty', backend_status={})
        task = AgentTask(task_id='t', agent_type='history', case_version=1, input_refs=['o'],
                         questions=['q'], why='test', permitted_tools=['search_knowledge'])
        task.budget.timeout_seconds = .5
        snapshot = CaseSnapshot(case_id='c', patient_id='p', raw_input_hash='h', records=[])
        proxy = ToolProxy(task, snapshot, Knowledge(), self.store, self.run)
        asyncio.run(proxy.search_knowledge(RetrievalRequest(request_id='q', question='q')))
        self.assertEqual(seen, [proxy.deadline])

    def test_expired_request_does_not_reserve_or_launch(self):
        from neurogra.clinical.retrieval import KnowledgeService
        from neurogra.clinical.schemas import RetrievalRequest
        service = KnowledgeService(self.config, 'r', self.store, self.run)
        with self.assertRaises(TimeoutError):
            service.search_knowledge(RetrievalRequest(request_id='q', question='q'), deadline=time.time()-1)
        self.assertEqual(self.store.summary(self.run)['calls'], [])

    def test_late_backend_output_is_not_persisted_as_evidence(self):
        from neurogra.clinical.retrieval import KnowledgeService
        from neurogra.clinical.schemas import RetrievalRequest, EvidenceItem
        service = KnowledgeService(self.config, 'r', self.store, self.run)
        item = EvidenceItem(evidence_id='e', document_id='d', span_id='s', quote='late', version='r', matched_by=['bm25'])
        def late(*args):
            time.sleep(.06)
            return 'ok', [item], {}
        with patch.object(service, '_run_backend', side_effect=late, create=True):
            result = service.search_knowledge(RetrievalRequest(request_id='q', question='q', requested_backends=['bm25']),
                                              deadline=time.time()+.03)
        self.assertEqual(result.status, 'failed')
        self.assertEqual(result.items, [])
        self.assertEqual(self.store.objects(self.run, 'retrieval')[0]['payload']['items'], [])

    def test_process_is_killed_at_deadline(self):
        import sys
        from neurogra.clinical.retrieval_worker import run_json_process
        marker = Path(self.tmp.name) / 'late.txt'
        script = 'import sys,time,pathlib;time.sleep(2);pathlib.Path(sys.argv[1]).write_text("late")'
        start = time.monotonic()
        with self.assertRaises(TimeoutError):
            run_json_process([sys.executable, '-c', script, str(marker)], {}, time.time()+.15)
        self.assertLess(time.monotonic()-start, .8)
        self.assertFalse(marker.exists())

    def test_executor_timeout_reaps_blocking_backend_and_other_task_completes(self):
        import sys
        from neurogra.clinical.retrieval import KnowledgeService
        from neurogra.clinical.retrieval_worker import run_json_process
        from neurogra.clinical.orchestration import Executor, fallback_plan
        from neurogra.clinical.schemas import CaseSnapshot, Observation, InventoryItem, AgentResult, RetrievalRequest
        snapshot = CaseSnapshot(case_id='c', patient_id='p', raw_input_hash='h', records=[],
            observations=[Observation(observation_id=role, name='n', domains=[role], quote='q', value='q', status='observed', source_refs=[])
                          for role in ('history', 'cognition')],
            inventory=[InventoryItem(domain=role, availability='available', observation_ids=[role])
                       for role in ('history', 'cognition')])
        plan = fallback_plan(snapshot)
        for task in plan.tasks:
            task.budget.timeout_seconds = .2
        service = KnowledgeService(self.config, 'r', self.store, self.run)
        def slow(request, backend, deadline):
            run_json_process([sys.executable, '-c', 'import time;time.sleep(5)'], {}, deadline)
        class Agent:
            async def analyze(self, task, context):
                if task.agent_type == 'history':
                    await context.tools.search_knowledge(RetrievalRequest(request_id='q', question='q', requested_backends=['bm25']))
                return AgentResult(result_id=task.task_id, task_id=task.task_id, case_version=1, status='completed')
        executor = Executor(self.store, self.run, service)
        executor.register('history', Agent()); executor.register('cognition', Agent())
        start = time.monotonic()
        with patch.object(service, '_run_backend', side_effect=slow):
            results = asyncio.run(executor.execute_tasks(plan, snapshot))
        self.assertLess(time.monotonic()-start, .9)
        self.assertEqual({r.task_id: r.status for r in results},
                         {t.task_id: ('failed' if t.agent_type == 'history' else 'completed') for t in plan.tasks})
        self.assertTrue(all(not r['payload']['items'] for r in self.store.objects(self.run, 'retrieval')))

    def test_backend_failure_keeps_already_completed_sources(self):
        from neurogra.clinical.retrieval import KnowledgeService
        from neurogra.clinical.schemas import RetrievalRequest, EvidenceItem
        service = KnowledgeService(self.config, 'r', self.store, self.run)
        item = EvidenceItem(evidence_id='e', document_id='d', span_id='s', quote='source', version='r', matched_by=['bm25'])
        def backend(request, name, deadline):
            if name == 'bm25': return 'ok', [item], {}
            raise TimeoutError('vector limit')
        with patch.object(service, '_run_backend', side_effect=backend):
            result = service.search_knowledge(RetrievalRequest(request_id='q', question='q', requested_backends=['bm25','vector']))
        self.assertEqual(result.status, 'degraded')
        self.assertEqual([i.evidence_id for i in result.items], ['e'])

    def test_caller_cannot_extend_original_run_deadline(self):
        from neurogra.clinical.retrieval import KnowledgeService
        from neurogra.clinical.schemas import RetrievalRequest
        service = KnowledgeService(self.config, 'r', self.store, self.run)
        original = self.store.run(self.run)['deadline']
        captured = []
        def backend(request, name, deadline):
            captured.append(deadline)
            return 'empty', [], {}
        with patch.object(service, '_run_backend', side_effect=backend):
            service.search_knowledge(RetrievalRequest(request_id='q', question='q', requested_backends=['bm25']),
                                     deadline=original+1000)
        self.assertEqual(captured, [original])
        self.assertEqual(self.store.run(self.run)['deadline'], original)


class ProfilingDedupTests(unittest.TestCase):
    def profile(self, raw, response=None):
        from neurogra.clinical.config import ClinicalConfig
        from neurogra.clinical.intake import ingest_patient
        from neurogra.clinical.profiling import profile_case, CandidateBatch
        self.submitted = []
        def generate(system, data, schema):
            self.submitted.append(data['records'])
            return response(data['records']) if response else CandidateBatch(candidates=[])
        from types import SimpleNamespace
        config = ClinicalConfig()
        return profile_case(ingest_patient(raw, config), [], config, SimpleNamespace(generate_structured=generate))

    def test_multisentence_record_submitted_once(self):
        self.profile({'患者ID':'p', '病史':'患者记忆下降，常忘记刚说过的话，需要家属提醒。'*30})
        self.assertEqual(len(self.submitted), 1)
        self.assertEqual(len(self.submitted[0]), 1)

    def test_equal_text_distinct_records_preserve_both_sources(self):
        snapshot = self.profile({'患者ID':'p', '患者病史':'记忆下降。', '家属病史':'记忆下降。'})
        rows = [r for group in self.submitted for r in group]
        self.assertEqual(len({r['record_id'] for r in rows}), 2)
        self.assertEqual(len(snapshot.observations), 2)

    def test_partial_extraction_retains_uncovered_text(self):
        from neurogra.clinical.profiling import CandidateBatch, Candidate
        def reply(rows):
            return CandidateBatch(candidates=[Candidate(record_id=rows[0]['record_id'], quote='记忆下降。', domains=['history'], name='病史')])
        snapshot = self.profile({'患者ID':'p','病史':'记忆下降。未提供用药记录。'}, reply)
        self.assertEqual({o.quote for o in snapshot.observations}, {'记忆下降。', '未提供用药记录。'})

    def test_model_failure_preserves_rules_and_original_record(self):
        from neurogra.clinical.llm import ModelFailure
        def fail(rows): raise ModelFailure('offline')
        snapshot = self.profile({'患者ID':'p','病史':'记忆下降。未提供用药记录。'}, fail)
        self.assertEqual(len(snapshot.observations), 2)
        self.assertTrue(any(r.text == '记忆下降。未提供用药记录。' for r in snapshot.records))

    def test_oversized_record_retained_with_explicit_issue(self):
        text = '患者病史待核对。'*3000
        snapshot = self.profile({'患者ID':'p','病史':text})
        self.assertEqual(self.submitted, [])
        self.assertTrue(any(r.text == text for r in snapshot.records))
        self.assertEqual(sum(i.code == 'record_too_large_for_model' for i in snapshot.issues), 1)

    def test_budget_exhaustion_retains_all_raw_facts(self):
        from neurogra.clinical.storage import BudgetExceeded
        def exhausted(rows): raise BudgetExceeded('llm')
        snapshot = self.profile({'患者ID':'p','病史':'记忆下降。未提供用药记录。'}, exhausted)
        self.assertEqual({o.quote for o in snapshot.observations}, {'记忆下降。', '未提供用药记录。'})


if __name__ == '__main__':
    unittest.main()
