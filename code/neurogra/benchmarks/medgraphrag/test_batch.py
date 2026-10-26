"""Batch orchestration regressions: isolation, provenance, context and resume."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / 'code/tools'))
import batch_medgraphrag_reports as batch
import medgraphrag_provenance as provenance
from .runtime import Config, LocalModel
from .test_adapter import patient


class BatchTests(unittest.TestCase):
    def test_unrelated_benchmark_changes_do_not_invalidate_adapter(self):
        with patch.object(provenance, 'adapter_fingerprints', return_value={'actual_adapter.py': 'same'}):
            provenance.verify_adapter_implementation({'actual_adapter.py': 'same', 'kgqa.py': 'old'})
            with self.assertRaisesRegex(ValueError, 'changed_since_run'):
                provenance.verify_adapter_implementation({'actual_adapter.py': 'changed'})
            with self.assertRaisesRegex(ValueError, 'not_recorded'):
                provenance.verify_adapter_implementation({'kgqa.py': 'old'})

    def fixture(self, root):
        source = root / 'patients'
        source.mkdir()
        numbers = ['041_S_5100', '041_S_4427']
        for number in numbers:
            raw = patient()
            raw['patient']['patient_number'] = number
            batch.write_json(source / f'patient_{number}_analysis.json', raw)
        listing = root / 'numbers.txt'
        listing.write_text('\n'.join(numbers), encoding='utf-8')
        knowledge = root / 'knowledge.json'
        batch.write_json(knowledge, {'documents': []})
        config = root / 'config.yaml'
        config.write_text('graph_backend: json\nknowledge_manifest: knowledge.json\n'
                          'answer_context_tokens: 65536\nanswer_max_input_chars: 48000\n', encoding='utf-8')
        return batch.parser().parse_args(['--patients-file', str(listing), '--source-dir', str(source),
            '--config', str(config), '--output-dir', str(root / 'batch')])

    def worker(self, command, **kwargs):
        patient_file = Path(command[command.index('--patient') + 1])
        output = Path(command[command.index('--output') + 1])
        output.mkdir()
        raw = batch.read(patient_file)
        prepared = batch.prepare_patient(raw)
        labels = {'cognitive_stage': 'MCI', 'etiology_label': 'UNRESOLVED', 'evaluation_class': 'MCI',
                  'assessment_status': 'insufficient_data'}
        data = {'status.json': {'status': 'prepared' if '--prepare-only' in command else 'completed'},
                'manifest.json': prepared['manifest'], 'source_map.json': prepared['sources'],
                'prediction.json': {'usage': {}, **labels}, 'labels.json': labels,
                'model_patient.json': {}, 'score_trends.json': {}, 'retrieval.json': {}}
        for name, value in data.items():
            batch.write_json(output / name, value)
        (output / 'report.md').write_text('synthetic only', encoding='utf-8')
        return subprocess.CompletedProcess(command, 0)

    def auditor(self, output):
        result = {'status': 'passed', 'labels': batch.read(output / 'labels.json')}
        batch.write_json(output / 'run_audit.json', result)
        return result

    def test_failure_isolated_retry_uses_new_attempt_and_completed_cases_skip(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'tmp') as temporary:
            args = self.fixture(Path(temporary))
            count = 0
            def flaky(command, **kwargs):
                nonlocal count
                count += 1
                if count == 1:
                    raise subprocess.TimeoutExpired(command, 1)
                return self.worker(command, **kwargs)
            with patch.object(batch.LocalModel, 'preflight', return_value={'local': 'same'}), \
                    patch.object(batch.subprocess, 'run', side_effect=flaky), \
                    patch.object(batch, 'audit', side_effect=self.auditor):
                self.assertEqual(batch.run_batch(args), 1)
                result = batch.read(args.output_dir / 'summary.json')
                self.assertEqual([r['status'] for r in result['patients']], ['timed_out', 'completed'])
            with patch.object(batch.LocalModel, 'preflight', return_value={'local': 'same'}), \
                    patch.object(batch.subprocess, 'run', side_effect=self.worker) as worker, \
                    patch.object(batch, 'audit', side_effect=self.auditor):
                self.assertEqual(batch.run_batch(args), 0)
                self.assertEqual(worker.call_count, 1)
                self.assertTrue((args.output_dir / 'cases/041_S_5100/attempt-0002/run/report.md').exists())
                worker.reset_mock()
                self.assertEqual(batch.run_batch(args), 0)
                worker.assert_not_called()
            self.assertTrue((args.output_dir / 'labels.csv').read_bytes().startswith(b'\xef\xbb\xbf'))

    def test_input_change_rejects_resume_before_service_call(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'tmp') as temporary:
            args = self.fixture(Path(temporary))
            args.prepare_only = True
            with patch.object(batch.subprocess, 'run', side_effect=self.worker):
                self.assertEqual(batch.run_batch(args), 0)
            source = args.source_dir / 'patient_041_S_5100_analysis.json'
            raw = batch.read(source)
            raw['forms']['mmse']['records'][0]['MMSE总分'] = 9
            batch.write_json(source, raw)
            args.prepare_only = False
            with patch.object(batch.LocalModel, 'preflight') as service:
                with self.assertRaisesRegex(ValueError, 'changed'):
                    batch.run_batch(args)
                service.assert_not_called()

    def test_corrupt_success_is_not_silently_skipped_or_overwritten(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'tmp') as temporary:
            args = self.fixture(Path(temporary))
            with patch.object(batch.LocalModel, 'preflight', return_value={}), \
                    patch.object(batch.subprocess, 'run', side_effect=self.worker) as worker, \
                    patch.object(batch, 'audit', side_effect=self.auditor):
                self.assertEqual(batch.run_batch(args), 0)
                path = args.output_dir / 'cases/041_S_5100/attempt-0001/run/report.md'
                path.write_text('changed', encoding='utf-8')
                worker.reset_mock()
                self.assertEqual(batch.run_batch(args), 1)
                worker.assert_not_called()
                self.assertEqual(batch.read(args.output_dir / 'summary.json')['patients'][0]['status'], 'integrity_failed')

    def test_finished_worker_is_recovered_after_parent_interruption(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'tmp') as temporary:
            args = self.fixture(Path(temporary))
            with patch.object(batch.LocalModel, 'preflight', return_value={}), \
                    patch.object(batch.subprocess, 'run', side_effect=self.worker) as worker, \
                    patch.object(batch, 'audit', side_effect=self.auditor):
                self.assertEqual(batch.run_batch(args), 0)
                path = args.output_dir / 'cases/041_S_5100/latest.json'
                receipt = batch.read(path)
                receipt.update(status='running', mode='run')
                receipt.pop('artifact_sha256')
                batch.write_json(path, receipt)
                worker.reset_mock()
                self.assertEqual(batch.run_batch(args), 0)
                worker.assert_not_called()
                self.assertEqual(batch.read(path)['status'], 'completed')

    def test_model_identity_change_blocks_mixing_runs(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'tmp') as temporary:
            args = self.fixture(Path(temporary))
            with patch.object(batch.LocalModel, 'preflight', return_value={'digest': 'old'}), \
                    patch.object(batch.subprocess, 'run', side_effect=self.worker), \
                    patch.object(batch, 'audit', side_effect=self.auditor):
                self.assertEqual(batch.run_batch(args), 0)
            with patch.object(batch.LocalModel, 'preflight', return_value={'digest': 'new'}), \
                    patch.object(batch.subprocess, 'run') as worker:
                with self.assertRaisesRegex(ValueError, 'model_identity_changed'):
                    batch.run_batch(args)
                worker.assert_not_called()

    def test_audit_failure_retains_patient_identity_and_continues_next_case(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'tmp') as temporary:
            args = self.fixture(Path(temporary))
            calls = 0
            def first_audit_fails(output):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise ValueError('synthetic audit failure')
                return self.auditor(output)
            with patch.object(batch.LocalModel, 'preflight', return_value={}), \
                    patch.object(batch.subprocess, 'run', side_effect=self.worker), \
                    patch.object(batch, 'audit', side_effect=first_audit_fails):
                self.assertEqual(batch.run_batch(args), 1)
            summary = batch.read(args.output_dir / 'summary.json')
            self.assertEqual([r['patient_number'] for r in summary['patients']], ['041_S_5100', '041_S_4427'])
            self.assertEqual([r['status'] for r in summary['patients']], ['failed', 'completed'])
            self.assertTrue(summary['patients'][0]['attempt'])

    def test_check_is_offline_and_sidecars_are_snapshotted(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'tmp') as temporary:
            root = Path(temporary)
            args = self.fixture(root)
            source = args.source_dir / 'patient_041_S_5100_analysis.json'
            batch.write_json(source.with_suffix('.source.manifest.json'), {'trim': 'synthetic'})
            batch.write_json(source.with_suffix('.masking.json'), {'masking': 'synthetic'})
            args.check = True
            with patch.object(batch.LocalModel, 'preflight') as service, patch.object(batch.subprocess, 'run') as worker:
                self.assertEqual(batch.run_batch(args), 0)
                service.assert_not_called()
                worker.assert_not_called()
            args.check, args.prepare_only = False, True
            with patch.object(batch.subprocess, 'run', side_effect=self.worker):
                self.assertEqual(batch.run_batch(args), 0)
            inputs = args.output_dir / 'cases/041_S_5100/attempt-0001/input'
            self.assertEqual(batch.read(inputs / source.with_suffix('.manifest.json').name), {'trim': 'synthetic'})
            self.assertEqual(batch.sha(source), batch.sha(inputs / source.name))
            self.assertTrue((inputs / source.with_suffix('.masking.json').name).exists())

    def test_answer_context_is_independent_of_extraction_and_part_of_cache_key(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'tmp') as temporary:
            cfg = Config(context_tokens=32768, output_tokens=8192, answer_context_tokens=65536,
                         answer_output_tokens=4096, answer_max_input_chars=48000)
            cfg.validate_local()
            model = LocalModel(cfg, temporary)
            response = {'message': {'content': 'done'}, 'done_reason': 'stop'}
            with patch.object(model, 'request', return_value=response) as request:
                model.generate('test', {}, 'reference_revision')
                self.assertEqual(request.call_args.args[1]['options']['num_ctx'], 65536)
                self.assertEqual(request.call_args.args[1]['options']['num_predict'], 4096)
                model.generate('test', {}, 'analyze:patient')
                self.assertEqual(request.call_args.args[1]['options']['num_ctx'], 32768)
                self.assertEqual(request.call_args.args[1]['options']['num_predict'], 8192)


if __name__ == '__main__':
    unittest.main()
