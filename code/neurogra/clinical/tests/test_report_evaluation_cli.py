"""Three-method orchestration with an isolated fake reviewer, no medical grading."""
import argparse
import csv
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / 'code/tools'))
import evaluate_reports as cli
from neurogra.clinical.report_evaluation import DIMENSIONS, Review


class CliTests(unittest.TestCase):
    def tearDown(self):
        from neurogra_common.llm_log import configure_llm_log
        configure_llm_log(None)

    def setup_study(self, root):
        reference = root / 'reference.json'
        cli.write(reference, {'checklist_status': 'draft', 'facts': [
            {'id': 'F1', 'text': '演示指标X为1', 'locator': {'json_pointer': '/X'},
             'critical': False, 'selection_reason': ''}], 'medical_sources': []})
        rows = []
        for method in ['method_A', 'method_B', 'method_C']:
            report = root / (method + '.md')
            report.write_text(method + '\n演示指标X为1。', encoding='utf-8')
            rows.append({'case_id': 'example_case', 'method': method, 'generation_model': 'generator',
                'report_path': str(report), 'reference_path': str(reference)})
        manifest = root / 'manifest.json'
        cli.write(manifest, {'reports': rows})
        study = root / 'study'
        cli.blind(argparse.Namespace(manifest=manifest, output=study, seed=1))
        return study

    def test_three_methods_and_independent_doctor_export(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'tmp') as temporary:
            study = self.setup_study(Path(temporary))
            args = argparse.Namespace(study=study, base_url='http://localhost:11434', model='reviewer',
                timeout=10, context_tokens=32768, check=False, batch_size=4, report_timeout=60)
            def generate(gateway, system, data, schema):
                self.assertNotIn('method', data)
                self.assertNotIn('generation_model', data)
                return Review(judgments=[{'unit_id': u['id'], 'dimension': dimension,
                    'quote': u['text'], 'verdict': 'not_applicable',
                    'basis': [{'id': 'F1', 'quote': '演示指标X为1'}],
                    'reason': '模拟流程检查', 'severity': 'none', 'handling': 'not_applicable'}
                    for u in data['target_units'] for dimension in DIMENSIONS])
            with patch('neurogra.clinical.llm.ModelGateway.generate_structured', new=generate):
                cli.run(args)
            summary = cli.read(study / 'summary.json')
            self.assertEqual(len(summary['aggregate']), 15)
            self.assertEqual({s['method'] for s in summary['aggregate']}, {'method_A', 'method_B', 'method_C'})
            self.assertTrue(all(s['reports_completed'] == 1 for s in summary['aggregate']))
            cli.review_sheet(argparse.Namespace(study=study, count=9, seed=1))
            doctor = study / 'human_review/doctor_independent.csv'
            with doctor.open(encoding='utf-8-sig', newline='') as handle:
                reader = csv.DictReader(handle)
                self.assertNotIn('auto_verdict', reader.fieldnames)
                rows = list(reader)
            self.assertTrue(rows)
            self.assertTrue(all(not r['human_verdict'] for r in rows))
            for row in rows:
                row.update(reviewer_id='test_reviewer', human_verdict='not_applicable', human_severity='none')
            cli.export_csv(doctor, rows, list(rows[0]))
            cli.compare(argparse.Namespace(study=study, sheet=doctor))
            compared = cli.read(study / 'human_comparison.json')
            self.assertEqual(compared['matched_items'], len(rows))
            self.assertTrue(all(r['agreement'] == 1 for r in compared['agreement']))

    def test_pending_groups_remain_visible_before_evaluation(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'tmp') as temporary:
            study = self.setup_study(Path(temporary))
            cli.summarize(study)
            summary = cli.read(study / 'summary.json')
            self.assertEqual(len(summary['aggregate']), 15)
            self.assertTrue(all(row['reports_pending'] == 1 for row in summary['aggregate']))


if __name__ == '__main__':
    unittest.main()
