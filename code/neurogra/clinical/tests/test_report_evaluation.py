"""Rule invariants, without a model or the generating system's retrieval sources."""
import unittest

from neurogra.clinical.report_evaluation import (
    DIMENSIONS, Coverage, Review, agreement, blind_reports, evaluate, metrics,
    sample_review, units, validate_coverage, validate_reference, validate_review,
)


def reference():
    return {'checklist_status': 'reviewed', 'checklist_timing': 'retrospective',
        'facts': [{'id': 'F0001', 'text': '2020年MMSE总分24分', 'locator': {'json_pointer': '/mmse'},
                   'critical': True, 'selection_reason': '主要认知测量'}],
        'medical_sources': []}


def review(text='MMSE为24分。'):
    labels = ['correct', 'source_not_provided', 'unknown', 'consistent']
    return Review(judgments=[{
        'unit_id': 'U0001', 'dimension': d, 'quote': text, 'verdict': label,
        'basis': [{'id': 'F0001', 'quote': 'MMSE总分24分'}], 'reason': '核对原始记录',
        'severity': 'none', 'handling': 'not_applicable'} for d, label in zip(DIMENSIONS, labels)])


class EvaluationTests(unittest.TestCase):
    def test_missing_sources_not_medical_errors(self):
        rows = [j.model_dump() for j in review().judgments]
        result = metrics(rows, [], False)
        self.assertEqual(result['medical_support']['problem_units'], 0)
        self.assertIsNone(result['medical_support']['problem_rate_among_evaluable'])
        self.assertEqual(result['medical_support']['unknown_or_missing_source_units'], 1)
        self.assertIsNone(result['key_evidence_completeness']['coverage_rate'])

    def test_fabricated_report_or_basis_quotes_are_rejected(self):
        for attribute in ['quote', 'basis']:
            result = review()
            if attribute == 'quote':
                result.judgments[0].quote = 'MMSE为30分。'
            else:
                result.judgments[0].basis[0].quote = 'MMSE总分30分'
            with self.assertRaises(ValueError):
                validate_review(result, units('MMSE为24分。'), reference(), units('MMSE为24分。'))

    def test_all_four_dimensions_required(self):
        result = review()
        result.judgments.pop()
        with self.assertRaisesRegex(ValueError, 'missing_or_duplicate'):
            validate_review(result, units('MMSE为24分。'), reference(), units('MMSE为24分。'))

    def test_no_source_cannot_be_unsupported(self):
        result = review()
        result.judgments[1].verdict = 'unsupported'
        result.judgments[1].severity = 'major'
        with self.assertRaisesRegex(ValueError, 'original_source'):
            validate_review(result, units('MMSE为24分。'), reference(), units('MMSE为24分。'))

    def test_independent_source_does_not_prove_report_cited_it(self):
        ref = reference()
        ref['medical_sources'] = [{'id': 'M1', 'text': '原始医学依据', 'locator': {'doi': 'example'},
                                   'citation_keys': ['未被报告引用的标题']}]
        result = review()
        result.judgments[1].verdict = 'supported'
        result.judgments[1].basis = [{'id': 'M1', 'quote': '原始医学依据'}]
        with self.assertRaisesRegex(ValueError, 'original_source'):
            validate_review(result, units('MMSE为24分。'), ref, units('MMSE为24分。'))

    def test_unknown_not_given_error_severity(self):
        result = review()
        result.judgments[2].severity = 'major'
        with self.assertRaisesRegex(ValueError, 'severity'):
            validate_review(result, units('MMSE为24分。'), reference(), units('MMSE为24分。'))

    def test_omission_has_no_fabricated_quote(self):
        coverage = Coverage(items=[{'fact_id': 'F0001', 'verdict': 'omitted', 'quote': '',
            'basis_quote': 'MMSE总分24分', 'reason': '报告只报告了其他测量'}])
        validate_coverage(coverage, reference()['facts'], '其他测量正常。')
        coverage.items[0].quote = '其他测量正常。'
        with self.assertRaisesRegex(ValueError, 'omission'):
            validate_coverage(coverage, reference()['facts'], '其他测量正常。')

    def test_reference_requires_independent_important_fact_selection(self):
        ref = reference()
        ref['facts'][0]['critical'] = False
        with self.assertRaisesRegex(ValueError, 'selected_facts'):
            validate_reference(ref)

    def test_method_mapping_not_in_public_packet_and_short_name_preserves_AD(self):
        entries = [{'case_id': '041_S_5100', 'method': 'A', 'text': 'A\n患者041_S_5100考虑AD。',
                    'generation_model': 'qwen3.8:27b', 'reference': reference()}]
        public, private = blind_reports(entries, 1)
        self.assertNotIn('method', public[0])
        self.assertNotIn('generation_model', public[0])
        self.assertNotIn('041_S_5100', public[0]['text'])
        self.assertIn('AD', public[0]['text'])
        self.assertEqual(private[0]['method'], 'A')

    def test_mocked_end_to_end_includes_fifth_dimension(self):
        class Gateway:
            def generate_structured(self, system, data, schema):
                if schema is Review:
                    return review()
                return Coverage(items=[{'fact_id': 'F0001', 'verdict': 'covered',
                    'quote': 'MMSE为24分。', 'basis_quote': 'MMSE总分24分', 'reason': '数值一致'}])
        packet = {'blind_id': 'B0001', 'case_id': 'C0001', 'text': 'MMSE为24分。', 'reference': reference()}
        result = evaluate(packet, Gateway())
        self.assertEqual(result['label'], '自动评价结果')
        self.assertEqual(len(result['metrics']), 5)
        self.assertEqual(result['metrics']['key_evidence_completeness']['coverage_rate'], 1)

    def test_draft_reference_skips_completeness_instead_of_scoring_zero(self):
        class Gateway:
            def generate_structured(self, system, data, schema):
                self.assert_schema = schema
                if schema is not Review:
                    raise AssertionError('draft facts must not be used as finalized checklist')
                return review()
        ref = reference()
        ref['checklist_status'] = 'draft'
        result = evaluate({'blind_id': 'B1', 'case_id': 'C1', 'text': 'MMSE为24分。', 'reference': ref}, Gateway())
        self.assertFalse(result['metrics']['key_evidence_completeness']['ready'])
        self.assertIsNone(result['metrics']['key_evidence_completeness']['coverage_rate'])

    def test_sampling_covers_three_groups_and_issue_levels(self):
        results, mapping = [], []
        for method in ['A', 'B', 'C']:
            for severity in ['none', 'major', 'critical']:
                key = method + severity
                results.append({'blind_id': key, 'status': 'completed', 'judgments': [{'severity': severity}]})
                mapping.append({'blind_id': key, 'method': method})
        selected = sample_review(results, mapping, 9)
        self.assertEqual(len(selected), 9)
        self.assertEqual({m['method'] for m in mapping if m['blind_id'] in selected}, {'A', 'B', 'C'})

    def test_agreement_empty_and_single_class(self):
        self.assertIsNone(agreement([])['agreement'])
        self.assertIsNone(agreement([('correct', 'correct')])['cohen_kappa'])
        self.assertEqual(agreement([('correct', 'correct'), ('error', 'error')])['cohen_kappa'], 1)


if __name__ == '__main__':
    unittest.main()
