import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from neurogra.platform.application import Application
from neurogra.platform.configuration import resolve_config
from neurogra.platform.worker import Worker


class ImportAndBatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.app = Application(self.root, resolve_config(root=self.root, profile='patient-only'))

    def test_rwe_import_uses_existing_exporter_and_retains_source(self):
        source = self.root / 'data/clinical/rwe/synthetic/source.json'
        source.parent.mkdir(parents=True)
        source.write_text('{"synthetic":true}', encoding='utf-8')
        with patch('neurogra.platform.knowledge.rwe_readiness', return_value={'ready': True}):
            job = self.app.submit_rwe('synthetic-rwe')
        with patch('neurogra.clinical.rwe.export_patient', return_value=(
                {'患者ID': 'synthetic-rwe', '病史': '合成导入资料'},
                {'export_id': 'synthetic-export', 'issues': [{'code': 'one_form_unavailable'}]}, source)):
            Worker(self.root).run_once()
        result = self.app.job(job['job_id'])
        attempt = result['attempts'][-1]
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(attempt['result']['execution_status'], 'partial')
        self.assertEqual(attempt['artifacts'][0]['kind'], 'source_json')
        imported = self.app.case_version(attempt['result']['version_id'])
        self.assertEqual(imported['source']['export_id'], 'synthetic-export')

    def test_batch_jobs_share_single_case_queue_and_keep_failed_items(self):
        versions = [self.app.import_case({'患者ID': f'synthetic-{i}', '病史': '合成资料'})['version_id'] for i in range(2)]
        with patch('neurogra.platform.application.inspect_environment', return_value={'ready': True, 'checks': []}):
            batch = self.app.submit_batch(versions)
        calls = []
        def execute(attempt, check):
            calls.append(attempt['attempt_id'])
            if len(calls) == 1:
                raise ValueError('synthetic-failure')
            return {'execution_status': 'partial'}
        worker = Worker(self.root, executor=execute)
        self.assertTrue(worker.run_once())
        self.assertTrue(worker.run_once())
        result = self.app.batch(batch['batch_id'])
        self.assertEqual(result['counts'], {'failed': 1, 'succeeded': 1})
        self.assertEqual(result['not_submitted'], [])
