import asyncio
import csv
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from neurogra.clinical.config import ClinicalConfig
from neurogra.clinical.schemas import *
from neurogra.clinical.specialists import CognitionAgent, LaboratoryAgent
from neurogra.clinical.imaging import ImagingAgent
from neurogra.clinical.history import HistoryDraft
from neurogra.clinical.orchestration import MainAgent, PlanProposal, TaskProposal, Executor, fallback_plan, validate_plan
from neurogra.clinical.diamond import *
from neurogra.clinical.storage import RunStore
from neurogra.clinical.utils import parse_time


class ThirdBatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = ClinicalConfig(project_root=self.root, patient_data_root=Path('.'))
        self.store = RunStore(self.root / 'test.sqlite')
        self.run = self.store.create_run({}, {'llm': 10, 'retrieval': 10, 'image': 2, 'seconds': 60})

    def snapshot(self, role='cognition', context=None, status='observed'):
        return CaseSnapshot(case_id='case', patient_id='p', raw_input_hash='raw', records=[],
            observations=[Observation(observation_id='o', name='原始项目', value=7, quote='自制观察表总分7，未注明校正。',
                domains=[role], status=status, source_refs=['r'], context=context or {})])

    def result(self):
        return AgentResult(result_id='r', task_id='t', case_version=1, status='completed',
            claims=[Claim(claim_id='c', text='分数提示异常', kind='interpretation', level='specialist',
                observation_ids=['o'], evidence_ids=['e'], strength='conditional', limitations=['待验证'])])

    def pair(self):
        output = []
        for key, modality in [('m', 'MRI'), ('p', 'PET')]:
            path = self.root / (key + '.nii')
            path.write_bytes(b'fixture')
            output.append(ImageAsset(asset_id=key, modality=modality, sequence='T1', tracer='FDG',
                time=parse_time('2026-09-01'), path=str(path), status='available'))
        return output

    def test_unknown_scale_no_default_threshold_or_correction(self):
        result = CognitionAgent().enrich(self.result(), self.snapshot(context={'名称': '自制观察表', '版本': '1', '教育校正': '未做', '语言': '中文'}))
        self.assertEqual(result.claims[0].kind, 'description')
        self.assertIn('量表解释规则未登记', result.scale_summaries[0].missing_context)
        self.assertEqual(result.scale_summaries[0].value, 7)

    def test_laboratory_missing_method_no_inferred_abnormality(self):
        result = LaboratoryAgent().enrich(self.result(), self.snapshot('laboratory', {'名称': 'p-tau181', '单位': 'pg/mL'}))
        self.assertEqual(result.claims[0].kind, 'description')
        self.assertEqual(result.report_flags, [])
        self.assertEqual(result.comparable_series, [])
        self.assertIn('方法', result.test_findings[0].missing_context)

    def test_not_performed_preserved(self):
        result = LaboratoryAgent().enrich(self.result(), self.snapshot('laboratory', status='not_performed'))
        self.assertEqual(result.test_findings[0].status, 'not_performed')
        self.assertEqual(result.report_flags, [])

    def test_distinct_dates_not_merged(self):
        snapshot = self.snapshot()
        snapshot.observations[0].time = parse_time('2026-01-01')
        snapshot.observations.append(snapshot.observations[0].model_copy(update={'observation_id': 'o2', 'time': parse_time('2026-09-01')}))
        result = CognitionAgent().enrich(self.result(), snapshot)
        self.assertEqual(len(result.scale_summaries), 2)
        self.assertNotEqual(result.scale_summaries[0].time, result.scale_summaries[1].time)

    def test_single_modality_rejected(self):
        self.assertFalse(check_diamond_eligibility(self.pair()[:1], self.config).eligible)

    def test_unknown_tracer_and_date_rejected(self):
        pair = self.pair()
        pair[1].tracer = None
        pair[0].time = parse_time(None)
        self.assertEqual(len(check_diamond_eligibility(pair, self.config).reasons), 2)

    def test_multiple_series_directory_not_guessed(self):
        pair = self.pair()
        pair[0].path = str(self.root)
        self.assertFalse(check_diamond_eligibility(pair, self.config).eligible)

    def test_pair_window(self):
        pair = self.pair()
        pair[1].time = parse_time('2026-09-02')
        self.assertFalse(check_diamond_eligibility(pair, self.config).eligible)
        self.config.diamond_max_pair_days = 2
        self.assertTrue(check_diamond_eligibility(pair, self.config).eligible)

    def test_unvalidated_model_never_runs(self):
        snapshot = self.snapshot('imaging')
        snapshot.images = self.pair()
        with patch('neurogra.clinical.diamond.model_profile', return_value=DiamondModelProfile(fingerprint='p', hashes={})), patch('neurogra.clinical.diamond.run_process', side_effect=AssertionError('must not run')):
            result = DiamondTool(self.config, self.store, self.run).predict(snapshot)
        self.assertEqual(result.status, 'blocked_validation')
        self.assertIsNone(result.prediction)
        self.assertEqual(result.scores, {})
        self.assertEqual(self.store.summary(self.run)['calls'], [])

    def write_csv(self, path, mri, pet, scores=('0.1', '0.2', '0.7'), label='AD'):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('w', newline='', encoding='utf-8') as stream:
            writer = csv.writer(stream)
            writer.writerow(['mri','pet','pred_idx','pred_label','prob_0','prob_1','prob_2'])
            writer.writerow([mri, pet, 2, label, *scores])

    def test_csv_rejects_nan_wrong_label_and_patient_binding(self):
        path = self.root / 'prediction.csv'
        for scores, label in [(('nan', '0', '1'), 'AD'), (('0.1', '0.2', '0.7'), 'CN')]:
            self.write_csv(path, 'm', 'p', scores, label)
            with self.assertRaises(ValueError): normalize_csv(path, 'm', 'p')
        self.write_csv(path, 'other', 'p')
        with self.assertRaises(ValueError): normalize_csv(path, 'm', 'p')

    def test_validated_protocol_and_cache_reuse_across_runs(self):
        snapshot = self.snapshot('imaging')
        snapshot.images = self.pair()
        calls = []
        def execute(args, timeout, log):
            if '--output' in args:
                path = Path(args[args.index('--output') + 1]); path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('{"eligible": true}', encoding='utf-8')
                return 0
            calls.append(args)
            self.write_csv(Path(args[args.index('--output-csv') + 1]), snapshot.images[0].path, snapshot.images[1].path)
            return 0
        profile = DiamondModelProfile(fingerprint='validated-test', hashes={}, validation_status='validated')
        with patch('neurogra.clinical.diamond.model_profile', return_value=profile), patch('neurogra.clinical.diamond.run_process', execute):
            first = DiamondTool(self.config, self.store, self.run).predict(snapshot)
            second_run = self.store.create_run({}, {'llm': 0, 'retrieval': 0, 'image': 2, 'seconds': 60})
            second = DiamondTool(self.config, self.store, second_run).predict(snapshot)
        self.assertEqual(first.prediction, 'AD')
        self.assertTrue(second.cache_hit)
        self.assertEqual(len(calls), 1)
        self.assertEqual(first.score_type, 'softmax_uncalibrated')

    def test_timeout_has_no_prediction(self):
        snapshot = self.snapshot('imaging'); snapshot.images = self.pair()
        profile = DiamondModelProfile(fingerprint='p', hashes={}, validation_status='validated')
        with patch('neurogra.clinical.diamond.model_profile', return_value=profile), patch('neurogra.clinical.diamond.run_process', side_effect=subprocess.TimeoutExpired('tool', 1)):
            result = DiamondTool(self.config, self.store, self.run).predict(snapshot)
        self.assertEqual(result.status, 'timeout')
        self.assertIsNone(result.prediction)

    def test_report_only_imaging_never_calls_classifier(self):
        snapshot = self.snapshot('imaging')
        snapshot.observations[0].quote = '报告记载脑萎缩。'
        snapshot.observations[0].name = '影像报告'
        task = AgentTask(task_id='t', agent_type='imaging', case_version=1, input_refs=['o'],
            questions=['整理报告'], why='提供报告', permitted_tools=['read_case_record', 'search_knowledge'])
        class Tools:
            async def search_knowledge(self, request):
                return EvidenceBundle(bundle_id='b', request_id=request.request_id, status='empty', backend_status={'bm25': 'empty'})
            async def generate_structured(self, *args): return HistoryDraft()
            async def diamond_predict(self): raise AssertionError('no image supplied')
        result = asyncio.run(ImagingAgent().analyze(task, SimpleNamespace(snapshot=snapshot, tools=Tools())))
        self.assertEqual(result.model_results, [])
        self.assertEqual(result.report_findings[0].quote, '报告记载脑萎缩。')

    def test_image_only_ineligible_result_never_invents_classification(self):
        snapshot = self.snapshot('imaging'); snapshot.observations = []; snapshot.images = self.pair()[:1]
        task = AgentTask(task_id='t', agent_type='imaging', case_version=1, input_refs=['m'],
            questions=['检查适用性'], why='仅MRI', permitted_tools=['search_knowledge', 'diamond_predict'])
        class Tools:
            async def search_knowledge(self, request): raise ValueError('offline')
            async def diamond_predict(self):
                return DiamondResult(result_id='d', patient_id='p', case_version=1, status='ineligible',
                    model_fingerprint='f', warnings=['缺少PET'])
        result = asyncio.run(ImagingAgent().analyze(task, SimpleNamespace(snapshot=snapshot, tools=Tools())))
        self.assertEqual(result.status, 'partial')
        self.assertEqual(result.model_results, [])
        self.assertIn('缺少PET', result.uncertainties)

    def test_negative_abnormal_flag_not_reported_as_positive(self):
        snapshot = self.snapshot('laboratory')
        snapshot.observations[0].quote = '报告未标记异常。'
        result = LaboratoryAgent().enrich(self.result(), snapshot)
        self.assertEqual(result.report_flags, [])

    def test_model_fingerprint_changes_with_checkpoint(self):
        self.config.diamond_root = self.root
        self.config.diamond_checkpoint = self.root / 'weight.pt'
        self.config.diamond_python = self.root / 'python.exe'
        self.config.diamond_checkpoint.write_bytes(b'first')
        first = model_profile(self.config)
        self.config.diamond_checkpoint.write_bytes(b'second')
        second = model_profile(self.config)
        self.assertNotEqual(first.fingerprint, second.fingerprint)
        self.assertEqual(second.validation_status, 'blocked_validation')

    def test_input_change_invalidates_cache(self):
        snapshot = self.snapshot('imaging'); snapshot.images = self.pair()
        calls = []
        def execute(args, timeout, log):
            if '--output' in args:
                path = Path(args[args.index('--output') + 1]); path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('{"eligible": true}', encoding='utf-8')
                return 0
            calls.append(args)
            self.write_csv(Path(args[args.index('--output-csv') + 1]), snapshot.images[0].path, snapshot.images[1].path)
            return 0
        profile = DiamondModelProfile(fingerprint='v', hashes={}, validation_status='validated')
        with patch('neurogra.clinical.diamond.model_profile', return_value=profile), patch('neurogra.clinical.diamond.run_process', execute):
            DiamondTool(self.config, self.store, self.run).predict(snapshot)
            Path(snapshot.images[0].path).write_bytes(b'changed_input')
            other = self.store.create_run({}, {'llm': 0, 'retrieval': 0, 'image': 2, 'seconds': 60})
            result = DiamondTool(self.config, self.store, other).predict(snapshot)
        self.assertFalse(result.cache_hit)
        self.assertEqual(len(calls), 2)

    def test_real_process_timeout_returns_after_kill(self):
        import sys
        with self.assertRaises(subprocess.TimeoutExpired):
            run_process([sys.executable, '-c', 'import time; time.sleep(20)'], .15, self.root / 'child.log')

    def test_planner_cannot_silently_drop_image_assets(self):
        snapshot = self.snapshot('imaging'); snapshot.images = self.pair()
        snapshot.inventory = [InventoryItem(domain='imaging', availability='partial', observation_ids=['o'])]
        class Gateway:
            def generate_structured(self, *args):
                return PlanProposal(tasks=[TaskProposal(agent_type='imaging', input_refs=['o'], questions=['分析'], why='有报告')])
        plan = MainAgent(Gateway()).plan(snapshot)
        self.assertTrue({'m', 'p'} <= set(plan.tasks[0].input_refs))
        plan.tasks[0].input_refs = ['o']
        self.assertIn('unassigned_image_assets', validate_plan(plan, snapshot))

    def test_executor_image_only_reaches_real_validation_gate(self):
        snapshot = self.snapshot('imaging'); snapshot.images = self.pair(); snapshot.observations = []
        snapshot.inventory = [InventoryItem(domain='imaging', availability='partial')]
        class Knowledge:
            def search_knowledge(self, request, deadline=None):
                return EvidenceBundle(bundle_id='b', request_id=request.request_id, status='empty', backend_status={'bm25': 'empty'})
        with patch('neurogra.clinical.diamond.model_profile', return_value=DiamondModelProfile(fingerprint='unverified', hashes={})):
            tool = DiamondTool(self.config, self.store, self.run)
        executor = Executor(self.store, self.run, Knowledge(), diamond=tool)
        executor.register('imaging', ImagingAgent())
        result = asyncio.run(executor.execute_tasks(fallback_plan(snapshot), snapshot))[0]
        self.assertEqual(result.status, 'partial')
        self.assertEqual(self.store.objects(self.run, 'diamond_result')[0]['payload']['status'], 'blocked_validation')


if __name__ == '__main__': unittest.main()
