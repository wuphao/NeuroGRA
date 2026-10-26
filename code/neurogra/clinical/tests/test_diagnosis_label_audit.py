"""Reference comparisons must never mistake imported history for predictions."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / 'code/tools'))
from audit_diagnosis_labels import aggregate, compare_case, normalize_truth


def case(label=None, truth='MCI'):
    records = [{'record_id': 1, '患病情况': truth}]
    raw = {'forms': {'dicom_basic_info': {'records': records}}, 'demographics': {'患病情况': truth}}
    report = {'status': 'completed', 'run_id': 'run', 'summary': '既往诊断：MCI',
              'diagnostic_assessment': {'primary_label': label} if label else None}
    return compare_case('example', raw, report, {'records': [{'condition': truth}]}, {})


class AuditTests(unittest.TestCase):
    def test_imported_history_and_completed_status_not_predictions(self):
        row = case()
        self.assertIsNone(row['predicted_class'])
        self.assertIsNone(row['match'])
        self.assertEqual(row['prediction_status'], 'no_published_primary_label')
        self.assertEqual(aggregate([row])['correct_mapped_predictions'], 0)

    def test_exact_alias_mapping(self):
        self.assertTrue(case('MCI随访')['match'])
        self.assertTrue(case('认知正常随访', 'CN')['match'])
        self.assertTrue(case('阿尔茨海默病痴呆（AD Dementia）随访', 'AD')['match'])

    def test_does_not_map_negation_or_multiple_diseases_by_substring(self):
        row = case('不支持MCI，AD待排')
        self.assertEqual(row['prediction_status'], 'unmapped_label')
        self.assertIsNone(row['predicted_class'])

    def test_all_case_yield_and_selected_agreement_are_separate(self):
        result = aggregate([case('MCI随访'), case(), case('认知正常随访')])
        self.assertEqual(result['patients'], 3)
        self.assertEqual(result['correct_mapped_predictions'], 1)
        self.assertEqual(result['agreement_among_mapped_predictions'], 0.5)
        self.assertEqual(result['correct_label_yield_all_reference_cases'], 1 / 3)
        self.assertEqual(result['confusion_matrix']['MCI']['no_prediction'], 1)

    def test_unknown_and_conflicting_truth_not_silently_normalized(self):
        self.assertIsNone(normalize_truth([{'condition': 'MCI'}, {'condition': 'AD'}])[0])
        self.assertIsNone(normalize_truth([{'condition': 'AD/MCI'}])[0])
        self.assertIsNone(normalize_truth([])[0])
        result = aggregate([case('MCI随访', truth=None)])
        self.assertEqual(result['known_reference_labels'], 0)
        self.assertIsNone(result['correct_label_yield_all_reference_cases'])


if __name__ == '__main__':
    unittest.main()
