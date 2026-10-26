"""Offline CN/MCI/AD accuracy and fixed-three-class Macro-F1 for frozen outputs."""
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

from audit_diagnosis_labels import CLASSES, normalize_truth
from neurogra.benchmarks.medgraphrag.pipeline import Prediction, comparison_labels
from neurogra.benchmarks.medgraphrag.runtime import write_json


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def metrics(rows):
    """Abstentions add FN for their true class; never add a fourth macro class."""
    known = [r for r in rows if r['reference_label'] in CLASSES]
    if not known:
        raise ValueError('no_known_reference_labels')
    matrix = {c: dict.fromkeys((*CLASSES, 'UNRESOLVED'), 0) for c in CLASSES}
    for row in known:
        prediction = row['predicted_class'] if row['predicted_class'] in CLASSES else 'UNRESOLVED'
        matrix[row['reference_label']][prediction] += 1
    per_class = {}
    for label in CLASSES:
        tp = matrix[label][label]
        fp = sum(matrix[other][label] for other in CLASSES if other != label)
        fn = sum(matrix[label][other] for other in (*CLASSES, 'UNRESOLVED') if other != label)
        per_class[label] = {'support': sum(matrix[label].values()), 'tp': tp, 'fp': fp, 'fn': fn,
                            'precision': tp / (tp + fp) if tp + fp else 0.0,
                            'recall': tp / (tp + fn) if tp + fn else 0.0,
                            'f1': 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.0}
    mapped = sum(row['predicted_class'] in CLASSES for row in known)
    correct = sum(matrix[c][c] for c in CLASSES)
    return {'patients': len(rows), 'evaluated': len(known), 'unknown_reference': len(rows) - len(known),
            'correct': correct, 'accuracy': correct / len(known),
            'macro_f1': sum(per_class[c]['f1'] for c in CLASSES) / len(CLASSES),
            'macro_precision': sum(per_class[c]['precision'] for c in CLASSES) / len(CLASSES),
            'macro_recall': sum(per_class[c]['recall'] for c in CLASSES) / len(CLASSES),
            'mapped_predictions': mapped, 'unresolved_predictions': len(known) - mapped,
            'prediction_coverage': mapped / len(known),
            'accuracy_among_mapped_only': correct / mapped if mapped else None,
            'reference_distribution': dict(Counter(r['reference_label'] for r in known)),
            'prediction_distribution': {c: sum(matrix[t][c] for t in CLASSES)
                                        for c in (*CLASSES, 'UNRESOLVED')},
            'per_class': per_class, 'confusion_matrix': matrix,
            'macro_labels': list(CLASSES), 'zero_division': 0,
            'formulas': {'accuracy': 'correct / all known-reference cases, including abstentions',
                         'f1_per_class': '2*TP / (2*TP + FP + FN)',
                         'macro_f1': '(F1_CN + F1_MCI + F1_AD) / 3'}}


def evaluate(batch_dir, reference_path):
    batch_dir = batch_dir.resolve()
    references = read(reference_path)
    reference_rows = references['patients']
    lookup = {r['patient_number']: r for r in reference_rows}
    if len(lookup) != len(reference_rows):
        raise ValueError('duplicate_reference_patient')
    summary = read(batch_dir / 'summary.json')
    patients = summary['patients']
    numbers = [r['patient_number'] for r in patients]
    if len(numbers) != len(set(numbers)):
        raise ValueError('duplicate_batch_patient')
    rows = []
    for state in patients:
        number = state['patient_number']
        if number not in lookup:
            raise ValueError('reference_missing_patient: ' + number)
        if state['status'] != 'completed':
            raise ValueError('batch_patient_not_completed: ' + number)
        case = batch_dir / 'cases' / number
        receipt = read(case / 'latest.json')
        run = Path(receipt['attempt']) / 'run'
        if not run.is_relative_to(case):
            raise ValueError('run_outside_case_directory')
        for name in ('labels.json', 'prediction.json', 'manifest.json'):
            if sha(run / name) != receipt['artifact_sha256'][name]:
                raise ValueError('frozen_prediction_artifact_changed: ' + number + ':' + name)
        labels, prediction, manifest = read(run / 'labels.json'), read(run / 'prediction.json'), read(run / 'manifest.json')
        structured = Prediction.model_validate({k: prediction[k] for k in Prediction.model_fields})
        expected = comparison_labels(structured)
        if any(labels[k] != value for k, value in expected.items()):
            raise ValueError('evaluation_mapping_mismatch: ' + number)
        if labels != receipt['labels'] or labels != state['labels']:
            raise ValueError('batch_labels_disagree: ' + number)
        if not (labels['run_id'] == prediction['run_id'] == manifest['run_id']):
            raise ValueError('run_identity_mismatch: ' + number)
        raw = read(Path(manifest['input_file']))
        if raw['patient']['patient_number'] != number or sha(manifest['input_file']) != receipt['input_sha256']:
            raise ValueError('patient_input_identity_or_hash_mismatch: ' + number)
        truth, issue = normalize_truth(lookup[number]['records'])
        predicted = labels['evaluation_class']
        if predicted is not None and predicted not in CLASSES:
            raise ValueError('unexpected_evaluation_class')
        label_paths = []
        for i, record in enumerate(raw.get('forms', {}).get('dicom_basic_info', {}).get('records', [])):
            if record.get('患病情况') is not None:
                label_paths.append(f'/forms/dicom_basic_info/records/{i}/患病情况')
        if raw.get('demographics', {}).get('患病情况') is not None:
            label_paths.append('/demographics/患病情况')
        rows.append({'patient_number': number, 'reference_label': truth, 'reference_issue': issue,
                     'reference_records': lookup[number]['records'], 'predicted_class': predicted,
                     'prediction_status': 'mapped' if predicted in CLASSES else 'unresolved',
                     'correct': bool(truth == predicted) if truth in CLASSES else None,
                     'cognitive_stage': labels['cognitive_stage'], 'etiology_label': labels['etiology_label'],
                     'assessment_status': labels['assessment_status'], 'run_id': labels['run_id'],
                     'target_label_input_paths': label_paths,
                     'prediction_file': str(run / 'prediction.json'),
                     'prediction_sha256': sha(run / 'prediction.json'),
                     'labels_file': str(run / 'labels.json'), 'labels_sha256': sha(run / 'labels.json')})
    return {'method': 'MedGraphRAG-adapted', 'evaluated_at': datetime.now(timezone.utc).isoformat(),
            'batch_directory': str(batch_dir), 'batch_summary_sha256': sha(batch_dir / 'summary.json'),
            'reference': {'file': str(reference_path.resolve()), 'sha256': sha(reference_path),
                          'source': references.get('source'), 'exported_at': references.get('exported_at')},
            'endpoint': 'frozen labels.json:evaluation_class; verified against structured prediction mapping',
            'mapping': 'NC->CN; MCI->MCI; DEMENTIA+AD etiology->AD; all other combinations abstain. '
                       'No keyword mining or post-hoc relabeling of the report.',
            'metrics': metrics(rows), 'patients': rows,
            'limitations': ['Reference labels are the saved RWE form-87 cohort labels, not independently adjudicated clinical truth.',
                            'Reference diagnosis dates are unavailable; alignment with the supplied longitudinal visits is unproven.',
                            'Inputs were previously trimmed and label-masked; this clinical_review batch is a retrospective label comparison, not a validated blinded clinical study.',
                            'Unresolved/abstaining predictions are retained as non-hits in all-case accuracy and as FN for the true class in fixed-three-class Macro-F1.',
                            'Published evaluation_class is counted even when assessment_status is unresolved or insufficient_data; these statuses remain visible per case.',
                            'This evaluates category agreement, not report quality, clinical correctness, citation entailment or probability calibration.']}


def save_report(result, directory):
    directory.mkdir(parents=True, exist_ok=False)
    write_json(directory / 'metrics.json', result)
    fields = ['patient_number', 'reference_label', 'predicted_class', 'prediction_status', 'correct',
              'cognitive_stage', 'etiology_label', 'assessment_status', 'run_id', 'prediction_file']
    with (directory / 'patient_comparison.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(result['patients'])
    m = result['metrics']
    lines = ['# MedGraphRAG 27 例分类结果统计', '',
             f'分类：CN / MCI / AD；参考来源：{result["reference"]["source"]}，'
             f'参考快照时间：{result["reference"]["exported_at"]}。', '',
             '| 指标 | 结果 |', '|---|---:|',
             f'| 全样本 Accuracy | {m["correct"]}/{m["evaluated"]} = {m["accuracy"]:.2%} |',
             f'| Macro-F1（三类等权） | {m["macro_f1"]:.6f}（{m["macro_f1"]:.2%}） |',
             f'| 三分类标签覆盖率 | {m["mapped_predictions"]}/{m["evaluated"]} = {m["prediction_coverage"]:.2%} |',
             f'| 未决 / 未映射 | {m["unresolved_predictions"]} |', '',
             '未决保留在总分母中，Accuracy 计为未命中；每类 F1 中计入真实类别的 FN。Macro-F1 仅对 CN/MCI/AD 三类平均，'
             '不把未决增设为第四个宏平均类别。', '',
             '| 类别 | 真实例数 | Precision | Recall | F1 |', '|---|---:|---:|---:|---:|']
    for label in CLASSES:
        c = m['per_class'][label]
        lines.append(f'| {label} | {c["support"]} | {c["precision"]:.2%} | {c["recall"]:.2%} | {c["f1"]:.2%} |')
    lines += ['', '## 混淆矩阵', '', '行是真实标签，列是预测标签。', '',
              '| 真实 / 预测 | CN | MCI | AD | 未决 |', '|---|---:|---:|---:|---:|']
    for label in CLASSES:
        row = m['confusion_matrix'][label]
        lines.append('| ' + label + ' | ' + ' | '.join(str(row[c]) for c in (*CLASSES, 'UNRESOLVED')) + ' |')
    if m['accuracy_among_mapped_only'] is not None:
        lines += ['', f'仅在有三分类输出的 {m["mapped_predictions"]} 例中，一致率为 '
                  f'{m["accuracy_among_mapped_only"]:.2%}；这是选择性子集结果，不替代全样本准确率。']
    lines += ['', '## 预测口径', '', result['mapping'], '',
              f'标准患病情况字段仍出现在输入的病例数：{sum(bool(r["target_label_input_paths"]) for r in result["patients"])}。',
              '', '## 限制', '', '本结果是与 RWE 参考标签的回顾性类别一致率，不能等同于经过独立临床验证的诊断准确率。'
              '参考标签无诊断日期，未证明与模型可见的随访时间一致；也未审核报告质量和引用的语义支持。']
    (directory / 'summary.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch-dir', type=Path, default=ROOT / 'output/baselines/medgraphrag/rwe27')
    parser.add_argument('--reference-labels', type=Path, default=ROOT / 'output/rwe_label_audit/reference_labels.json')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'output/baselines/medgraphrag/rwe27/evaluation')
    args = parser.parse_args(argv)
    result = evaluate(args.batch_dir, args.reference_labels)
    save_report(result, args.output_dir)
    print(json.dumps(result['metrics'], ensure_ascii=False, indent=2))
    print('统计结果：' + str(args.output_dir.resolve()))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
