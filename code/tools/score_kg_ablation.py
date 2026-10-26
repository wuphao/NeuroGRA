"""Audit frozen cohort predictions against independent RWE labels; no model calls."""
import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'code'))

CLASSES = ('CN', 'MCI', 'AD')
OUTPUTS = (*CLASSES, 'OTHER', 'UNRESOLVED')
NAMES = {'single_llm': '单LLM', 'no_kg': '去图＋文本RAG',
         'plain_triples': '普通三元组图谱', 'full': '完整NeuroGRA'}


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def published_classes(report, group):
    result = {'syndrome': 'UNRESOLVED', 'etiology': 'UNRESOLVED'}
    for layer in (report.get('diagnostic_outcome') or {}).get('layers', []):
        permitted = layer['review_status'] == 'passed' or (group == 'single_llm' and layer['review_status'] == 'not_required')
        if permitted and layer['status'] in {'working_diagnosis', 'conditional_diagnosis'} and layer.get('label'):
            result[layer['level']] = layer.get('research_class') or 'UNRESOLVED'
    s, e = result['syndrome'], result['etiology']
    cohort = s if s in {'CN', 'MCI'} else ('AD' if s == 'DEMENTIA' and e == 'AD' else
                                        'OTHER' if s == 'DEMENTIA' and e == 'OTHER' else 'UNRESOLVED')
    return s, e, cohort


def summarize(rows):
    total = len(rows)
    classified = [r for r in rows if r['predicted_class'] != 'UNRESOLVED']
    correct = sum(r['match'] for r in rows)
    matrix = {truth: {prediction: 0 for prediction in OUTPUTS} for truth in CLASSES}
    for row in rows:
        matrix[row['reference_label']][row['predicted_class']] += 1
    return {'patients': total, 'correct': correct, 'incorrect_classified': len(classified) - correct,
        'unresolved': total - len(classified), 'classified': len(classified),
        'all_case_accuracy_counting_abstentions_as_misses': correct / total,
        'accuracy_among_classified': correct / len(classified) if classified else None,
        'classification_coverage': len(classified) / total,
        'confusion_matrix': matrix,
        'per_class': {c: {'reference_cases': sum(r['reference_label'] == c for r in rows),
            'correct': sum(r['reference_label'] == c and r['match'] for r in rows)} for c in CLASSES},
        'outcome_status': dict(Counter(r['outcome_status'] for r in rows)),
        'syndrome_stage_comparison': {'correct': sum(r['stage_match'] for r in rows), 'patients': total,
            'agreement': sum(r['stage_match'] for r in rows) / total,
            'note': '仅比较CN/MCI/痴呆阶段，参考AD暂映射DEMENTIA；不能据此声称AD病因判断正确。'}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--experiment', type=Path, default=ROOT / 'output/ablation/pilot_27_20261016')
    parser.add_argument('--references', type=Path, default=ROOT / 'output/rwe_repair_effects/strict_label_audit/reference_labels.json')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    experiment = args.experiment.resolve()
    manifest = read(experiment / 'manifest.json')
    reference = read(args.references)
    truth = {}
    for patient in reference['patients']:
        labels = {r['condition'] for r in patient['records'] if r.get('condition')}
        if len(labels) != 1 or next(iter(labels)) not in CLASSES:
            raise ValueError('reference_missing_or_conflicting:' + patient['patient_number'])
        truth[patient['patient_number']] = next(iter(labels))
    numbers = [c['patient_number'] for c in manifest['cases']]
    if len(set(numbers)) != len(numbers) or set(numbers) != set(truth):
        raise ValueError('reference_patient_membership_mismatch')
    results = read(experiment / 'results.json')
    expected = {(n, g) for n in numbers for g in manifest['groups']}
    if len(results) != len(expected) or {(r['patient_number'], r['group']) for r in results} != expected:
        raise ValueError('missing_or_duplicate_method_case')
    rows = []
    bindings = {}
    for record in results:
        number, group = record['patient_number'], record['group']
        directory = experiment / 'cases' / number / group
        prediction = read(directory / 'prediction.json')
        s, e, cohort = published_classes(read(directory / 'report.json'), group)
        if (s, e, cohort) != (prediction['syndrome_class'], prediction['etiology_class'], prediction['cohort_class']):
            raise ValueError('published_report_and_prediction_disagree:' + number + '/' + group)
        if cohort not in OUTPUTS or cohort != record['cohort_class']:
            raise ValueError('invalid_or_changed_cohort_class:' + number + '/' + group)
        reference_stage = 'DEMENTIA' if truth[number] == 'AD' else truth[number]
        rows.append({'patient_number': number, 'group': group, 'method': NAMES.get(group, group),
            'reference_label': truth[number], 'predicted_class': cohort, 'match': cohort == truth[number],
            'classified': cohort != 'UNRESOLVED', 'syndrome_class': s, 'etiology_class': e,
            'reference_stage': reference_stage, 'stage_match': s == reference_stage,
            'execution_status': prediction['execution_status'], 'outcome_status': prediction['outcome_status'],
            'primary_working_label': prediction.get('primary_working_label'), 'report_file': str(directory / 'report.json')})
        bindings[number + '/' + group] = {'prediction_sha256': sha(directory / 'prediction.json'),
            'report_sha256': sha(directory / 'report.json')}
    groups = {g: summarize([r for r in rows if r['group'] == g]) for g in manifest['groups']}
    output = (args.output or experiment / 'accuracy').resolve()
    output.mkdir(parents=True, exist_ok=False)
    write(output / 'reference_snapshot.json', reference)
    result = {'label': '与RWE原始患病标签的回顾性一致性；非临床验证准确率',
        'scored_at': datetime.now(timezone.utc).isoformat(), 'experiment': str(experiment),
        'reference_source': reference.get('source'), 'reference_exported_at': reference.get('exported_at'),
        'reference_sha256': sha(args.references), 'experiment_manifest_sha256': sha(experiment / 'manifest.json'),
        'reference_distribution': dict(Counter(truth.values())), 'groups': groups, 'artifact_bindings': bindings,
        'definition': '按实验预定stage_plus_etiology_v1终点：CN/MCI按综合征；DEMENTIA且AD病因才算AD。未决及技术失败保留分母但不视作已明确误诊。',
        'limitations': ['参考标签无对应诊断日期，尚未证明与本轮纵向综合判断同一时点。',
            '条件性工作判断被纳入分类，不代表确诊。', '样本27例，属于参与过调试的预实验，不是独立测试集。',
            '未执行五维独立自动评价或医生评价；一致率不能概括整份报告的医学质量。']}
    write(output / 'accuracy.json', result)
    with (output / 'patient_comparison.csv').open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    lines = ['# 四组方法与RWE原始患病标签的一致性', '',
        '主要终点沿用实验预定映射，不从报告自由文本或失败草稿补类别。全部27人均纳入；UNRESOLVED包括无法完成三分类、待定及技术失败，计为未命中但不一概称为误诊。', '',
        '| 方法 | 正确 | 已分类但不一致 | 未分类 | 全27人命中率 | 已分类病例一致率 |',
        '|---|---:|---:|---:|---:|---:|']
    for g, stats in groups.items():
        lines.append(f"|{NAMES.get(g,g)}|{stats['correct']}|{stats['incorrect_classified']}|{stats['unresolved']}|"
            f"{stats['correct']}/27 = {stats['all_case_accuracy_counting_abstentions_as_misses']:.2%}|"
            f"{stats['correct']}/{stats['classified']} = {stats['accuracy_among_classified']:.2%}|")
    lines += ['', '注意：已分类病例一致率的分母不同，不能只拿这一列判断方法优劣。工作标签发布率也不是CN/MCI/AD分类覆盖率。', '',
        '## 按原始类别的正确数', '', '| 方法 | CN（3例） | MCI（14例） | AD（10例） |', '|---|---:|---:|---:|']
    for g, stats in groups.items():
        lines.append('|' + NAMES.get(g,g) + '|' + '|'.join(str(stats['per_class'][c]['correct']) + '/' + str(stats['per_class'][c]['reference_cases']) for c in CLASSES) + '|')
    lines += ['', '## 综合征阶段的补充对照', '',
        '把原始AD暂映射为痴呆阶段，只比较认知正常/MCI/痴呆。此终点不验证AD病因，不能替代主要三分类终点。', '']
    for g, stats in groups.items():
        stage = stats['syndrome_stage_comparison'];lines.append(f"- {NAMES.get(g,g)}：{stage['correct']}/27 = {stage['agreement']:.2%}。")
    lines += ['', '## 解释与限制', '',
        '本轮完整系统在预定三分类终点上未优于三个基线：正确数8例，其余各9例；与去图组相比少命中1例，不能据此证明完整系统准确性更好。小样本结果也不足以证明方法普遍优劣。', '',
        f"原始标签来自{reference.get('source')}，读取时间为{reference.get('exported_at')}，在本轮生成之前保存。本次评分未将参考标签交给生成模型。", '',
        *['- ' + text for text in result['limitations']], '',
        '逐病例结果见[patient_comparison.csv](patient_comparison.csv)，完整混淆矩阵及文件哈希见[accuracy.json](accuracy.json)。']
    (output / 'summary.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps({'output': str(output), 'reference_distribution': result['reference_distribution'],
        'groups': {g: {k:s[k] for k in ['correct','incorrect_classified','unresolved','classified','all_case_accuracy_counting_abstentions_as_misses','accuracy_among_classified']} for g,s in groups.items()}}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
