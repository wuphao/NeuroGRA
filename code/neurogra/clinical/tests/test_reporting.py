import json
from pathlib import Path
import re
import tempfile
import unittest

from neurogra.clinical.reporting import render_report, render_data_appendix, render_saved_report, primary_diagnosis
from neurogra.clinical.schemas import (
    CaseSnapshot, Claim, EvidenceItem, FinalReport, ImageAsset, Observation, TaskPlan, TimeValue,
)
from neurogra.clinical.service import PreparationResult


class ReportingTests(unittest.TestCase):
    def setUp(self):
        self.observations = [self.observation('obs_score', 'MMSE总分', '0', '2024-03-06',
                                              context={'名称': 'MMSE', '版本': '原记录版本'}),
                             self.observation('obs_diagnosis', '患病情况', 'MCI', domain='history')]
        self.snapshot = CaseSnapshot(case_id='case', patient_id='p', raw_input_hash='h', records=[],
                                     observations=self.observations)
        self.observations = self.snapshot.observations
        self.preparation = PreparationResult(run_id='a' * 32, status='prepared', snapshot=self.snapshot,
            plan=TaskPlan(tasks=[]), knowledge_release_id=None, issues=[], artifact_path='preparation.json')
        self.report = FinalReport(patient_id='p', run_id='a' * 32, status='partial',
            assessment_status='unresolved', summary='尚不能确认当前诊断。', claims=[], unresolved=[], citations=[])

    def observation(self, identifier, name, value, date=None, domain='cognition', context=None, status='observed'):
        return Observation(observation_id=identifier, name=name, value=value, quote=value,
            source_refs=['record_' + identifier], domains=[domain], status=status,
            time=TimeValue(raw=date, value=date, precision='day' if date else 'unknown'), context=context or {})

    def render(self, results=None, evidence=None):
        return render_report(self.report, self.preparation, results or [], evidence or [], {}, 'model_selection')

    def test_fixed_sections_match_local_final_template(self):
        self.assertEqual(re.findall(r'^## (.+)$', self.render(), re.M), ['一、患者基本信息', '二、病史', '三、辅助检查', '四、诊断结论'])
        self.assertIn('[完整检查记录](./data_appendix.md)', self.render())
        self.assertNotIn('{{临床正文}}', self.render())

    def test_score_zero_and_unknown_binary_codes_are_not_reinterpreted(self):
        self.observations.append(self.observation('obs_unknown', '未知编码', '1', domain='laboratory'))
        text = self.render()
        score_row = next(s for s in text.splitlines() if s.startswith('| MMSE |'))
        self.assertIn('| 0 |', score_row)
        self.assertNotIn('否', score_row)
        self.assertIn('| 1 |', text)

    def test_undated_score_does_not_replace_latest_dated_score(self):
        self.observations.append(self.observation('obs_undated', 'MMSE总分', '30', context={'名称': 'MMSE'}))
        summary = self.render().split('诊断结论')[1]
        self.assertIn('MMSE：0', summary)
        self.assertNotIn('MMSE：30', summary)
        self.assertNotIn('日期未提供', self.render())

    def test_conflicting_scores_survive_same_table_cell(self):
        self.observations.append(self.observation('obs_conflict', 'MMSE总分', '1', '2024-03-06', context={'名称': 'MMSE'}))
        self.snapshot.conflict_groups = [['obs_score', 'obs_conflict']]
        text = self.render()
        self.assertIn('0<br>1', text)
        self.assertIn('同次记录存在不同结果', text)

    def test_missing_data_and_historical_diagnosis_are_explicit(self):
        self.observations.append(self.observation('obs_missing', '检验甲', '0', domain='laboratory', status='not_performed'))
        text = self.render()
        self.assertIn('既往诊断：轻度认知障碍（MCI）', text)
        self.assertNotIn('检验甲', text)
        self.assertNotIn('已确诊MCI', text)
        self.assertNotIn('未全部完成', text)
        self.assertNotIn('影像检查', text)

    def test_body_only_cites_medical_knowledge_and_appendix_keeps_patient_provenance(self):
        evidence = EvidenceItem(evidence_id='evidence_k', document_id='d', span_id='s', title='知识依据',
                                quote='参考内容', version='1', matched_by=[])
        self.report.claims = [Claim(claim_id='claim_c', text='条件性意见', kind='interpretation', level='cognition',
            observation_ids=['obs_score'], evidence_ids=['evidence_k'], strength='conditional',
            rationale='依据obs_score', limitations=['缺少教育信息'])]
        text = self.render(evidence=[evidence])
        appendix = render_data_appendix(self.report, self.preparation, [], [evidence])
        self.assertNotRegex(text, r'\[\^[sk]\d+\]')
        self.assertNotIn('| 来源 |', text)
        self.assertIn('id="source-s1"', appendix)
        self.assertIn('id="source-k1"', appendix)
        self.assertIn('条件性意见', text)
        self.assertNotIn('缺少教育信息', text)
        self.assertNotIn('obs_score', text)
        self.assertIn('obs\\_score', appendix)
        self.assertIn('<sup><a href="#reference-1">[1]</a></sup>', text)
        self.assertIn('<a id="reference-1"></a>', text)
        self.assertIn('[1] 知识依据；版本：1。', text)

    def test_knowledge_citations_reuse_number_and_exclude_unused_retrievals(self):
        used = EvidenceItem(evidence_id='evidence_used', document_id='d', span_id='s', title='指南甲',
                            quote='原文', version='2024', page=12, matched_by=[])
        unused = used.model_copy(update={'evidence_id': 'evidence_unused', 'title': '未采用指南'})
        self.report.claims = [Claim(claim_id=f'claim_{i}', text=f'有依据的解释{i}', kind='interpretation',
            level='cognition', observation_ids=['obs_score'], evidence_ids=['evidence_used'],
            strength='conditional') for i in range(2)]
        text = self.render(evidence=[unused, used])
        self.assertGreaterEqual(text.count('<sup><a href="#reference-1">[1]</a></sup>'), 2)
        self.assertEqual(text.count('<a id="reference-1"></a>'), 1)
        self.assertNotIn('reference-2', text)
        self.assertNotIn('未采用指南', text)
        self.assertIn('第 12 页', text)
        self.assertIn('source-k2', text)

    def test_missing_knowledge_reference_is_explicit_and_never_fabricated(self):
        self.report.claims = [Claim(claim_id='claim_missing', text='待核实意见', kind='interpretation',
            level='cognition', observation_ids=['obs_score'], evidence_ids=['evidence_missing'],
            strength='tentative')]
        text = self.render()
        self.assertNotIn('待核实意见', text)
        self.assertNotIn('### 参考资料', text)
        self.assertNotIn('<sup>', text)

    def test_source_text_cannot_break_tables_or_inject_headings(self):
        self.observations[0].quote = '0 | 1\n## 假标题<script>alert(1)</script>'
        text = self.render()
        self.assertNotIn('<script>', text)
        self.assertNotIn('\n## 假标题', text)
        self.assertIn('0 \\| 1', text)

    def test_all_observations_and_omitted_labs_are_retained_in_appendix(self):
        for i in range(25):
            self.observations.append(self.observation(f'obs_lab{i}', f'检验{i}', str(i), domain='laboratory'))
        text = self.render()
        appendix = render_data_appendix(self.report, self.preparation, [], [])
        self.assertIn('检验24', text)
        self.assertEqual(len(re.findall(r'<a id="source-s\d+"', appendix)), len(self.observations))
        self.assertIn('检验24', appendix)

    def test_failed_narrative_does_not_break_template(self):
        self.report.natural_language_report = '不应进入正文的失败摘要'
        self.report.narrative_status = 'unavailable'
        text = self.render()
        self.assertNotIn('不应进入正文', text)
        self.assertIn('诊断结论', text)
        self.assertIn('MCI', text)

    def test_registered_image_without_path_is_not_reported_as_analyzed(self):
        self.snapshot.images = [ImageAsset(asset_id='image_mri', modality='MRI', status='path_pending_manual_input')]
        text = self.render()
        self.assertNotIn('等待提供影像路径', text)
        self.assertNotIn('未提供可读取路径', text)
        self.assertNotIn('已完成MRI分析', text)

    def test_complaint_and_exam_are_separate_from_history_and_keep_sources(self):
        self.observations.extend([
            self.observation('obs_complaint', '主诉', '记忆下降两年', '2024-03-06', domain='history'),
            self.observation('obs_exam', '血压', '150/95 mmHg', '2024-03-06', domain='history'),
            self.observation('obs_past', '高血压病史', '有', domain='history')])
        text = self.render()
        complaint = text.split('## 二、主诉')[1].split('## 三、病史')[0]
        physical = text.split('## 四、体格检查')[1].split('## 五、辅助检查')[0]
        self.assertIn('记忆下降两年', complaint)
        self.assertIn('150/95 mmHg', physical)
        self.assertNotIn('高血压病史', physical)
        self.assertNotIn('[^s', complaint)
        self.assertNotIn('[^s', physical)

    def test_absent_complaint_and_exam_do_not_become_normal_findings(self):
        text = self.render()
        self.assertNotIn('## 二、主诉', text)
        self.assertNotIn('## 四、体格检查', text)
        self.assertNotIn('神经系统检查正常', text)

    def test_unknown_fields_and_technical_summaries_are_omitted(self):
        self.observations.extend([
            self.observation('obs_birth', '出生日期', '未提供', status='not_recorded'),
            self.observation('obs_history', '家族史', '不详', domain='history')])
        self.report.summary = '资料不足，未完成分析'
        self.report.natural_language_report = '本次自动复查未完成，综合判断仍待核实。'
        self.report.narrative_status = 'generated'
        self.report.narrative_method = 'model_verified'
        self.report.narrative_source_ids = ['obs_score']
        text = self.render()
        for phrase in ('出生日期', '家族史', '资料不足', '未提供', '自动复查', '日期未提供', '任务状态'):
            self.assertNotIn(phrase, text)

    def test_moca_total_is_visible_and_names_are_readable(self):
        self.observations.extend([
            self.observation('obs_moca', 'MoCA 汇总得分', '25', '2024-03-06'),
            self.observation('obs_adas', 'ADAS-Cog 11 项总分', '4.67', '2024-03-06', context={'名称': 'ADAS量表'})])
        text = self.render()
        self.assertIn('| MoCA | 2024-03-06 | 25 |', text)
        self.assertIn('| ADAS-Cog 11 项 |', text)
        self.assertNotIn('项ADAS', text)

    def test_explicit_supported_diagnosis_label_has_priority_over_history(self):
        evidence = EvidenceItem(evidence_id='evidence_d', document_id='d', span_id='s', title='指南',
                                quote='依据', version='2024', matched_by=[],
                                applicability='applicable', allowed_use='conditional_support')
        claim = Claim(claim_id='claim_d', text='综合评估倾向轻度认知障碍。', diagnosis_label='轻度认知障碍',
                      kind='interpretation', level='cognition', observation_ids=['obs_score'],
                      evidence_ids=['evidence_d'], strength='conditional')
        self.report.claims = [claim]
        self.report.assessment_status = 'conditional'
        selected = primary_diagnosis(self.report, self.snapshot, [evidence])
        self.assertEqual(selected['basis'], 'current_tendency')
        text = self.render(evidence=[evidence])
        self.assertIn('**首选诊断倾向：轻度认知障碍**', text)
        self.assertIn('<sup><a href="#reference-1">[1]</a></sup>', text)
        self.report.assessment_status = 'unresolved'
        self.assertEqual(primary_diagnosis(self.report, self.snapshot, [evidence])['basis'], 'historical_record')
        self.report.assessment_status = 'conditional'
        evidence.applicability = 'unknown'
        self.assertEqual(primary_diagnosis(self.report, self.snapshot, [evidence])['basis'], 'historical_record')

    def test_diagnosis_cannot_be_invented_from_scores_or_an_unvalidated_label(self):
        self.snapshot.observations = self.observations[:1]
        self.assertIsNone(primary_diagnosis(self.report, self.snapshot, []))
        from neurogra.clinical.history import check_claims
        bad = Claim(claim_id='claim_bad', text='MMSE总分0', diagnosis_label='阿尔茨海默病',
                    kind='description', level='cognition', observation_ids=['obs_score'], strength='descriptive')
        with self.assertRaisesRegex(ValueError, 'diagnosis_label_requires_explicit_interpretation'):
            check_claims([bad], self.snapshot, [])

    def test_saved_report_render_is_offline_preserves_sources_and_rejects_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, output = Path(tmp) / 'source', Path(tmp) / 'new'
            source.mkdir()
            original = self.report.model_dump_json()
            (source / 'report.json').write_text(original, encoding='utf-8')
            provenance = {'patient_id': 'p', 'run_id': self.report.run_id, 'records': [],
                'observations': [o.model_dump(mode='json') for o in self.observations],
                'conflict_groups': [], 'tasks': self.preparation.plan.model_dump(mode='json'), 'evidence': [], 'images': []}
            (source / 'provenance.json').write_text(json.dumps(provenance), encoding='utf-8')
            (source / 'audit.md').write_text('原始执行日志', encoding='utf-8')
            render_saved_report(source, output)
            for name in ('report.md', 'report.json', 'provenance.json', 'data_appendix.md', 'audit.md'):
                self.assertTrue((output / name).is_file())
            self.assertIn('原始执行日志', (output / 'audit.md').read_text(encoding='utf-8'))
            self.assertEqual((source / 'report.json').read_text(encoding='utf-8'), original)
            self.assertEqual(json.loads((output / 'provenance.json').read_text(encoding='utf-8'))['observations'], provenance['observations'])
            with self.assertRaisesRegex(ValueError, 'must_be_new'):
                render_saved_report(source, output)
            provenance.pop('images')
            (source / 'provenance.json').write_text(json.dumps(provenance), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'requires_preparation'):
                render_saved_report(source, Path(tmp) / 'legacy')


if __name__ == '__main__':
    unittest.main()
