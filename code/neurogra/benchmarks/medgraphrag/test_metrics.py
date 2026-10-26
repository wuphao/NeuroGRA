"""Metric regressions: abstention denominator and fixed-class macro averaging."""
from fractions import Fraction
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / 'code/tools'))
from evaluate_medgraphrag_labels import metrics


class MetricsTests(unittest.TestCase):
    def test_abstentions_are_false_negatives_not_excluded(self):
        rows = [{'reference_label': 'CN', 'predicted_class': 'CN'},
                {'reference_label': 'MCI', 'predicted_class': None},
                {'reference_label': 'AD', 'predicted_class': 'MCI'}]
        m = metrics(rows)
        self.assertEqual(m['evaluated'], 3)
        self.assertEqual(m['unresolved_predictions'], 1)
        self.assertEqual(m['accuracy'], 1/3)
        self.assertEqual(m['macro_f1'], 1/3)
        self.assertEqual(m['per_class']['MCI']['fn'], 1)
        self.assertEqual(m['per_class']['MCI']['fp'], 1)
        self.assertEqual(m['accuracy_among_mapped_only'], 0.5)

    def test_all_abstaining_has_zero_f1_and_no_selective_accuracy(self):
        m = metrics([{'reference_label': c, 'predicted_class': None} for c in ('CN', 'MCI', 'AD')])
        self.assertEqual(m['accuracy'], 0)
        self.assertEqual(m['macro_f1'], 0)
        self.assertIsNone(m['accuracy_among_mapped_only'])

    def test_macro_uses_three_fixed_classes_even_if_a_class_is_absent(self):
        m = metrics([{'reference_label': 'CN', 'predicted_class': 'CN'}])
        self.assertEqual(m['accuracy'], 1)
        self.assertEqual(m['macro_f1'], 1/3)
        with self.assertRaisesRegex(ValueError, 'no_known'):
            metrics([{'reference_label': None, 'predicted_class': 'CN'}])

    def test_27_case_matrix_matches_hand_calculated_fraction(self):
        counts = {('CN', 'CN'): 1, ('CN', 'MCI'): 1, ('CN', None): 1,
                  ('MCI', 'CN'): 2, ('MCI', 'MCI'): 7, ('MCI', None): 5,
                  ('AD', 'MCI'): 5, ('AD', 'AD'): 3, ('AD', None): 2}
        rows = [{'reference_label': t, 'predicted_class': p}
                for (t, p), n in counts.items() for _ in range(n)]
        m = metrics(rows)
        self.assertEqual(m['correct'], 11)
        self.assertEqual(m['evaluated'], 27)
        self.assertEqual(m['mapped_predictions'], 19)
        self.assertEqual(m['accuracy'], 11/27)
        expected = (Fraction(1, 3) + Fraction(14, 27) + Fraction(6, 13)) / 3
        self.assertAlmostEqual(m['macro_f1'], float(expected))


if __name__ == '__main__':
    unittest.main()
