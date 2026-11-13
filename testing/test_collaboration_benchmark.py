import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from collaboration_benchmark import Plan, Selection, cases, run
from neurogra.platform.configuration import resolve_config


class BenchmarkIsolationTests(unittest.TestCase):
    def test_expected_answers_are_not_sent_to_any_model_adapter(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = resolve_config(root=root, profile='patient-only')
            seen = []

            def generate(_system, data, schema):
                serialized = json.dumps(data, ensure_ascii=False)
                seen.append(serialized)
                return Plan(domains=['history']) if schema is Plan else Selection(source_ids=['s0'])

            with patch('collaboration_benchmark.ModelGateway.generate_structured', side_effect=generate):
                for method in ('B0', 'B2', 'B3', 'B4'):
                    row = run(config, root, cases()[0], method, 0)
                    # B4 legitimately refuses the synthetic model's attempt to
                    # cite another worker's source. It must not leak the oracle.
                    self.assertIn(row['status'], {'completed', 'failed'})
            self.assertTrue(seen)
            for serialized in seen:
                self.assertNotIn('expected_ids', serialized)
                self.assertNotIn('exact_match', serialized)

    def test_invalid_plan_does_not_dispatch_a_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = resolve_config(root=root, profile='patient-only')
            with patch('collaboration_benchmark.ModelGateway.generate_structured',
                       return_value=SimpleNamespace(domains=['other-patient'])) as model:
                result = run(config, root, cases()[0], 'B2', 0)
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['detail'], 'invalid_domain_plan')
            model.assert_called_once()


if __name__ == '__main__':
    unittest.main()
