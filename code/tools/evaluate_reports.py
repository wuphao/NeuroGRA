"""Prepare independent references, blind reports, run an LLM reviewer and export review sheets."""
from __future__ import annotations

import argparse
from collections import defaultdict
import csv
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import runpy
import sys
import traceback

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'code'))
from neurogra.clinical.report_evaluation import (
    RULE_VERSION, DIMENSIONS, blind_reports, evaluate, reference_from_patient,
    sample_review, sha, units, validate_reference, agreement, PROBLEMS, VERDICTS,
    SYSTEM, COVERAGE_SYSTEM, Review, Coverage, review_input,
)


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    temp.replace(path)


def relative(base, text):
    path = Path(text)
    return (path if path.is_absolute() else base / path).resolve()


def prepare(args):
    config = runpy.run_path(str(ROOT / 'code/main.py'))['build_config']()
    paths = sorted(args.patients_dir.glob('*_analysis.json'))
    if not paths:
        raise ValueError('no_patient_json_files')
    args.output.mkdir(parents=True, exist_ok=True)
    for path in paths:
        raw = read(path)
        number = raw['patient']['patient_number']
        target = args.output / (number + '.json')
        # Never overwrite human annotations.
        if target.exists():
            old = read(target)
            if not args.refresh_drafts or old.get('checklist_status') != 'draft' or any(f.get('critical') for f in old['facts']):
                print('保留已有清单：' + str(target))
                continue
        reference = reference_from_patient(raw, config)
        reference['prepared_at'] = datetime.now(timezone.utc).isoformat()
        reference['checklist_timing'] = 'retrospective'
        reference['patient_file'] = str(path.resolve())
        write(target, reference)
    print('参考清单：' + str(args.output.resolve()))


def blind(args):
    manifest_path = args.manifest.resolve()
    manifest = read(manifest_path)
    entries = []
    for row in manifest['reports']:
        report_path = relative(manifest_path.parent, row['report_path'])
        reference_path = relative(manifest_path.parent, row['reference_path'])
        reference = read(reference_path)
        validate_reference(reference)
        entries.append({**row, 'report_path': str(report_path), 'reference_path': str(reference_path),
                        'text': report_path.read_text(encoding='utf-8-sig'), 'reference': reference})
    public, private = blind_reports(entries, args.seed)
    # An immutable snapshot. New inputs or rules require a fresh study directory.
    args.output.mkdir(parents=True, exist_ok=False)
    for packet in public:
        write(args.output / 'blinded' / (packet['blind_id'] + '.json'), packet)
    write(args.output / 'private_mapping.json', private)
    write(args.output / 'study.json', {'rule_version': RULE_VERSION, 'seed': args.seed,
        'created_at': datetime.now(timezone.utc).isoformat(), 'count': len(public),
        'packet_hashes': {p['blind_id']: sha(p) for p in public},
        'blinding_status': 'automatic_masking_requires_manual_inspection',
        'reference_status': 'snapshot_frozen_at_blinding_not_proof_of_preregistration'})
    print('匿名包已冻结，请检查身份或方法名称是否仍能从文本识别：' + str(args.output))


def manifest_from_batch(args):
    summary = read(args.batch_dir / 'summary.json')
    generation_model = runpy.run_path(str(ROOT / 'code/main.py'))['GENERATION_MODEL']
    rows = []
    for row in summary['patients']:
        number = row['patient_number']
        rows.append({'case_id': number, 'method': args.method,
            'generation_model': generation_model,
            'report_path': str((args.batch_dir / 'reports' / number / 'report.md').resolve()),
            'reference_path': str((args.references / (number + '.json')).resolve()),
            'mask_tokens': ['NeuroGRA', 'neurogra']})
    write(args.output, {'reports': rows,
        'note': '清单含全部计划病例；blind前需确保每份报告已经生成。三组方法使用相同case_id和reference_path。'})
    print('报告登记清单：' + str(args.output))


def run(args):
    from neurogra.clinical.config import ModelConfig
    from neurogra.clinical.llm import ModelGateway
    from neurogra.clinical.storage import RunStore
    from neurogra_common.llm_log import configure_llm_log

    directory = args.study.resolve()
    study = read(directory / 'study.json')
    if study['rule_version'] != RULE_VERSION:
        raise ValueError('evaluation_rule_version_changed_create_new_study')
    config = ModelConfig(base_url=args.base_url, model=args.model, timeout_seconds=args.timeout,
        max_output_tokens=6000, context_tokens=args.context_tokens, max_input_chars=100000)
    paths = sorted((directory / 'blinded').glob('B*.json'))
    if len(paths) != study['count']:
        raise ValueError('blinded_packet_count_changed')
    # No private method mapping is read by the evaluator.
    for path in paths:
        packet = read(path)
        if sha(packet) != study['packet_hashes'][packet['blind_id']]:
            raise ValueError('frozen_packet_changed_create_new_study')
    if args.check:
        from neurogra.clinical.context_budget import measure, messages_for
        checks = []
        for path in paths:
            packet = read(path)
            reference = packet['reference']
            validate_reference(reference)
            report_units = units(packet['text'])
            measurements = [measure(messages_for(SYSTEM, review_input(packet, report_units[i:i + args.batch_size], report_units)),
                                    Review, config) for i in range(0, len(report_units), args.batch_size)]
            facts = [f for f in reference['facts'] if f['critical']] if reference['checklist_status'] == 'reviewed' else []
            measurements += [measure(messages_for(COVERAGE_SYSTEM, {'report': packet['text'],
                'critical_facts': facts[i:i + args.batch_size]}), Coverage, config)
                for i in range(0, len(facts), args.batch_size)]
            checks.append({'blind_id': packet['blind_id'], 'review_units': len(report_units),
                'minimum_model_calls': len(measurements), 'completeness_ready': reference['checklist_status'] == 'reviewed',
                'all_requests_fit': bool(measurements) and all(m['fits'] for m in measurements),
                'max_estimated_input_tokens': max((m['estimated_input_tokens'] for m in measurements), default=0)})
        write(directory / 'preflight.json', {'model': args.model, 'context_tokens': args.context_tokens, 'reports': checks})
        print(json.dumps(checks, ensure_ascii=False, indent=2))
        return
    store = RunStore(directory / 'evaluation.sqlite')
    settings_hash = sha({'model': config.model_dump(mode='json'), 'batch_size': args.batch_size,
                         'rule_version': RULE_VERSION})
    for index, path in enumerate(paths, 1):
        packet = read(path)
        result_path = directory / 'results' / (packet['blind_id'] + '.json')
        if result_path.exists():
            old = read(result_path)
            if old.get('settings_hash') != settings_hash:
                raise ValueError('evaluator_settings_changed_create_new_study')
            if old.get('packet_sha256') != sha(packet):
                raise ValueError('result_packet_hash_mismatch')
            if old['status'] == 'completed':
                continue
        print(f"[{index}/{len(paths)}] 自动评价 {packet['blind_id']}", flush=True)
        configure_llm_log(directory / 'logs' / (packet['blind_id'] + '.jsonl'))
        n_facts = sum(f['critical'] for f in packet['reference']['facts'])
        calls = math.ceil(len(units(packet['text'])) / args.batch_size) + math.ceil(n_facts / args.batch_size)
        run_id = store.create_run(config.model_dump(mode='json'), {'llm': max(4, calls * 3), 'seconds': args.report_timeout})
        metadata = {'run_id': run_id, 'blind_id': packet['blind_id'], 'settings_hash': settings_hash,
                    'evaluator_model': args.model, 'evaluation_type': 'automatic',
                    'packet_sha256': sha(packet)}
        def checkpoint(partial):
            write(result_path, {**metadata, **partial})
        try:
            result = evaluate(packet, ModelGateway(config, store, run_id),
                              batch_size=args.batch_size, on_checkpoint=checkpoint)
            write(result_path, {**metadata, **result})
            store.status(run_id, 'completed')
        except Exception as exc:
            partial = read(result_path) if result_path.exists() else {}
            write(result_path, {**partial, **metadata, 'status': 'failed',
                'error': f'{type(exc).__name__}: {exc}', 'metrics': None})
            store.status(run_id, 'failed')
            print(f"{packet['blind_id']} 评价失败：{type(exc).__name__}: {exc}", flush=True)
    summarize(directory)
    if any(read(directory / 'results' / (read(path)['blind_id'] + '.json'))['status'] != 'completed' for path in paths):
        raise RuntimeError('some_report_evaluations_failed_see_results')


def export_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            # Keep source text literal; neutralize spreadsheet formula execution.
            writer.writerow({key: ("'" + str(row.get(key, '')) if str(row.get(key, '')).startswith(('=', '+', '-', '@'))
                                   else row.get(key, '')) for key in fields})


def summarize(directory):
    mapping = read(directory / 'private_mapping.json')
    methods = {m['blind_id']: m for m in mapping}
    results = [read(p) for p in sorted((directory / 'results').glob('B*.json'))]
    grouped = defaultdict(list)
    for entry in mapping:
        grouped[entry['method']]
    rows = []
    for result in results:
        group = methods[result['blind_id']]
        grouped[group['method']].append(result)
        for j in result.get('judgments', []):
            rows.append({'blind_id': result['blind_id'], 'item_id': j['unit_id'] + ':' + j['dimension'],
                'case_id': group['blind_case_id'], 'dimension': j['dimension'], 'report_quote': j['quote'],
                'basis': json.dumps(j['basis'], ensure_ascii=False), 'auto_verdict': j['verdict'],
                'auto_reason': j['reason'], 'auto_severity': j['severity'], 'handling': j['handling'],
                'evaluation_status': result['status']})
        for c in result.get('coverage', []):
            rows.append({'blind_id': result['blind_id'], 'item_id': c['fact_id'],
                'case_id': group['blind_case_id'], 'dimension': 'key_evidence_completeness',
                'report_quote': c['quote'] or '（遗漏或无法确认，无对应原句）',
                'basis': c['fact_id'] + ': ' + c['basis_quote'], 'auto_verdict': c['verdict'],
                'auto_reason': c['reason'], 'auto_severity': 'none', 'handling': '',
                'evaluation_status': result['status']})
    summaries = []
    for method, group in sorted(grouped.items()):
        valid = [r for r in group if r['status'] == 'completed']
        for dimension in (*DIMENSIONS, 'key_evidence_completeness'):
            values = []
            for result in valid:
                metric = result['metrics'][dimension]
                value = metric.get('coverage_rate') if dimension == 'key_evidence_completeness' else metric.get('problem_rate_among_evaluable')
                if value is not None:
                    values.append(value)
            summaries.append({'method': method, 'dimension': dimension,
                'reports_expected': sum(m['method'] == method for m in mapping),
                'reports_completed': len(valid), 'reports_failed': sum(r['status'] == 'failed' for r in group),
                'reports_in_progress': sum(r['status'] == 'in_progress' for r in group),
                'reports_pending': sum(m['method'] == method for m in mapping) - len(group),
                'reports_with_metric': len(values), 'macro_mean': sum(values) / len(values) if values else None})
    limitations = ['自动评价结果，不等同临床验证或医生认可。',
        'macro_mean 对前四维是可核验单元问题率（越低越好），对完整性是关键事实覆盖率（越高越好）。',
        '无法核验、未知与来源缺失不当作错误，也不当作正确；同时查看逐报告分母和缺失计数。',
        '评价失败的报告不进入指标均值；未完成或无可计算分母的报告数量必须同时披露。',
        '清单冻结时间不证明其早于报告生成；本轮已生成报告的事后清单应标记 retrospective。']
    for result in results:
        generator = methods[result['blind_id']].get('generation_model')
        if not generator:
            limitations.append(result['blind_id'] + '：生成模型未登记，无法确定评价模型独立性。')
        elif generator == result.get('evaluator_model'):
            limitations.append(result['blind_id'] + '：生成和评价使用相同模型；评价为独立调用，存在自偏好风险。')
    write(directory / 'summary.json', {'label': '自动评价结果', 'aggregate': summaries, 'limitations': limitations})
    fields = ['blind_id', 'item_id', 'case_id', 'dimension', 'report_quote', 'basis', 'auto_verdict',
              'auto_reason', 'auto_severity', 'handling', 'evaluation_status']
    export_csv(directory / 'item_results.csv', rows, fields)
    print('逐条结果：' + str(directory / 'item_results.csv'))
    print('分维度汇总：' + str(directory / 'summary.json'))


def review_sheet(args):
    directory = args.study.resolve()
    mapping = read(directory / 'private_mapping.json')
    results = [read(p) for p in sorted((directory / 'results').glob('B*.json'))]
    selected = sample_review(results, mapping, args.count, args.seed)
    packets = {key: read(directory / 'blinded' / (key + '.json')) for key in selected}
    for key, packet in packets.items():
        target = directory / 'human_review' / key
        target.mkdir(parents=True, exist_ok=True)
        (target / 'report.md').write_text(packet['text'], encoding='utf-8')
        write(target / 'reference.json', packet['reference'])
    auto_rows, independent_rows = [], []
    for result in results:
        if result['blind_id'] not in selected:
            continue
        for j in result['judgments']:
            common = {'blind_id': result['blind_id'], 'item_id': j['unit_id'] + ':' + j['dimension'],
                'dimension': j['dimension'], 'report_quote': j['quote'], 'basis': json.dumps(j['basis'], ensure_ascii=False)}
            auto_rows.append({**common, 'auto_verdict': j['verdict'], 'auto_reason': j['reason'], 'auto_severity': j['severity']})
        for c in result['coverage']:
            common = {'blind_id': result['blind_id'], 'item_id': c['fact_id'],
                'dimension': 'key_evidence_completeness', 'report_quote': c['quote'],
                'basis': c['fact_id'] + ': ' + c['basis_quote']}
            auto_rows.append({**common, 'auto_verdict': c['verdict'], 'auto_reason': c['reason'], 'auto_severity': 'none'})
        packet = packets[result['blind_id']]
        for unit in units(packet['text']):
            for dimension in DIMENSIONS:
                independent_rows.append({'blind_id': result['blind_id'],
                    'item_id': unit['id'] + ':' + dimension, 'dimension': dimension,
                    'report_quote': unit['text'], 'basis': '独立核对完整 reference.json，填写依据于 human_reason'})
        for fact in packet['reference']['facts']:
            if fact['critical'] and packet['reference']['checklist_status'] == 'reviewed':
                independent_rows.append({'blind_id': result['blind_id'], 'item_id': fact['id'],
                    'dimension': 'key_evidence_completeness', 'report_quote': '', 'basis': fact['text']})
    base = ['blind_id', 'item_id', 'dimension', 'report_quote', 'basis']
    human = ['reviewer_id', 'human_verdict', 'human_severity', 'human_reason', 'additional_missed_errors']
    export_csv(directory / 'human_review' / 'spotcheck.csv', auto_rows, base + ['auto_verdict', 'auto_reason', 'auto_severity'] + human)
    # The doctor receives whole reports and facts; this sheet contains no model labels or reasons.
    export_csv(directory / 'human_review' / 'doctor_independent.csv', independent_rows, base + human)
    write(directory / 'human_review' / 'sample.json', {'blind_ids': selected, 'seed': args.seed,
        'kind': '人工抽查', 'limitations': '按方法及问题数量分层抽样，非随机代表性样本；用于排查误判，不能据此推断临床质量。'})
    print('人工抽查/独立医生复核材料：' + str(directory / 'human_review'))


def compare(args):
    """Compare independent filled sheets; do not treat unreviewed rows as agreement."""
    directory = args.study.resolve()
    with (directory / 'item_results.csv').open(encoding='utf-8-sig', newline='') as handle:
        automatic = {(r['blind_id'], r['item_id']): r for r in csv.DictReader(handle)
                     if r['evaluation_status'] == 'completed'}
    with args.sheet.open(encoding='utf-8-sig', newline='') as handle:
        human = list(csv.DictReader(handle))
    paired = defaultdict(list)
    severity_pairs = defaultdict(list)
    matched, missed, both, additional = [], [], [], []
    seen = set()
    for row in human:
        if row.get('additional_missed_errors', '').strip():
            additional.append(row)
        if not row.get('human_verdict', '').strip():
            continue
        reviewer = row.get('reviewer_id', '').strip()
        if not reviewer:
            raise ValueError('filled_sheet_requires_reviewer_id')
        key = (row['blind_id'], row['item_id'])
        if (reviewer, *key) in seen:
            raise ValueError('duplicate_reviewer_item')
        seen.add((reviewer, *key))
        if key not in automatic:
            raise ValueError('human_item_has_no_completed_automatic_result')
        auto = automatic[key]
        dimension = auto['dimension']
        valid = {'covered', 'omitted', 'uncertain'} if dimension == 'key_evidence_completeness' else VERDICTS[dimension]
        if row['human_verdict'] not in valid:
            raise ValueError('invalid_human_verdict')
        paired[(reviewer, dimension)].append((auto['auto_verdict'], row['human_verdict']))
        if row.get('human_severity'):
            if row['human_severity'] not in {'none', 'minor', 'major', 'critical'}:
                raise ValueError('invalid_human_severity')
            severity_pairs[(reviewer, dimension)].append((auto['auto_severity'], row['human_severity']))
        human_problem = row['human_verdict'] in PROBLEMS or (dimension == 'key_evidence_completeness' and row['human_verdict'] == 'omitted')
        auto_problem = auto['auto_verdict'] in PROBLEMS or (dimension == 'key_evidence_completeness' and auto['auto_verdict'] == 'omitted')
        comparison = {**row, 'auto_verdict': auto['auto_verdict'], 'auto_severity': auto['auto_severity']}
        matched.append(comparison)
        if human_problem and auto_problem:
            both.append(comparison)
        if human_problem and not auto_problem and row.get('human_severity') in {'major', 'critical'}:
            missed.append(comparison)
    result = {'label': '自动评价与人工评价的一致性分析',
        'agreement': [{'reviewer_id': reviewer, 'dimension': dim, **agreement(values),
            'severity_agreement': agreement(severity_pairs[(reviewer, dim)])}
            for (reviewer, dim), values in sorted(paired.items())],
        'errors_found_by_both': both, 'serious_errors_missed_by_llm': missed,
        'additional_human_errors': additional, 'matched_items': len(matched),
        'limitations': ['仅统计已填写且可匹配的条目，空白不算一致。',
            '人工抽查不是临床验证；医生独立评价也需说明人数、资质、盲法与抽样范围。',
            '按方法/问题水平分层的少量样本不适合直接判断全体方法排名是否反转。',
            'Cohen κ 为未加权名义一致性；分母与缺失必须一同报告，单一类别时 κ 可能无法定义。']}
    write(directory / 'human_comparison.json', result)
    print('人工与自动评价比较：' + str(directory / 'human_comparison.json'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare', help='只从患者JSON整理独立事实候选清单，不读取报告')
    p.add_argument('--patients-dir', type=Path, default=ROOT / 'output/rwe_batch_screenshots/patients')
    p.add_argument('--output', type=Path, default=ROOT / 'output/report_evaluation/references')
    p.add_argument('--refresh-drafts', action='store_true', help='刷新未选择关键事实的草稿，保留已标注清单')
    p.set_defaults(action=prepare)
    p = sub.add_parser('manifest-from-batch', help='登记既有批处理的全部计划报告')
    p.add_argument('--batch-dir', type=Path, default=ROOT / 'output/rwe_batch_screenshots')
    p.add_argument('--references', type=Path, default=ROOT / 'output/report_evaluation/references')
    p.add_argument('--method', default='NeuroGRA')
    p.add_argument('--output', type=Path, default=ROOT / 'output/report_evaluation/report_manifest.json')
    p.set_defaults(action=manifest_from_batch)
    p = sub.add_parser('blind', help='从多组报告清单创建冻结的匿名评价集')
    p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--seed', type=int, default=20261016)
    p.set_defaults(action=blind)
    p = sub.add_parser('run', help='独立调用LLM；不读取方法映射、不检索生成系统知识库')
    p.add_argument('--study', type=Path, required=True)
    p.add_argument('--model', default='qwen3.6:35b')
    p.add_argument('--base-url', default='http://127.0.0.1:11434')
    p.add_argument('--context-tokens', type=int, default=32768)
    p.add_argument('--batch-size', type=int, default=4)
    p.add_argument('--timeout', type=float, default=300)
    p.add_argument('--report-timeout', type=float, default=1800)
    p.add_argument('--check', action='store_true', help='只核验冻结数据与上下文容量，不调用模型')
    p.set_defaults(action=run)
    p = sub.add_parser('summarize')
    p.add_argument('--study', type=Path, required=True)
    p.set_defaults(action=lambda args: summarize(args.study.resolve()))
    p = sub.add_parser('review-sheet')
    p.add_argument('--study', type=Path, required=True)
    p.add_argument('--count', type=int, default=9)
    p.add_argument('--seed', type=int, default=20261016)
    p.set_defaults(action=review_sheet)
    p = sub.add_parser('compare', help='对填写的独立复核表计算一致性及严重错误漏检')
    p.add_argument('--study', type=Path, required=True)
    p.add_argument('--sheet', type=Path, required=True)
    p.set_defaults(action=compare)
    args = parser.parse_args()
    if getattr(args, 'batch_size', 1) < 1:
        parser.error('--batch-size 必须大于零')
    args.action(args)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
        raise SystemExit(2)
