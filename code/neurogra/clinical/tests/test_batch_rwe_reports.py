"""Batch orchestration: failure isolation, resume and patient identity checks."""
import argparse
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / 'code/tools'))
import batch_rwe_reports as batch


class BatchTests(unittest.TestCase):
    def test_screenshot_list(self):
        numbers = batch.read_numbers(ROOT / 'configs/rwe_batch_patients.txt')
        self.assertEqual(len(numbers), 27)
        self.assertIn('941_S_6962', numbers)
        self.assertEqual(numbers[-1], '002_S_5018')

    def test_export_failure_does_not_stop_next_patient_and_resumes(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'tmp') as temporary:
            root = Path(temporary)
            patients = root / 'patients.txt'
            patients.write_text('041_S_5100\n041_S_4427\n', encoding='utf-8')
            args = argparse.Namespace(patients_file=patients, output_dir=root / 'batch',
                check=False, export_only=True, trim_mode='short', detail_years=[])
            def export(settings):
                if settings.patient_number == '041_S_5100':
                    raise RuntimeError('unavailable')
                batch.save(settings.output, {'patient': {'patient_number': settings.patient_number}})
            with patch.object(batch, 'export_patient', side_effect=export) as exporter, patch.object(batch, 'patient_json'):
                self.assertEqual(batch.batch(args), 1)
                summary = json.loads((args.output_dir / 'summary.json').read_text(encoding='utf-8'))
                self.assertEqual([r['status'] for r in summary['patients']], ['export_failed', 'exported'])
                lock_state = json.loads((args.output_dir / 'batch.lock').read_text(encoding='utf-8'))
                self.assertEqual(lock_state['status'], 'released')
                exporter.reset_mock()
                batch.batch(args)
                self.assertEqual(exporter.call_count, 1)
                self.assertEqual(exporter.call_args.args[0].patient_number, '041_S_5100')

    def test_mismatched_export_is_rejected(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'tmp') as temporary:
            path = Path(temporary) / 'patient.json'
            batch.save(path, {'patient': {'patient_number': '041_S_5100'}})
            with self.assertRaisesRegex(ValueError, '编号不匹配'):
                batch.patient_json(path, '041_S_4427')

    def test_modified_input_does_not_reuse_old_report(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'tmp') as temporary:
            directory = Path(temporary)
            path = directory / 'patient.json'
            path.write_text('{}', encoding='utf-8')
            batch.save(directory / 'result.json', {'patient_number': '041_S_5100', 'input_sha256': 'old'})
            with self.assertRaisesRegex(ValueError, '输入不一致'):
                batch.resumable_result(directory, '041_S_5100', path)

    def test_old_timestamp_file_does_not_block_restart(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'tmp') as temporary:
            directory = Path(temporary)
            (directory / 'batch.lock').write_text('2026-10-16T11:52:48+08:00', encoding='utf-8')
            with batch.batch_lock(directory):
                pass
            self.assertEqual(json.loads((directory / 'batch.lock').read_text(encoding='utf-8'))['status'], 'released')

    def child(self, directory, code):
        return subprocess.run([sys.executable, '-c',
            'import sys, os; from pathlib import Path; '
            f'sys.path.insert(0, {str(ROOT / "code/tools")!r}); '
            'from batch_rwe_reports import batch_lock, BatchAlreadyRunning\n' + code,
            str(directory)], capture_output=True, text=True, timeout=10)

    def test_real_process_cannot_take_active_lock_but_can_after_release(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'tmp') as temporary:
            directory = Path(temporary)
            code = ('try:\n'
                    '    with batch_lock(Path(sys.argv[1])): pass\n'
                    'except BatchAlreadyRunning:\n'
                    '    sys.exit(7)\n')
            with batch.batch_lock(directory):
                result = self.child(directory, code)
                self.assertEqual(result.returncode, 7, result.stderr)
            result = self.child(directory, code)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_abrupt_process_exit_releases_kernel_lock(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'tmp') as temporary:
            directory = Path(temporary)
            result = self.child(directory, 'with batch_lock(Path(sys.argv[1])): os._exit(17)\n')
            self.assertEqual(result.returncode, 17, result.stderr)
            with batch.batch_lock(directory):
                pass

    def test_exception_releases_lock(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'tmp') as temporary:
            directory = Path(temporary)
            with self.assertRaisesRegex(RuntimeError, 'simulated_failure'):
                with batch.batch_lock(directory):
                    raise RuntimeError('simulated_failure')
            with batch.batch_lock(directory):
                pass

    def test_duplicate_cli_has_clear_message_without_traceback(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'tmp') as temporary:
            directory = Path(temporary)
            with batch.batch_lock(directory):
                result = subprocess.run([sys.executable, str(Path(batch.__file__)),
                    '--output-dir', str(directory), '--export-only'],
                    capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 2)
                self.assertNotIn('Traceback', result.stderr)
                self.assertIn('已有批处理正在运行', result.stderr)

    def test_early_analysis_failure_has_explicit_technical_result(self):
        from neurogra.clinical.config import ClinicalConfig
        with tempfile.TemporaryDirectory(dir=ROOT / 'tmp') as temporary:
            root = Path(temporary)
            path = root / 'patient.json'
            path.write_text('{}', encoding='utf-8')
            config = ClinicalConfig(project_root=root)
            batch.record_worker_failure(path, root / 'report', 'example', config, RuntimeError('model_unavailable'), 'signature')
            result = batch.resumable_result(root / 'report', 'example', path, 'follow_up', 'signature')
            self.assertEqual(result['diagnosis_status'], 'technical_failure')
            report = json.loads((root / 'report/report.json').read_text(encoding='utf-8'))
            self.assertIsNone(report['diagnostic_outcome']['primary_working_label'])

    def test_existing_technical_failure_is_retried(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'tmp') as temporary:
            root = Path(temporary)
            patients = root / 'patients.txt'
            patients.write_text('041_S_5100\n', encoding='utf-8')
            directory = root / 'batch'
            batch.save(directory / 'patients/patient_041_S_5100_analysis.json', {})
            args = argparse.Namespace(patients_file=patients, output_dir=directory, check=False,
                export_only=False, trim_mode='short', detail_years=[], task_mode='follow_up', patient_timeout=1200)
            with patch.object(batch, 'patient_json'), patch.object(batch, 'resumable_result', side_effect=[
                    {'diagnosis_status': 'technical_failure'},
                    {'status': 'completed', 'diagnosis_status': 'conditional_diagnosis'}]), \
                    patch.object(batch.subprocess, 'run') as run:
                self.assertEqual(batch.batch(args), 0)
                run.assert_called_once()


if __name__ == '__main__':
    unittest.main()
