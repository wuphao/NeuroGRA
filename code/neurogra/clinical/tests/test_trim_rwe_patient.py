import copy
import unittest

from neurogra.clinical.rwe_preprocessing import trim_patient


class TrimRwePatientTests(unittest.TestCase):
    def setUp(self):
        self.raw = {'schema_version': '2.0', 'patient': {'patient_number': 'synthetic'},
            'forms': {'mmse': {'form_name': 'MMSE量表', 'records': [
                {'record_id': 1, 'visit_date': '2013-03-25', 'MMSE总分': 0, '分项': '否'},
                {'record_id': 2, 'visit_date': '2018-07-17', 'MMSE总分': 20, '分项': '有'},
                {'record_id': 3, 'visit_date': '2026-02-13', 'MMSE总分': 30, '分项': '无'},
                {'record_id': 4, 'visit_date': None, 'MMSE总分': None, '分项': '未知'}]},
                'plasma_biomarkers': {'form_name': '血浆信息', 'records': [
                    {'record_id': 5, 'visit_date': '2024-03-06', '结果': 0.086}]}},
            'imaging': {'mri': {'path': ''}}, 'timeline': [{'redundant': True}],
            'data_quality': {'warnings': ['原始日期缺失'], 'missing_forms': []}}

    def test_short_retains_all_scores_and_selected_full_records_without_mutating_source(self):
        before = copy.deepcopy(self.raw)
        data, manifest = trim_patient(self.raw, 'short')
        rows = data['forms']['mmse']['records']
        self.assertEqual(rows[0], {'record_id': 1, 'visit_date': '2013-03-25', 'MMSE总分': 0})
        self.assertEqual(rows[1], {'record_id': 2, 'visit_date': '2018-07-17', 'MMSE总分': 20})
        self.assertEqual(rows[2:], self.raw['forms']['mmse']['records'][2:])
        self.assertEqual(self.raw, before)
        self.assertNotIn('timeline', data)
        self.assertEqual(data['forms']['plasma_biomarkers'], {**self.raw['forms']['plasma_biomarkers'], 'record_count': 1})
        self.assertEqual(data['imaging'], self.raw['imaging'])
        self.assertEqual(manifest['forms']['mmse']['rows'][0]['omitted_fields'], ['分项'])
        self.assertIn('患者资料子集', data['data_quality']['warnings'][-1])
        self.assertEqual(manifest['policy']['complete_detail_years'], [])

    def test_explicit_years_are_reusable_for_other_patients(self):
        self.raw['patient']['patient_number'] = 'another-patient'
        self.raw['forms']['mmse']['records'][1]['visit_date'] = '2020-06-01'
        data, manifest = trim_patient(self.raw, 'short', complete_detail_years=[2020])
        self.assertEqual(data['forms']['mmse']['records'][1], self.raw['forms']['mmse']['records'][1])
        self.assertEqual(manifest['policy']['complete_detail_years'], [2020])

    def test_remarks_removed_from_all_views_without_changing_clinical_notes(self):
        self.raw['forms']['mmse']['records'][2].update({'备注': 'remove', '检查备注说明': 'retain'})
        self.raw['demographics'] = {'Remarks': 'remove', '年龄': 70}
        data, manifest = trim_patient(self.raw, 'none')
        self.assertNotIn('备注', data['forms']['mmse']['records'][2])
        self.assertEqual(data['forms']['mmse']['records'][2]['检查备注说明'], 'retain')
        self.assertEqual(data['demographics'], {'年龄': 70})
        self.assertIn('timeline', data)
        self.assertIn('/forms/mmse/records/2/备注', manifest['removed_note_fields'])
        self.assertIn('备注', manifest['forms']['mmse']['rows'][2]['omitted_fields'])
        self.assertIn('备注', self.raw['forms']['mmse']['records'][2])

    def test_invalid_date_is_preserved_and_no_known_score_means_no_guess(self):
        row = self.raw['forms']['mmse']['records'][0]
        row['visit_date'] = 'not-a-date'
        data, _ = trim_patient(self.raw, 'smoke')
        self.assertEqual(data['forms']['mmse']['records'][0], row)

    def test_reject_incompatible_year_options(self):
        with self.assertRaisesRegex(ValueError, 'detail_years_require_short_mode'):
            trim_patient(self.raw, 'smoke', complete_detail_years=[2018])
        with self.assertRaisesRegex(ValueError, 'invalid_complete_detail_year'):
            trim_patient(self.raw, 'short', complete_detail_years=[10000])

    def test_smoke_preserves_latest_and_unknown_dates_and_updates_counts(self):
        data, manifest = trim_patient(self.raw, 'smoke')
        self.assertEqual(data['forms']['mmse']['records'], self.raw['forms']['mmse']['records'][2:])
        self.assertEqual(data['forms']['mmse']['record_count'], 2)
        self.assertEqual(data['data_quality']['record_counts']['MMSE量表'], 2)
        self.assertEqual(data['data_quality']['warnings'][0], '原始日期缺失')
        self.assertEqual(manifest['forms']['mmse']['rows'][0]['action'], 'omitted_visit')

    def test_missing_total_is_not_calculated_or_fabricated(self):
        self.raw['forms']['mmse']['records'][0].pop('MMSE总分')
        data, _ = trim_patient(self.raw, 'short')
        self.assertNotIn('MMSE总分', data['forms']['mmse']['records'][0])

    def test_duplicate_latest_date_records_are_not_deduplicated(self):
        self.raw['forms']['mmse']['records'].append(
            {'record_id': 6, 'visit_date': '2026-02-13', 'MMSE总分': 28})
        data, _ = trim_patient(self.raw, 'smoke')
        self.assertEqual([r['record_id'] for r in data['forms']['mmse']['records']], [3, 4, 6])


if __name__ == '__main__':
    unittest.main()
