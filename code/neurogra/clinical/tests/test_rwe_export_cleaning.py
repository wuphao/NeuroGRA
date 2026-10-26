"""Decode confirmed categorical codes without changing clinical measurements."""
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[4]
SPEC = importlib.util.spec_from_file_location('rwe_cleaning_test_exporter', ROOT / 'code/tools/export_rwe_patient.py')
EXPORTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EXPORTER)


class ExportCleaningTests(unittest.TestCase):
    def test_all_confirmed_binary_fields(self):
        for field, labels in EXPORTER.BINARY_FIELD_LABELS['moca'].items():
            for code, expected in ((0, labels[0]), ('0', labels[0]),
                                   (1, labels[1]), (' 1 ', labels[1])):
                with self.subTest(field=field, code=code):
                    record = EXPORTER.clean_record({field: code}, None, 'moca')
                    self.assertEqual(record[field], expected)

    def test_scores_counts_missing_and_unknown_values_are_preserved(self):
        original = {'MoCA 汇总得分': 1, '总分': 0, '检验值': 0.1,
                    '看图说出犀牛名称': None, '评估指针': 2,
                    '评估时钟数字': '', '时钟轮廓是否正确': '正确'}
        expected = {**original, '评估时钟数字': None}
        self.assertEqual(EXPORTER.clean_record(original, None, 'moca'), expected)
        self.assertEqual(original['评估时钟数字'], '')
        field = '受试者经过多少次尝试才学会这三个词'
        self.assertEqual(EXPORTER.clean_record({field: 1}, None, 'mmse')[field], 1)

    def test_field_mapping_is_scoped_to_form(self):
        record = EXPORTER.clean_record({'看图说出犀牛名称': 1}, None, 'unknown')
        self.assertEqual(record['看图说出犀牛名称'], 1)

    def test_timeline_uses_same_decoded_values(self):
        record = EXPORTER.clean_record({'id': 1, '访视日期': '2025-01-01',
            '看图说出犀牛名称': '0'}, '访视日期', 'moca')
        timeline = EXPORTER.build_timeline({'moca': {'records': [record]}})
        self.assertEqual(timeline[0]['assessments']['moca']['看图说出犀牛名称'], '错误')

    def test_remark_columns_are_removed_before_timeline_generation(self):
        record = EXPORTER.clean_record({'id': 1, '访视日期': '2025-01-01',
            '备注': 'remove', 'comment': 'remove', 'Remarks': 'remove',
            'MMSE总分': 0, '检查备注说明': 'retain'}, '访视日期', 'mmse')
        self.assertEqual(record, {'record_id': 1, 'visit_date': '2025-01-01',
                                 'MMSE总分': 0, '检查备注说明': 'retain'})
        timeline = EXPORTER.build_timeline({'mmse': {'records': [record]}})
        self.assertNotIn('备注', timeline[0]['assessments']['mmse'])


if __name__ == '__main__':
    unittest.main()
