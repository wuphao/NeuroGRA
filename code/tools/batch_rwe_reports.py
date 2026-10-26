"""Export a patient list using the existing exporter, then run the clinical system.

Defaults to the 27 patient numbers supplied in the three screenshots. All
configuration of models, agents and knowledge sources comes from code/main.py.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import sys
import traceback

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'code'))
from export_rwe_patient import export_arguments, export_patient


def now():
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds')


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    temporary.replace(path)


def read_numbers(path):
    numbers = [line.split('#', 1)[0].strip() for line in path.read_text(encoding='utf-8-sig').splitlines()]
    numbers = [number for number in numbers if number]
    if not numbers or any(not re.fullmatch(r'\d{3}_S_\d{4}', number) for number in numbers):
        raise ValueError('患者清单必须每行一个编号，例如 041_S_5100，且不能为空。')
    if len(set(numbers)) != len(numbers):
        raise ValueError('患者清单包含重复编号。')
    return numbers


def patient_json(path, number):
    raw = json.loads(path.read_text(encoding='utf-8-sig'))
    if raw.get('patient', {}).get('patient_number') != number:
        raise ValueError('导出文件患者编号不匹配：' + number)
    from neurogra.clinical.schemas import PatientInput
    PatientInput.model_validate(raw)
    return raw


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_signature(config):
    from neurogra.clinical.execution import execution_binding
    from neurogra.clinical.utils import fingerprint
    return fingerprint({'configuration': config.model_dump(mode='json'), 'execution': execution_binding()})


class BatchAlreadyRunning(RuntimeError):
    pass


@contextmanager
def batch_lock(directory):
    """Kernel-owned lock: a crash releases ownership, even if the file remains."""
    path = directory / 'batch.lock'
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(descriptor, 'r+b') as handle:
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                raise
            raise BatchAlreadyRunning(
                '该输出目录已有批处理正在运行，请等待其结束；不要删除活动锁或重复启动。'
                f'\n进度：{directory / "index.md"}\n汇总：{directory / "summary.json"}'
            ) from None

        def metadata(status):
            handle.seek(0)
            handle.write((json.dumps({'pid': os.getpid(), 'status': status,
                'updated_at': now()}, ensure_ascii=False) + '\n').encode('utf-8'))
            handle.truncate()
            handle.flush()

        try:
            metadata('running')
            yield
        finally:
            try:
                metadata('released')
            finally:
                handle.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        # Keep the same file/inode so concurrent contenders cannot lock different files.


def worker(patient_file, directory, number, task_mode='blinded_classification'):
    from neurogra.clinical.workflow import analyze_patient
    from neurogra_common.llm_log import configure_llm_log, record_llm_event
    settings = runpy.run_path(str(ROOT / 'code/main.py'))
    config = settings['build_config']()
    config.diagnosis_task_mode = task_mode
    signature = run_signature(config)
    raw = patient_json(patient_file, number)
    directory.mkdir(parents=True, exist_ok=True)
    configure_llm_log(directory / 'llm_calls.jsonl')
    record_llm_event('session_start', patient_file=str(patient_file), patient_number=number)
    try:
        result = analyze_patient(raw, config)
    except Exception as exc:
        traceback.print_exc()
        record_worker_failure(patient_file, directory, number, config, exc, signature)
        return
    source_manifest = patient_file.with_suffix('.masking.json')
    if source_manifest.is_file():
        provenance_path = Path(result.report_paths['provenance'])
        provenance = json.loads(provenance_path.read_text(encoding='utf-8'))
        provenance['input_label_masking'] = json.loads(source_manifest.read_text(encoding='utf-8'))
        save(provenance_path, provenance)
    # Copy all artifacts together so relative links in the report keep working.
    for source in result.report_paths.values():
        shutil.copy2(source, directory / Path(source).name)
    save(directory / 'result.json', {
        'pipeline_version': 'layered_diagnosis_v2', 'task_mode': task_mode,
        'run_signature': signature,
        'patient_number': number, 'input_sha256': digest(patient_file),
        'run_id': result.run_id, 'status': result.status,
        'stop_reason': result.stop_reason,
        'assessment_status': result.report.assessment_status,
        'diagnostic_assessment_status': result.report.diagnostic_assessment.status if result.report.diagnostic_assessment else None,
        'diagnosis_status': result.report.diagnostic_outcome.status if result.report.diagnostic_outcome else None,
        'primary_working_label': result.report.diagnostic_outcome.primary_working_label if result.report.diagnostic_outcome else None,
        'evaluation_class': result.report.diagnostic_outcome.evaluation_class if result.report.diagnostic_outcome else None,
        'report_paths': result.report_paths, 'finished_at': now(),
    })
    record_llm_event('session_complete', run_id=result.run_id, status=result.status)


def record_worker_failure(patient_file, directory, number, config, error, signature):
    """A pre-diagnosis crash produces a technical receipt, never a fake diagnosis."""
    from neurogra.clinical.schemas import DiagnosticOutcome, DiagnosisLayer, FinalReport
    from neurogra.clinical.storage import RunStore
    from neurogra.clinical.reporting import TEMPLATE_VERSION
    store = RunStore(config.resolve(config.database))
    run_id = store.create_run(config.model_dump(mode='json'), {'llm': 0, 'seconds': 1})
    detail = type(error).__name__ + ': ' + str(error)
    outcome = DiagnosticOutcome(status='technical_failure', task_mode=config.diagnosis_task_mode,
        layers=[DiagnosisLayer(level=level, status='technical_failure', review_status='unavailable',
                    reasoning='完整分析流程在诊断发布前发生技术错误。', technical_errors=[detail])
                for level in ('syndrome', 'etiology')],
        conclusion='本次诊断技术核验未完成，未发布新的疾病判断。',
        evaluation_class_mapping_reason='技术失败，不从既往诊断或草稿补填预测。')
    report = FinalReport(patient_id=number, run_id=run_id, status='partial', assessment_status='unresolved',
        summary=outcome.conclusion, claims=[], unresolved=[detail], citations=[],
        report_template_version=TEMPLATE_VERSION, diagnostic_outcome=outcome)
    directory.mkdir(parents=True, exist_ok=True)
    save(directory / 'report.json', report.model_dump(mode='json'))
    save(directory / 'provenance.json', {'run_id': run_id, 'patient_id': number,
        'records': [], 'observations': [], 'evidence': [], 'technical_failure_receipt': True,
        'input_file': str(patient_file), 'input_sha256': digest(patient_file),
        'diagnostic_outcome': outcome.model_dump(mode='json')})
    (directory / 'report.md').write_text('# 本次诊断结果\n\n患者：' + number + '\n\n' + outcome.conclusion +
        '\n\n故障信息见运行审计；原始输入保留，不能将本结果视为患者资料不足或医学判断。\n', encoding='utf-8')
    (directory / 'audit.md').write_text('# 技术失败回执\n\n' + detail + '\n\n' +
        '此回执另建运行编号保存。原流程未成功结束，原模型调用及错误保留在console.log和llm_calls.jsonl。\n', encoding='utf-8')
    (directory / 'data_appendix.md').write_text('# 输入范围\n\n原输入文件：' + str(patient_file) +
        '\n\n本回执未生成新的患者事实或检查结果。\n', encoding='utf-8')
    store.save(run_id, 'diagnostic_outcome', 'main', outcome)
    store.event(run_id, 'worker_failure_receipt', {'reason': detail, 'source_file': str(patient_file)})
    store.status(run_id, 'failed')
    save(directory / 'result.json', {'pipeline_version': 'layered_diagnosis_v2', 'run_signature': signature,
        'task_mode': config.diagnosis_task_mode, 'patient_number': number, 'input_sha256': digest(patient_file),
        'run_id': run_id, 'status': 'partial', 'stop_reason': 'worker_technical_failure',
        'diagnosis_status': 'technical_failure', 'primary_working_label': None,
        'evaluation_class': None, 'finished_at': now()})


def resumable_result(directory, number, input_path, task_mode=None, signature=None):
    path = directory / 'result.json'
    if not path.exists():
        return None
    result = json.loads(path.read_text(encoding='utf-8'))
    if task_mode is not None and (result.get('pipeline_version') != 'layered_diagnosis_v2' or result.get('task_mode') != task_mode):
        raise ValueError('已有报告使用不同的诊断版本或任务模式，请使用新的 --output-dir。')
    if signature is not None and result.get('run_signature') != signature:
        raise ValueError('已有报告的代码或配置不同，请使用新的 --output-dir。')
    if result['patient_number'] != number or result['input_sha256'] != digest(input_path):
        raise ValueError('已有报告与输入不一致，请使用新的 --output-dir：' + number)
    if not all((directory / name).is_file() for name in ('report.md', 'report.json', 'provenance.json', 'audit.md', 'data_appendix.md')):
        raise ValueError('已有报告文件不完整，请使用新的 --output-dir：' + number)
    return result


def batch(args):
    numbers = read_numbers(args.patients_file)
    if args.check:
        print(json.dumps({'patients': numbers, 'count': len(numbers), 'output_dir': str(args.output_dir)}, ensure_ascii=False, indent=2))
        return 0
    directory = args.output_dir.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    with batch_lock(directory):
        return run_batch(args, directory, numbers)


def run_batch(args, directory, numbers):
    summary_path = directory / 'summary.json'
    summary = {'started_at': now(), 'patients': []}
    task_mode = getattr(args, 'task_mode', 'blinded_classification')
    config = runpy.run_path(str(ROOT / 'code/main.py'))['build_config']()
    config.diagnosis_task_mode = task_mode
    signature = run_signature(config)
    if summary_path.exists():
        summary = json.loads(summary_path.read_text(encoding='utf-8'))
        if summary.get('pipeline_version') != 'layered_diagnosis_v2' and (directory / 'reports').exists():
            raise ValueError('旧版批次保留只读，请使用新的 --output-dir 运行修复版。')
        if [row['patient_number'] for row in summary['patients']] != numbers:
            raise ValueError('此批次的患者清单已改变，请使用新的 --output-dir。')
        if summary.get('task_mode', task_mode) != task_mode:
            raise ValueError('批次任务模式已改变，请使用新的 --output-dir。')
    else:
        summary['patients'] = [{'patient_number': n, 'status': 'pending'} for n in numbers]
    summary.pop('finished_at', None)
    summary.update(task_mode=task_mode, pipeline_version='layered_diagnosis_v2')

    def checkpoint():
        summary['updated_at'] = now()
        summary['counts'] = dict(Counter(row['status'] for row in summary['patients']))
        save(summary_path, summary)
        lines = ['# RWE 批量报告', '', '| 患者编号 | 流程状态 | 诊断状态 | 本次工作判断 | 报告 |', '| --- | --- | --- | --- | --- |']
        for row in summary['patients']:
            link = f"[查看报告](reports/{row['patient_number']}/report.md)" if row.get('batch_report') else ''
            label = str(row.get('primary_working_label') or '—').replace('|', '\\|').replace('\n', ' ')
            lines.append(f"| {row['patient_number']} | {row['status']} | {row.get('diagnosis_status') or '—'} | {label} | {link} |")
        (directory / 'index.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')

    checkpoint()
    # Export the whole cohort before any lengthy model calls.
    for index, row in enumerate(summary['patients'], 1):
        number = row['patient_number']
        path = directory / 'patients' / f'patient_{number}_analysis.json'
        row['patient_json'] = str(path)
        print(f'[{index}/{len(numbers)}] 导出 {number}', flush=True)
        try:
            if not path.exists():
                source_dir = getattr(args, 'source_dir', None)
                source = source_dir / path.name if source_dir else None
                if source and source.is_file() and args.trim_mode == 'short':
                    source_raw = patient_json(source, number)
                    save(path, source_raw)
                    source_manifest = source.with_suffix('.manifest.json')
                    if source_manifest.is_file():
                        shutil.copy2(source_manifest, path.with_suffix('.source.manifest.json'))
                else:
                    export_patient(export_arguments(number, path, trim_mode=args.trim_mode,
                                                    detail_years=args.detail_years))
            if task_mode == 'blinded_classification':
                from neurogra.clinical.label_masking import mask_labels
                masked, masking = mask_labels(json.loads(path.read_text(encoding='utf-8')))
                if masking['removed_paths']:
                    save(path, masked)
                    save(path.with_suffix('.masking.json'), masking)
            patient_json(path, number)
            row['export_sha256'] = digest(path)
            row['status'] = 'exported'
            row.pop('error', None)
        except Exception as exc:
            row.update(status='export_failed', error=f'{type(exc).__name__}: {exc}')
            print(row['error'], flush=True)
        checkpoint()
    if not args.export_only:
        for index, row in enumerate(summary['patients'], 1):
            if row['status'] != 'exported':
                continue
            number = row['patient_number']
            path = Path(row['patient_json'])
            reports = directory / 'reports' / number
            reports.mkdir(parents=True, exist_ok=True)
            print(f'[{index}/{len(numbers)}] 生成报告 {number}', flush=True)
            try:
                result = resumable_result(reports, number, path, task_mode, signature)
                if result and result.get('diagnosis_status') == 'technical_failure':
                    result = None
                if result is None:
                    row.update(status='analyzing', started_at=now())
                    checkpoint()
                    with (reports / 'console.log').open('a', encoding='utf-8') as log:
                        subprocess.run([sys.executable, '-u', str(Path(__file__).resolve()),
                            '--worker', '--patient-file', str(path), '--worker-output', str(reports),
                            '--patient-number', number, '--task-mode', task_mode], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                            check=True, timeout=args.patient_timeout)
                    result = resumable_result(reports, number, path, task_mode, signature)
                    if result is None:
                        raise RuntimeError('子进程未生成 result.json')
                row.update(result)
                row['batch_report'] = str(reports / 'report.md')
            except Exception as exc:
                row.update(status='analysis_failed', error=f'{type(exc).__name__}: {exc}')
                print(row['error'], flush=True)
            checkpoint()
    summary['finished_at'] = now()
    checkpoint()
    print(json.dumps(summary['counts'], ensure_ascii=False), flush=True)
    print('汇总：' + str(summary_path), flush=True)
    return int(any(row['status'] not in {'completed', 'exported'} for row in summary['patients']))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--patients-file', type=Path, default=ROOT / 'configs/rwe_batch_patients.txt')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'output/rwe_batch_repaired_v2')
    parser.add_argument('--source-dir', type=Path, default=ROOT / 'output/rwe_batch_screenshots/patients')
    parser.add_argument('--task-mode', choices=('follow_up', 'blinded_classification'), default='blinded_classification')
    parser.add_argument('--trim-mode', choices=('short', 'smoke', 'none'), default='short')
    parser.add_argument('--detail-years', type=int, nargs='+', default=[])
    parser.add_argument('--patient-timeout', type=float, default=1500)
    parser.add_argument('--export-only', action='store_true')
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--patient-file', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--worker-output', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--patient-number', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.patient_timeout <= 0:
        parser.error('--patient-timeout 必须大于 0')
    if args.worker:
        worker(args.patient_file, args.worker_output, args.patient_number, args.task_mode)
        return 0
    return batch(args)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except BatchAlreadyRunning as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2)
    except Exception:
        traceback.print_exc()
        raise SystemExit(2)
