"""Audit existing predictions against RWE cohort labels, without generating predictions.

Historical diagnoses rendered from the input are never treated as predictions.
This retrospective label agreement audit is not a blinded diagnostic evaluation.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'code'))

CLASSES = ('CN', 'MCI', 'AD')
# Explicit aliases only: no keyword search through history, negations or conclusions.
PREDICTION_ALIASES = {
    'CN': 'CN', '认知正常': 'CN', '认知正常随访': 'CN',
    'MCI': 'MCI', 'MCI随访': 'MCI', '轻度认知障碍': 'MCI',
    '轻度认知障碍（MCI）': 'MCI', '轻度认知障碍（MCI）随访': 'MCI',
    'AD': 'AD', 'AD随访': 'AD', '阿尔茨海默病': 'AD',
    '阿尔茨海默病痴呆（AD Dementia）随访': 'AD',
}


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def label_rows(raw):
    return [{'record_id': row.get('record_id', row.get('id')),
             'condition': row.get('患病情况')} for row in raw]


def normalize_truth(records):
    values = {str(row['condition']).strip() for row in records if row.get('condition') is not None
              and str(row['condition']).strip()}
    if len(values) != 1:
        return None, 'missing_or_conflicting_reference_labels'
    value = next(iter(values))
    if value not in CLASSES:
        return None, 'unrecognized_reference_label:' + value
    return value, None


def refresh_labels(patient_paths):
    from export_rwe_patient import (
        export_arguments, dependencies, login_token, resolve_patient, api_query_form,
    )
    database, requests = dependencies()
    args = export_arguments('', Path('.'))
    args.token = login_token(requests, args)
    results = []
    for number, path in patient_paths:
        args.patient_number = number
        patient = resolve_patient(args, database)
        raw = api_query_form(requests, args, 87, patient['patient_id'])
        results.append({'patient_number': number, 'patient_id': patient['patient_id'],
            'records': label_rows(raw)})
        print('已读取 RWE 患病情况：' + number, flush=True)
    return results


def compare_case(number, raw, report, reference, provenance):
    truth, truth_issue = normalize_truth(reference['records'])
    saved = label_rows(raw.get('forms', {}).get('dicom_basic_info', {}).get('records', []))
    saved_truth, _ = normalize_truth(saved)
    assessment = report.get('diagnostic_assessment') or {}
    outcome = report.get('diagnostic_outcome')
    label = outcome.get('primary_working_label') if outcome is not None else assessment.get('primary_label')
    # The report JSON's published primary label is the prediction endpoint.
    # We do not mine the Markdown's historical header, raw LLM drafts or other text.
    prediction = PREDICTION_ALIASES.get(label.strip()) if isinstance(label, str) else None
    prediction_status = 'no_published_primary_label' if not label else ('mapped' if prediction else 'unmapped_label')
    input_paths = []
    for index, record in enumerate(raw.get('forms', {}).get('dicom_basic_info', {}).get('records', [])):
        if record.get('患病情况') is not None:
            input_paths.append(f'/forms/dicom_basic_info/records/{index}/患病情况')
    if raw.get('demographics', {}).get('患病情况') is not None:
        input_paths.append('/demographics/患病情况')
    observation_values = [o.get('value') for o in provenance.get('observations', []) if o.get('name') == '患病情况']
    return {'patient_number': number, 'reference_label': truth,
        'reference_records': reference['records'], 'reference_issue': truth_issue,
        'saved_input_label': saved_truth, 'reference_matches_saved_input': truth == saved_truth if truth and saved_truth else None,
        'primary_label_original': label, 'predicted_class': prediction,
        'prediction_status': prediction_status,
        'match': prediction == truth if prediction and truth else None,
        'report_status': report['status'],
        'report_run_id': report['run_id'],
        'diagnostic_verified': assessment.get('verified'),
        'target_label_present_in_input': bool(input_paths),
        'label_input_paths': input_paths,
        'label_values_in_provenance': observation_values,
        'reference_date': None,
        'reference_date_note': 'DICOM基本信息中的患病情况未提供对应诊断日期，未证明与末次访视同一时间。'}


def aggregate(rows):
    known = [r for r in rows if r['reference_label'] in CLASSES]
    mapped = [r for r in known if r['predicted_class'] in CLASSES]
    correct = sum(r['match'] is True for r in mapped)
    published = sum(r['prediction_status'] != 'no_published_primary_label' for r in known)
    matrix = {label: dict.fromkeys((*CLASSES, 'no_prediction', 'unmapped_prediction'), 0) for label in CLASSES}
    for row in known:
        prediction = row['predicted_class'] or ('no_prediction' if row['prediction_status'] == 'no_published_primary_label'
                                                else 'unmapped_prediction')
        matrix[row['reference_label']][prediction] += 1
    return {'patients': len(rows), 'known_reference_labels': len(known),
        'reference_distribution': dict(Counter(r['reference_label'] for r in known)),
        'published_primary_labels': published, 'mapped_predictions': len(mapped),
        'no_published_primary_label': sum(r['prediction_status'] == 'no_published_primary_label' for r in known),
        'unmapped_predictions': published - len(mapped), 'correct_mapped_predictions': correct,
        'incorrect_mapped_predictions': len(mapped) - correct,
        'published_label_coverage': published / len(known) if known else None,
        'agreement_among_mapped_predictions': correct / len(mapped) if mapped else None,
        'correct_label_yield_all_reference_cases': correct / len(known) if known else None,
        'input_contains_target_label': sum(r['target_label_present_in_input'] for r in rows),
        'reference_changed_from_saved_input': [r['patient_number'] for r in known if r['reference_matches_saved_input'] is False],
        'confusion_matrix': matrix,
        'per_class': {label: {'reference_cases': sum(r['reference_label'] == label for r in known),
            'mapped_predictions': sum(r['reference_label'] == label for r in mapped),
            'correct': sum(r['reference_label'] == label and r['match'] is True for r in mapped)} for label in CLASSES}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch-dir', type=Path, default=ROOT / 'output/rwe_batch_screenshots')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'output/rwe_label_audit')
    parser.add_argument('--refresh-rwe', action='store_true', help='重新读取原始RWE表单87，不修改任何源数据')
    args = parser.parse_args()
    summary = read(args.batch_dir / 'summary.json')
    numbers = [r['patient_number'] for r in summary['patients']]
    if len(set(numbers)) != len(numbers):
        raise ValueError('duplicate_patient_numbers')
    paths = [(n, args.batch_dir / 'patients' / f'patient_{n}_analysis.json') for n in numbers]
    if args.refresh_rwe:
        references = refresh_labels(paths)
        origin = 'fresh_rwe_api_form_87'
    else:
        references = [{'patient_number': n, 'records': label_rows(read(path)['forms']['dicom_basic_info']['records'])}
                      for n, path in paths]
        origin = 'saved_export_also_used_as_model_input'
    timestamp = datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds')
    write(args.output_dir / 'reference_labels.json', {'exported_at': timestamp, 'source': origin, 'patients': references})
    lookup = {r['patient_number']: r for r in references}
    rows = []
    for number, path in paths:
        directory = args.batch_dir / 'reports' / number
        report_path = directory / 'report.json'
        raw, report, provenance = read(path), read(report_path), read(directory / 'provenance.json')
        run_result = read(directory / 'result.json')
        if raw['patient']['patient_number'] != number or report['patient_id'] != number or provenance['patient_id'] != number:
            raise ValueError('patient_identity_mismatch:' + number)
        if run_result['input_sha256'] != digest(path):
            raise ValueError('saved_input_changed_since_report:' + number)
        if len({report['run_id'], provenance['run_id'], run_result['run_id']}) != 1:
            raise ValueError('report_provenance_run_mismatch:' + number)
        row = compare_case(number, raw, report, lookup[number], provenance)
        row.update({'patient_file': str(path.resolve()), 'patient_file_sha256': digest(path),
                    'report_file': str(report_path.resolve()), 'report_file_sha256': digest(report_path)})
        rows.append(row)
    metrics = aggregate(rows)
    leakage_note = ('本轮输入保留患病情况，存在目标标签泄漏；一致率不能证明独立诊断能力。'
                    if metrics['input_contains_target_label'] else
                    '已保存输入中未发现指定的患病情况字段值；仍需核查自由文本及其他可能泄漏，不能仅据此宣称完全盲法。')
    result = {'label': '回顾性疾病类别一致性核对（非临床诊断准确率）', 'audited_at': timestamp,
        'reference_source': origin, 'prediction_endpoint': 'report.json: diagnostic_outcome.primary_working_label（旧报告为diagnostic_assessment.primary_label）',
        'mapping': PREDICTION_ALIASES, 'metrics': metrics, 'patients': rows,
        'limitations': [
            leakage_note,
            '无已发布首选诊断时保留为未输出，不把既往诊断、未通过的模型草稿或普通事实描述当作预测。',
            '输入标签是RWE记录的参考类别，尚未独立核验医生判定、标签时间及其与最新访视的对应关系。',
            'completed/partial是流程状态，不是诊断正确与否，不能以完成数量作为准确率分子。',
            '全样本正确标签产出率将未输出/未映射视为未命中，不能把这些病例直接称为医学误诊。',
            '此统计只核对疾病类别，不评价事实、引用、条件适用性和报告推理质量。']}
    write(args.output_dir / 'label_audit.json', result)
    fields = ['patient_number', 'reference_label', 'saved_input_label', 'reference_matches_saved_input',
        'primary_label_original', 'predicted_class', 'prediction_status', 'match', 'report_status',
        'target_label_present_in_input', 'reference_date_note', 'report_file']
    with (args.output_dir / 'patient_comparison.csv').open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)
    m = result['metrics']
    def percent(value):
        return f'{value:.2%}' if value is not None else '无法计算'
    lines = ['# 本轮诊断标签核对', '', result['label'], '',
        f'参考来源：{origin}；核对时间：{timestamp}。', '',
        '| 指标 | 结果 |', '| --- | --- |',
        f"| 已知参考标签 | {m['known_reference_labels']}/{m['patients']} |",
        f"| 已发布首选诊断 | {m['published_primary_labels']}/{m['known_reference_labels']} |",
        f"| 未发布首选诊断 | {m['no_published_primary_label']} |",
        f"| 已映射输出中的一致率 | {m['correct_mapped_predictions']}/{m['mapped_predictions']} = {percent(m['agreement_among_mapped_predictions'])} |",
        f"| 全样本正确标签产出率 | {m['correct_mapped_predictions']}/{m['known_reference_labels']} = {percent(m['correct_label_yield_all_reference_cases'])} |", '',
        '| 患者 | RWE参考标签 | 系统原始首选标签 | 归一化预测 | 结果 |', '| --- | --- | --- | --- | --- |']
    for row in rows:
        outcome = ('一致' if row['match'] else '不一致') if row['match'] is not None else row['prediction_status']
        lines.append(f"| {row['patient_number']} | {row['reference_label']} | {row['primary_label_original'] or '未发布'} | {row['predicted_class'] or '—'} | {outcome} |")
    lines += ['', '## 解释与限制', '', *['- ' + value for value in result['limitations']]]
    (args.output_dir / 'summary.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps(result['metrics'], ensure_ascii=False, indent=2))
    print('核对结果：' + str(args.output_dir.resolve()))


if __name__ == '__main__':
    main()
