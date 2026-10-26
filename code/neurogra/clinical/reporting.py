"""Source-preserving rendering of the project report template (no model calls)."""
from datetime import datetime
from importlib.resources import files
import json
import re

TEMPLATE_VERSION = '2.2'
ROLES = {'history': '病史', 'cognition': '认知与功能', 'laboratory': '检验', 'imaging': '影像'}
STATUS = {'observed': '已记录', 'present': '有', 'absent': '无', 'not_recorded': '未提供',
          'not_performed': '未检查', 'uncertain': '不确定', 'conflicting': '记录冲突'}
TASK_STATUS = {'completed': '已完成', 'partial': '部分完成', 'failed': '未成功完成',
               'skipped_no_data': '无可用资料，未分析'}


def cell(value):
    """Escape arbitrary source text so it cannot create Markdown structure."""
    text = '' if value is None else str(value)
    text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    for token in ('\\', '`', '*', '_', '[', ']', '|', '#', '~'):
        text = text.replace(token, '\\' + token)
    return text.replace('\r\n', '\n').replace('\r', '\n').replace('\n', '<br>')


def table(headers, rows):
    return '\n'.join(['| ' + ' | '.join(headers) + ' |',
                      '| ' + ' | '.join('---' for _ in headers) + ' |',
                      *['| ' + ' | '.join(row) + ' |' for row in rows]])


def date_text(row):
    return row.time.raw or row.time.value or '日期未提供'


def form_name(row):
    return str(row.context.get('名称') or row.context.get('量表名称') or
               row.context.get('表格名称') or '')


def label(row):
    return ' / '.join(dict.fromkeys(filter(None, (form_name(row), row.name))))


def result_text(row):
    # Binary values are already decoded by intake/export using field metadata.
    # Scores and unknown codes must never undergo a blanket 0/1 substitution.
    if row.status in {'not_recorded', 'not_performed'}:
        return STATUS[row.status]
    unit = row.unit or row.context.get('单位')
    text = row.quote or json.dumps(row.value, ensure_ascii=False)
    return text + (f'（单位：{unit}）' if unit else '')


def ordered(rows):
    return sorted(rows, key=lambda o: (o.time.value or o.time.raw or '9999', label(o)))


def is_total(row):
    return 'cognition' in row.domains and any(x in row.name for x in ('总分', '汇总得分', '总评分', 'total score'))


def observed(row):
    return row.status not in {'not_recorded', 'not_performed'}


class ReportView:
    def __init__(self, report, preparation, results, evidence):
        self.report, self.snapshot, self.results = report, preparation.snapshot, results
        self.evidence = evidence
        self.obs = self.snapshot.observations
        self.by_id = {o.observation_id: o for o in self.obs}
        self.refs = {o.observation_id: f's{i}' for i, o in enumerate(self.obs, 1)}
        self.refs.update({e.evidence_id: f'k{i}' for i, e in enumerate(evidence, 1)})
        self.used = {}
        self.knowledge_numbers = {}

    def cite(self, ids):
        refs = list(dict.fromkeys(self.refs[i] for i in ids if i in self.refs))
        self.used.update({r: True for r in refs})
        citations = []
        for ref in refs:
            if ref.startswith('k'):
                number = self.knowledge_numbers.setdefault(ref, len(self.knowledge_numbers) + 1)
                citations.append(f'<sup><a href="#reference-{number}">[{number}]</a></sup>')
        return ''.join(citations)

    def prose(self, text):
        # Model rationales sometimes contain canonical IDs; keep those in attachments.
        text = re.sub(r'\bK\d+(?:[/、,，]\s*K?\d+)*', '相关文献', text)
        text = re.sub(r'[（(]\s*P\d+(?:[/、,，]\s*P?\d+)*\s*[）)]', '', text)
        return cell(re.sub(r'\b(?:obs|record|claim|evidence|task|result)_[a-zA-Z0-9_]+',
                           lambda m: ('医学依据（见参考资料）' if self.refs.get(m[0], '').startswith('k')
                                      else '相关记录'), text))


def known(row):
    """Keep explicit positives/negatives and zero scores, omit missing values."""
    return observed(row) and row.status != 'uncertain' and row.value is not None and str(row.value).strip() not in {
        '', '未提供', '未记录', '未知', '不详', '不确定', '未检查', 'null', 'None'}


def display_name(row):
    exact = {'MMSE总分': 'MMSE', 'MoCA 汇总得分': 'MoCA', 'CDRSB总分': 'CDR-SB',
             '总体评分': 'CDR总体评分', '总分': form_name(row).replace('量表', '') or row.name}
    return exact.get(row.name, row.name.replace('总分', '').strip())


def clinical_fact(row):
    when = row.time.value or row.time.raw
    if row.kind == 'historical_diagnosis' or any(k in row.name for k in ('诊断', '患病情况')):
        diagnosis = {'MCI': '轻度认知障碍（MCI）'}.get(str(row.value).upper(), str(row.value))
        return (cell(when) + '，' if when else '') + '既往记录诊断为' + cell(diagnosis) + '。'
    return (f'{cell(when)}，' if when else '') + cell(display_name(row)) + '：' + cell(result_text(row))


def primary_diagnosis(report, snapshot, evidence):
    """Choose an explicit reviewed label; never diagnose from scores in a renderer."""
    available = {e.evidence_id: e for e in evidence}
    observations = {o.observation_id: o for o in snapshot.observations if known(o)}
    if report.assessment_status in {'supported_tendency', 'conditional'}:
        candidates = [c for c in report.claims if c.kind == 'interpretation' and c.diagnosis_label
                      and c.diagnosis_label in c.text and c.strength in {'supported', 'conditional', 'tentative'}
                      and c.observation_ids and all(i in observations for i in c.observation_ids)
                      and c.evidence_ids and all(i in available and available[i].applicability == 'applicable'
                          and available[i].allowed_use in {'direct_support', 'conditional_support'} for i in c.evidence_ids)]
        # Within the same evidence strength, retain the main agent's synthesis order.
        candidates.sort(key=lambda c: {'supported': 0, 'conditional': 1, 'tentative': 2}[c.strength])
        if candidates:
            c = candidates[0]
            return {'label': c.diagnosis_label, 'basis': 'current_tendency', 'claim': c}
    previous = ordered([o for o in observations.values() if o.kind == 'historical_diagnosis'
                        or any(k in o.name for k in ('诊断', '患病情况'))])
    if previous:
        dated = [o for o in previous if o.time.value]
        row = dated[-1] if dated else previous[-1]
        value = str(row.value).strip()
        return {'label': {'MCI': '轻度认知障碍（MCI）'}.get(value.upper(), value),
                'basis': 'historical_record', 'observation': row}
    return None


def diagnostic_facts(rows):
    """Summarize exact cited values without adding clinical thresholds or interpretations."""
    rows = ordered(rows)
    if len(rows) > 5 and sum('laboratory' in o.domains for o in rows) > len(rows) / 2:
        latest_date = max((o.time.value or '' for o in rows), default='')
        recent = [o for o in rows if o.time.value == latest_date]
        recent.sort(key=lambda o: (not any(k in o.name for k in ('217', '42/40')), o.name))
        rows = recent[:4] + [o for o in rows if 'APOE' in o.name]
    groups, activities = {}, {}
    for o in rows:
        if isinstance(o.value, str) and '独立完成' in o.value:
            activities.setdefault((o.time.value or '', o.value), []).append(o.name)
        else:
            groups.setdefault((form_name(o), o.name), []).append(o)
    phrases = []
    for group in groups.values():
        numeric = [o.value for o in group if isinstance(o.value, (int, float)) and not isinstance(o.value, bool)]
        if len(numeric) == len(group) and len(group) > 1:
            dates = sorted({o.time.value for o in group if o.time.value})
            period = (dates[0] + '至' + dates[-1]) if len(dates) > 1 else (dates[0] if dates else '')
            values = str(min(numeric)) if min(numeric) == max(numeric) else f'{min(numeric)}–{max(numeric)}'
            phrases.append((cell(period) + '，' if period else '') + cell(display_name(group[0])) + '记录值为' + values)
        else:
            phrases.extend(clinical_fact(o).rstrip('。') for o in group)
    for (date, value), names in activities.items():
        phrases.append((cell(date) + '，' if date else '') + cell('、'.join(names[:3]))
                       + ('等' + str(len(names)) + '项活动' if len(names) > 3 else '') + '均记录为' + cell(value))
    return '；'.join(phrases) + '。'


def render_report(report, preparation, results, evidence, reviews, mode):
    """Publish concise clinical content; omit empty sections and technical commentary."""
    view = ReportView(report, preparation, results, evidence)
    obs = [o for o in view.obs if known(o)]
    dates = sorted({o.time.value for o in obs if o.time.value})
    diagnosis = primary_diagnosis(report, preparation.snapshot, evidence)
    assessment = report.diagnostic_assessment
    outcome = report.diagnostic_outcome
    if assessment and (not assessment.verified or any(
            not all(i in view.by_id for i in s.observation_ids) or not all(i in view.refs for i in s.evidence_ids)
            for s in assessment.steps)):
        assessment = None
    lead = ''
    if outcome:
        names = {'working_diagnosis': '工作诊断', 'conditional_diagnosis': '条件性工作诊断',
                 'indeterminate': '诊断待定', 'technical_failure': '诊断技术核验未完成'}
        lead = '**本次诊断结果：' + names[outcome.status] + '**'
        if outcome.primary_working_label:
            lead += '\n\n**首选工作判断：' + cell(outcome.primary_working_label) + '**'
    elif assessment:
        lead = '**首选诊断考虑：' + cell(assessment.primary_label) + '**'
        lead += view.cite([i for s in assessment.steps for i in s.evidence_ids])
    elif diagnosis:
        caption = '首选诊断倾向' if diagnosis['basis'] == 'current_tendency' else '既往诊断'
        lead = f"**{caption}：{cell(diagnosis['label'])}**"
        if diagnosis['basis'] == 'current_tendency':
            lead += view.cite(diagnosis['claim'].evidence_ids)
    info_rows = [['患者编号', cell(report.patient_id)],
                 ['报告日期', datetime.now().astimezone().strftime('%Y-%m-%d')]]
    for o in obs:
        if o.name in {'姓名', '性别', '年龄', '出生日期', '教育程度', '教育年限', '文化程度'}:
            value = result_text(o)
            if o.name == '年龄':
                value += '岁（记录年龄）'
            info_rows.append([cell(o.name), cell(value)])
    if dates:
        info_rows.append(['记录时间', cell(dates[0] + ' 至 ' + dates[-1])])
    info = table(['项目', '内容'], info_rows)
    complaint = [o for o in obs if any(k in o.name for k in ('主诉', '主要症状'))]
    physical = [o for o in obs if any(k in label(o) for k in ('体格检查', '神经系统检查', '查体'))
                or o.name in {'血压', '收缩压', '舒张压', '心率', '脉搏', '体温', '呼吸频率'}]
    history = [o for o in obs if 'history' in o.domains and o not in physical and o not in complaint
               and not any(k in label(o) for k in ('量表', 'MMSE', 'MOCA', 'MoCA', 'FAQ', 'ADAS', 'CDR'))
               and o.name not in {'年龄', '性别', '姓名', '出生日期', '身高', '体重'}]
    totals = ordered([o for o in obs if is_total(o) or ('cognition' in o.domains and o.name == '总体评分')])
    # Latest values and longitudinal ranges are descriptions of recorded scores, not diagnoses.
    groups = {}
    for o in totals:
        groups.setdefault((form_name(o), o.name), []).append(o)
    scale_rows, findings = [], []
    for (form, name), rows in groups.items():
        dated = [o for o in rows if o.time.value]
        row = dated[-1] if dated else rows[-1]
        latest_rows = [o for o in rows if (o.time.value == row.time.value if row.time.value
                                          else o.time.raw == row.time.raw and not o.time.value)]
        numeric = [o.value for o in rows if isinstance(o.value, (int, float)) and not isinstance(o.value, bool)]
        span = (cell(str(min(numeric))) + '–' + cell(str(max(numeric)))) if numeric else ''
        scale_rows.append([cell(display_name(row)), cell(row.time.value or row.time.raw or ''),
                           '<br>'.join(cell(result_text(o)) for o in latest_rows), span])
        findings.extend(clinical_fact(o) for o in latest_rows)
        if len({json.dumps(o.value, ensure_ascii=False) for o in latest_rows}) > 1:
            findings.append(cell(display_name(row)) + '同次记录存在不同结果，已并列列出')
    scales = table(['量表', '最近记录日期', '最近得分', '历次得分范围'], scale_rows) if scale_rows else ''
    if not scales:
        scales = '\n'.join('- ' + clinical_fact(o) for o in obs if 'cognition' in o.domains)
    labs = ordered([o for o in obs if 'laboratory' in o.domains])
    # Assay replicates, raw processing values and quality-control coefficients stay in the appendix.
    lab_display = [o for o in labs if not any(k in o.name for k in ('变异系数', '原始浓度'))]
    lab_rows = [[cell(o.time.value or o.time.raw or ''), cell(o.name), cell(result_text(o))] for o in lab_display]
    lab_text = table(['检查日期', '项目', '结果'], lab_rows) if lab_rows else ''
    images = [o for o in obs if 'imaging' in o.domains and o.name not in {'年龄', '性别', '身高', '体重', '患病情况'}
              and any(k in o.name for k in ('结论', '发现', '印象', '影像报告', '萎缩', '病灶'))]
    sections = []
    def section(title, content):
        if content.strip():
            heading = '一二三四五六七'[len(sections)] + '、' + title.split('、', 1)[-1]
            sections.append('## ' + heading + '\n\n' + content.strip())
    section('一、患者基本信息', info)
    section('二、主诉', '\n'.join(clinical_fact(o) for o in complaint))
    section('三、病史', '\n\n'.join(clinical_fact(o) for o in history))
    section('四、体格检查', '\n'.join(clinical_fact(o) for o in physical))
    auxiliary = []
    for title, content in (('认知与功能量表', scales), ('实验室与生物标志物', lab_text),
                           ('影像检查', '\n\n'.join(clinical_fact(o) for o in images))):
        if content:
            auxiliary.append('### ' + title + '\n\n' + content)
    section('五、辅助检查', '\n\n'.join(auxiliary))
    conclusions = []
    if outcome:
        if outcome.assessment_time:
            conclusions.append('评估记录截止：' + cell(outcome.assessment_time) + '。' + cell(outcome.input_scope))
        for layer in outcome.layers:
            title = '认知与功能综合征' if layer.level == 'syndrome' else '病因与鉴别'
            caption = layer.label or ('技术核验未完成' if layer.status == 'technical_failure' else '待定')
            content = '**' + title + '：' + cell(caption) + '**\n\n' + view.prose(layer.reasoning)
            if layer.review_status == 'passed':
                content += view.cite(layer.evidence_ids)
            if layer.missing_conditions:
                content += '\n\n影响判断的条件：' + '；'.join(cell(v.rstrip('。；')) for v in layer.missing_conditions) + '。'
            if layer.alternatives:
                content += '\n\n需鉴别：' + '；'.join(cell(v) for v in layer.alternatives) + '。'
            conclusions.append(content)
    elif assessment:
        for step in assessment.steps:
            facts = diagnostic_facts([view.by_id[i] for i in step.observation_ids])
            conclusions.append('**' + cell(step.topic) + '**\n\n' + facts
                               + ' ' + view.prose(step.interpretation) + view.cite(step.evidence_ids))
        conclusions.append('**综合判断：** ' + view.prose(assessment.conclusion)
                           + view.cite([i for s in assessment.steps for i in s.evidence_ids]))
    elif diagnosis:
        if diagnosis['basis'] == 'current_tendency':
            c = diagnosis['claim']
            conclusions.append('**首选诊断倾向：' + cell(diagnosis['label']) + '**。' + view.prose(c.text) + view.cite(c.evidence_ids))
        else:
            conclusions.append('既往记录诊断为' + cell(diagnosis['label']) + '。')
    if findings and not assessment and not outcome:
        by_date = {}
        for text in findings:
            date, sep, item = text.partition('，')
            by_date.setdefault(date if sep else '', []).append(item if sep else text)
        conclusions.append(' '.join((date + '记录：' if date else '') + '，'.join(items) + '。'
                                   for date, items in by_date.items()))
    # Only explicit interpretations are clinical prose; execution-state summaries never enter the body.
    for c in report.claims:
        if assessment or outcome:
            break
        if c.kind != 'interpretation' or not c.observation_ids or not all(i in view.by_id for i in c.observation_ids):
            continue
        if diagnosis and diagnosis.get('claim') is c:
            continue
        if c.level in {'recommendation', 'management', 'follow_up'}:
            continue
        if not c.evidence_ids or not all(i in view.refs for i in c.evidence_ids):
            continue
        prefix = {'tentative': '考虑：', 'conditional': '评估倾向：'}.get(c.strength, '评估意见：')
        conclusions.append(prefix + view.prose(c.text) + view.cite(c.evidence_ids))
    section('六、诊断结论', '\n\n'.join(dict.fromkeys(conclusions)))
    recommendations = [view.prose(c.text) + view.cite(c.evidence_ids) for c in report.claims
                       if c.kind == 'interpretation' and c.level in {'recommendation', 'management', 'follow_up'}
                       and c.observation_ids and all(i in view.by_id for i in c.observation_ids)
                       and c.evidence_ids and all(i in view.refs for i in c.evidence_ids)]
    section('七、处理建议', '\n\n'.join(recommendations))
    references = []
    by_ref = {view.refs[e.evidence_id]: e for e in evidence}
    for ref, number in view.knowledge_numbers.items():
        e = by_ref[ref]
        details = [cell(e.title or e.document_id)]
        if e.version and e.version not in {'unknown', 'unavailable'} and not e.version.startswith(('release_', 'kb_release_')):
            details.append('版本：' + cell(e.version))
        if e.page is not None:
            details.append(f'第 {e.page} 页')
        elif e.source_locator and not e.source_locator.startswith('原文片段：'):
            details.append(cell(e.source_locator))
        references.append(f'<a id="reference-{number}"></a>\n\n[{number}] ' + '；'.join(details)
                          + f'。[原文摘录](./data_appendix.md#source-{ref})。')
    blocks = {'诊断标签': lead, '临床正文': '\n\n'.join(sections),
              '参考资料': ('### 参考资料\n\n' + '\n\n'.join(references)) if references else ''}
    template = files('neurogra.clinical').joinpath('report_template.md').read_text(encoding='utf-8')
    rendered = re.sub(r'\{\{([^{}]+)\}\}', lambda m: blocks[m[1]], template)
    return re.sub(r'\n{3,}', '\n\n', rendered).strip() + '\n'


def render_data_appendix(report, preparation, results, evidence):
    view = ReportView(report, preparation, results, evidence)
    lines = ['# 综合评估报告数据附录', '', f'患者编号：{cell(report.patient_id)}', '',
             '[返回报告正文](./report.md)', '', '## 一、资料与访视目录', '']
    groups = {}
    analyzed = {i for r in results for i in r.coverage.get('analyzed_ids', [])}
    for o in ordered(view.obs):
        groups.setdefault((date_text(o), form_name(o)), []).append(o)
    lines += [table(['日期', '资料表单', '观察数量', '模型已分析数量'],
                    [[cell(d), cell(f or '未命名表单'), str(len(rows)), str(sum(o.observation_id in analyzed for o in rows))]
                     for (d, f), rows in groups.items()])]
    for title, domain in (('二、完整认知及功能量表', 'cognition'), ('三、完整检验与生物标志物', 'laboratory'),
                          ('四、病史及影像资料明细', None)):
        rows = [o for o in ordered(view.obs) if domain in o.domains] if domain else [o for o in ordered(view.obs) if not {'cognition', 'laboratory'}.intersection(o.domains)]
        lines += ['', '## ' + title, '', table(['日期', '项目', '原始记录／原始单位', '记录状态', '来源'],
                    [[cell(date_text(o)), cell(label(o)), cell(result_text(o)), STATUS[o.status],
                      f'[来源 {view.refs[o.observation_id]}](#source-{view.refs[o.observation_id]})'] for o in rows])]
    lines += ['', '## 五、冲突、缺失与专业未决事项', '']
    lines += ['- 冲突记录组：' + cell('、'.join(g)) for g in preparation.snapshot.conflict_groups]
    lines += (['- ' + cell(s) for s in report.unresolved] or ['未登记额外未决事项。'])
    lines += ['- 资料处理问题：' + cell(i.message) for i in preparation.issues + preparation.snapshot.issues]
    if preparation.snapshot.images:
        lines += ['', '### 影像资产原始登记', '']
        lines += [cell(json.dumps(i.model_dump(mode='json'), ensure_ascii=False)) for i in preparation.snapshot.images]
    lines += ['', '### 全部专业意见与依据', '']
    for i, c in enumerate(report.claims, 1):
        lines += [f'#### 专业意见 {i}', '', f'类型：{cell(c.kind)}；标识：{cell(c.claim_id)}', '', cell(c.text), '',
                  '观察依据：' + cell('、'.join(c.observation_ids)), '知识依据：' + cell('、'.join(c.evidence_ids)),
                  '推断依据：' + cell(c.rationale), '适用条件：' + cell(c.applicability), '限制：' + cell('；'.join(c.limitations))]
    lines += ['', '## 六、来源索引', '']
    records = {r.record_id: r for r in preparation.snapshot.records}
    for o in view.obs:
        ref = view.refs[o.observation_id]
        lines += [f'<a id="source-{ref}"></a>', '', f'### 来源 {ref}', '',
                  f'{cell(date_text(o))} · {cell(label(o))}', '',
                  '观察标识：' + cell(o.observation_id), '原始记录：' + cell('、'.join(o.source_refs)),
                  '原始值：' + cell(json.dumps(o.value, ensure_ascii=False)),
                  '原始摘录：' + cell(o.quote), '上下文：' + cell(json.dumps(o.context, ensure_ascii=False))]
        for record_id in o.source_refs:
            if record_id in records:
                lines.append('记录定位：' + cell(json.dumps(records[record_id].locator, ensure_ascii=False)))
        lines.append('')
    for e in evidence:
        ref = view.refs[e.evidence_id]
        lines += [f'<a id="source-{ref}"></a>', '', f'### 知识来源 {ref}', '', cell(e.title or e.document_id),
                  f'标识：{cell(e.evidence_id)}；版本：{cell(e.version)}；定位：{cell(e.page if e.page is not None else e.source_locator)}',
                  '适用性：' + cell(e.applicability), '限制：' + cell('；'.join(e.limitations)), cell(e.quote), '']
    return '\n'.join(lines) + '\n'


def render_saved_report(source_directory, output_directory, preparation_path=None, report_override=None, evidence_override=None):
    """Offline migration preview: never resume a run, spend budget or overwrite it."""
    from pathlib import Path
    from .schemas import FinalReport, CaseSnapshot, TaskPlan, EvidenceItem
    from .service import PreparationResult
    from .workflow import render_audit
    from .utils import write_json

    source, output = Path(source_directory).resolve(), Path(output_directory).resolve()
    if source == output or output.exists():
        raise ValueError('output_directory_must_be_new')
    report = FinalReport.model_validate_json((source / 'report.json').read_text(encoding='utf-8'))
    if report_override is not None:
        if (report.run_id, report.patient_id) != (report_override.run_id, report_override.patient_id):
            raise ValueError('report_override_identity_mismatch')
        report = report_override.model_copy(deep=True)
    provenance = json.loads((source / 'provenance.json').read_text(encoding='utf-8'))
    if (report.run_id != provenance['run_id'] or report.patient_id != provenance['patient_id']):
        raise ValueError('report_provenance_identity_mismatch')
    if preparation_path:
        preparation = PreparationResult.model_validate_json(Path(preparation_path).read_text(encoding='utf-8'))
        if preparation.run_id != report.run_id or preparation.snapshot.patient_id != report.patient_id:
            raise ValueError('report_preparation_identity_mismatch')
        base = preparation.snapshot.model_dump(mode='json')
    else:
        # Legacy provenance did not save image inventory. Do not infer no images from that omission.
        if 'images' not in provenance:
            raise ValueError('legacy_report_requires_preparation')
        base = {'case_id': report.run_id, 'patient_id': report.patient_id, 'raw_input_hash': 'saved_report', 'records': []}
        preparation = None
    base.update({k: provenance[k] for k in ('records', 'observations', 'conflict_groups')})
    if 'images' in provenance:
        base['images'] = provenance['images']
    for key in ('inventory', 'patient_metadata', 'issues'):
        if key in provenance:
            base[key] = provenance[key]
    snapshot = CaseSnapshot.model_validate(base)
    plan = TaskPlan.model_validate(provenance['tasks'])
    if preparation:
        preparation = preparation.model_copy(update={'snapshot': snapshot, 'plan': plan})
    else:
        preparation = PreparationResult(run_id=report.run_id, status=report.status, snapshot=snapshot,
            plan=plan, knowledge_release_id=None, issues=[], artifact_path=str(source / 'provenance.json'))
    evidence = [EvidenceItem.model_validate(e) for e in provenance['evidence']]
    if evidence_override is not None:
        evidence = [EvidenceItem.model_validate(e) for e in evidence_override]
        provenance['evidence'] = [e.model_dump(mode='json') for e in evidence]
    report.report_template_version = TEMPLATE_VERSION
    markdown = render_report(report, preparation, report.agent_results, evidence, {}, 'saved_report')
    appendix = render_data_appendix(report, preparation, report.agent_results, evidence)
    # Preserve the original complete iteration log, including historical generations.
    old_audit = source / 'audit.md'
    if not old_audit.exists():
        old_audit = source / 'report.md'
    audit = (old_audit.read_text(encoding='utf-8') if old_audit.exists() else
             render_audit(report, preparation, report.agent_results, evidence,
                          {'stop_reason': provenance.get('stop_reason', ''), 'outcomes': {}}, 'saved_report'))
    output.mkdir(parents=True, exist_ok=False)
    for name, text in (('report.md', markdown), ('data_appendix.md', appendix),
                       ('audit.md', '# 历史运行审计\n\n[返回报告正文](./report.md)\n\n' + audit)):
        (output / name).write_text(text, encoding='utf-8')
    provenance['report_template_version'] = TEMPLATE_VERSION
    provenance['images'] = [i.model_dump(mode='json') for i in snapshot.images]
    for key in ('inventory', 'patient_metadata', 'issues'):
        provenance[key] = snapshot.model_dump(mode='json')[key]
    provenance['render_source_directory'] = str(source)
    provenance['diagnostic_assessment'] = report.diagnostic_assessment.model_dump(mode='json') if report.diagnostic_assessment else None
    provenance['diagnostic_outcome'] = report.diagnostic_outcome.model_dump(mode='json') if report.diagnostic_outcome else None
    write_json(output / 'report.json', report)
    write_json(output / 'provenance.json', provenance)
    return output / 'report.md'
