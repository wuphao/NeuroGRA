"""Prepare or run paired single-LLM/no-KG/plain-triple/full-system experiments."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import runpy
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'code'))

from neurogra.benchmarks.ablation import (GROUPS, VERSION, read, write, now, prepare_case,
    pin_knowledge, model_identity, run_worker, implementation_binding)
from neurogra.clinical.utils import fingerprint


def render_index(directory, results):
    write(directory / 'results.json', results)
    lines = ['# 图谱消融预实验', '', '工程试跑结果；不代表诊断准确率或临床验证。', '',
        '| 患者 | 组别 | 执行 | 综合征 | 病因 | 对照类别 | 诊断状态 | 秒 |',
        '|---|---|---|---|---|---|---|---:|']
    for r in results:
        link = str(Path('cases') / r['patient_number'] / r['group'] / 'report.md').replace('\\', '/')
        lines.append(f"|{r['patient_number']}|[{r['group']}]({link})|{r['execution_status']}|"
            f"{r.get('syndrome_class', '')}|{r.get('etiology_class', '')}|{r.get('cohort_class', '')}|"
            f"{r.get('outcome_status', '')}|{r.get('seconds', 0):.1f}|")
    (directory / 'index.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def execute(directory):
    manifest = read(directory / 'manifest.json')
    if manifest['implementation'] != implementation_binding():
        raise ValueError('prepared_implementation_changed_use_new_output_directory')
    from neurogra.clinical.config import ClinicalConfig
    config = ClinicalConfig.model_validate(read(directory / 'base_config.json'))
    if fingerprint(config.model_dump(mode='json')) != manifest['base_config_hash']:
        raise ValueError('prepared_configuration_changed')
    identity = model_identity(config)
    if manifest['generation_model'] is None:
        manifest['generation_model'] = identity
    elif manifest['generation_model'] != identity:
        raise ValueError('prepared_model_changed')
    manifest.update({'status': 'running', 'started_at': now()})
    write(directory / 'manifest.json', manifest)
    results = []
    for case in manifest['cases']:
        case_dir = directory / 'cases' / case['patient_number']
        for group in manifest['groups']:
            destination = case_dir / group
            receipt = destination / 'status.json'
            if destination.exists():
                if not receipt.is_file() or read(receipt).get('execution_status') not in {'completed', 'failed'}:
                    raise ValueError('incomplete_group_requires_new_output_directory:' + str(destination))
                result = read(receipt)
                if result.get('visible_hash') != case['visible_hash']:
                    raise ValueError('existing_group_patient_mismatch')
            else:
                console = case_dir / (group + '.console.log')
                print(case['patient_number'] + ' / ' + group, flush=True)
                try:
                    with console.open('w', encoding='utf-8') as log:
                        process = subprocess.run([sys.executable, str(Path(__file__).resolve()),
                            '--worker', group, '--run-prepared', str(directory), '--case', str(case_dir)],
                            stdout=log, stderr=subprocess.STDOUT,
                            timeout=config.budget.seconds + 90)
                    if not receipt.is_file():
                        raise RuntimeError('worker_failed_without_receipt:' + str(process.returncode))
                    result = read(receipt)
                except (subprocess.TimeoutExpired, RuntimeError) as exc:
                    result = {'patient_number': case['patient_number'], 'group': group,
                        'visible_hash': case['visible_hash'], 'execution_status': 'failed',
                        'outcome_status': 'technical_failure', 'cohort_class': 'UNRESOLVED',
                        'error': {'type': type(exc).__name__, 'message': str(exc)}}
                    write(receipt, result)
                    write(destination / 'prediction.json', result)
                    (destination / 'evaluation_report.md').write_text(
                        '# 认知与病因分析报告\n\n本次生成或技术校验失败，未发布可用判断。\n', encoding='utf-8')
            results.append(result)
            render_index(directory, results)
            print('  ' + result['execution_status'] + ' / ' + result.get('outcome_status', '') +
                  ' / ' + result.get('cohort_class', ''), flush=True)
    manifest.update({'status': 'finished', 'finished_at': now(),
        'completed_groups': sum(r['execution_status'] == 'completed' for r in results),
        'failed_groups': sum(r['execution_status'] == 'failed' for r in results)})
    write(directory / 'manifest.json', manifest)
    write(directory / 'evaluation_manifest.json', {'status': 'reference_drafts_require_independent_review',
        'reports': [{'case_id': c['patient_number'], 'method': group,
            'generation_model': manifest['generation_model']['name'],
            'report_path': str((directory / 'cases' / c['patient_number'] / group / 'evaluation_report.md').resolve()),
            'reference_path': str((directory / 'cases' / c['patient_number'] / 'reference_draft.json').resolve()),
            'mask_tokens': ['NeuroGRA', 'neurogra']} for c in manifest['cases'] for group in manifest['groups']]})
    return 1 if manifest['failed_groups'] else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--patient', type=Path, action='append', help='Repeat for multiple patient JSONs')
    parser.add_argument('--patients-file', type=Path, help='One patient number per line')
    parser.add_argument('--source-dir', type=Path, default=ROOT / 'output/rwe_batch_repaired_v2/patients')
    parser.add_argument('--output', type=Path, help='Must be a new directory')
    parser.add_argument('--groups', nargs='+', choices=GROUPS, default=list(GROUPS))
    parser.add_argument('--prepare-only', action='store_true', help='Offline, no model call or graph write')
    parser.add_argument('--run-prepared', type=Path)
    parser.add_argument('--worker', choices=GROUPS, help=argparse.SUPPRESS)
    parser.add_argument('--case', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker:
        if not args.run_prepared or not args.case:
            parser.error('worker requires prepared directory and case')
        return run_worker(args.run_prepared, args.case, args.worker)
    if args.run_prepared:
        if args.patient or args.patients_file or args.output or args.prepare_only:
            parser.error('--run-prepared cannot change patient/output/preparation')
        from batch_rwe_reports import batch_lock
        with batch_lock(args.run_prepared.resolve()):
            return execute(args.run_prepared.resolve())
    if args.patient and args.patients_file:
        parser.error('choose --patient or --patients-file')
    patients = args.patient or []
    if args.patients_file:
        from batch_rwe_reports import read_numbers
        for number in read_numbers(args.patients_file):
            candidates = [args.source_dir / ('patient_' + number + '_analysis.json'), args.source_dir / (number + '.json')]
            patients.append(next((path for path in candidates if path.is_file()), candidates[0]))
    if not patients:
        parser.error('provide --patient or --patients-file')
    if len(set(args.groups)) != len(args.groups):
        parser.error('duplicate group')
    config = runpy.run_path(str(ROOT / 'code/main.py'))['build_config']().model_copy(deep=True)
    config.diagnosis_task_mode = 'blinded_classification'
    config.research_classification = True
    config.model.context_tokens = config.diagnosis_context_tokens
    config.model.max_input_chars = 100000
    knowledge = pin_knowledge(config)
    raws = [(path.resolve(), read(path)) for path in patients]
    directory = (args.output or ROOT / 'output/ablation' / datetime.now().strftime('%Y%m%d-%H%M%S')).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    write(directory / 'knowledge.json', knowledge)
    write(directory / 'base_config.json', config.model_dump(mode='json'))
    cases, seen = [], set()
    for path, raw in raws:
        from neurogra.clinical.schemas import PatientInput
        number = PatientInput.model_validate(raw).patient_id
        # Patient identifiers become directory names only after validation.
        if not number or any(c in number for c in '/\\:') or number in {'.', '..'} or number in seen:
            raise ValueError('invalid_or_duplicate_patient_identifier')
        seen.add(number)
        cases.append(prepare_case(raw, config, directory / 'cases' / number, path))
    manifest = {'version': VERSION, 'status': 'prepared', 'created_at': now(),
        'groups': args.groups, 'cases': cases, 'generation_model': None,
        'base_config_hash': fingerprint(config.model_dump(mode='json')),
        'implementation': implementation_binding(), 'knowledge_hash': fingerprint(knowledge),
        'interpretation': '同病例配对预实验；未载入真实标签；未执行自动评分或医生验证。',
        'group_definitions': {'single_llm': '患者事实+Prompt，单次生成，至多一次结构修复；无检索与医学复核',
            'no_kg': '相同临床流程+共同文本库；所有角色关闭KG',
            'plain_triples': '相同临床流程+共同文本库+同源三元组投影；不运行条件引擎或图谱补全',
            'full': '相同临床流程+共同文本库+完整图谱；现有适用性与补查机制'}}
    write(directory / 'manifest.json', manifest)
    print('Prepared: ' + str(directory), flush=True)
    if args.prepare_only:
        return 0
    from batch_rwe_reports import batch_lock
    with batch_lock(directory):
        return execute(directory)


if __name__ == '__main__':
    raise SystemExit(main())
