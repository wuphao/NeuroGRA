"""Run the existing 27 RWE exports through the local JSON MedGraphRAG adapter.

Sequential workers, immutable input copies, per-case attempts, verified resume,
and JSON/CSV/Markdown summaries. --check and --prepare-only never call models.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import traceback

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'code'))
sys.path.insert(0, str(ROOT / 'code/tools'))

import yaml
from batch_rwe_reports import batch_lock, read_numbers, BatchAlreadyRunning
from audit_medgraphrag_run import audit
from medgraphrag_provenance import adapter_files
from neurogra.benchmarks.patient_view import prepare_patient, digest
from neurogra.benchmarks.medgraphrag.pipeline import Prediction
from neurogra.benchmarks.medgraphrag.retrieval import compact_patient, score_trends
from neurogra.benchmarks.medgraphrag.runtime import Config, LocalModel, request_fits, write_json

ENTRY = ROOT / 'code/tools/run_medgraphrag_baseline.py'


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def sha(path):
    hasher = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            hasher.update(block)
    return hasher.hexdigest()


def settings(path):
    path = path.resolve()
    config = Config.model_validate(yaml.safe_load(path.read_text(encoding='utf-8-sig')) or {})
    config.validate_local()
    if config.graph_backend != 'json':
        raise ValueError('batch_requires_json_graph_backend')
    for name in ('knowledge_manifest', 'knowledge_cache', 'model_cache'):
        value = getattr(config, name)
        if value:
            setattr(config, name, str((path.parent / value).resolve()))
    if not config.knowledge_manifest:
        raise ValueError('knowledge_manifest_required')
    return config


def plan_inputs(numbers, source_dir, config):
    rows = []
    for number in numbers:
        source = (source_dir / f'patient_{number}_analysis.json').resolve()
        row = {'patient_number': number, 'source': str(source), 'status': 'ready', 'sidecars': {}}
        try:
            raw = read(source)
            if raw.get('patient', {}).get('patient_number') != number:
                raise ValueError('patient_number_mismatch')
            prepared = prepare_patient(raw, task='clinical_review')
            compact, _ = compact_patient(prepared)
            payload = {'task': 'clinical_review', 'labels': None, 'patient': compact,
                       'score_trends': score_trends(prepared['sources'])}
            row.update(input_sha256=sha(source), input_hash=prepared['manifest']['input_hash'],
                       fact_count=len(prepared['sources']), visible_hash=prepared['manifest']['visible_hash'],
                       presentation_utf8_bytes=len(json.dumps(payload, ensure_ascii=False).encode('utf-8')))
            # Conservative offline check; worker performs the exact final evidence budget check.
            row['context_headroom_check'] = request_fits(config.answer_limits(), 'clinical assessment',
                                                        payload, Prediction, reserve_bytes=12000)
            if not row['context_headroom_check']:
                raise ValueError('insufficient_answer_context_headroom: increase_answer_context_tokens')
            trim = next((p for p in (source.with_suffix('.source.manifest.json'),
                                    source.with_suffix('.manifest.json')) if p.is_file()), None)
            for name, path in (('trim', trim), ('masking', source.with_suffix('.masking.json'))):
                if path and path.is_file():
                    read(path)  # Reject malformed provenance before any inference.
                    row['sidecars'][name] = {'path': str(path), 'sha256': sha(path)}
        except Exception as exc:
            row.update(status='input_failed', error=f'{type(exc).__name__}: {exc}')
        rows.append(row)
    return rows


def binding(config, config_path, patients_file, plan):
    files = adapter_files()
    files += [ENTRY, Path(__file__), ROOT / 'code/tools/audit_medgraphrag_run.py',
              ROOT / 'code/tools/batch_rwe_reports.py', ROOT / 'code/tools/medgraphrag_provenance.py']
    implementation = {str(p.relative_to(ROOT)): sha(p) for p in sorted(files)
                      if not p.name.startswith('test_')}
    knowledge_path = Path(config.knowledge_manifest)
    knowledge = read(knowledge_path)
    corpus_files = {str(knowledge_path): sha(knowledge_path)}
    for doc in knowledge['documents']:
        for key in ('path', 'prebuilt_graph'):
            if key in doc:
                path = (knowledge_path.parent / doc[key]).resolve()
                corpus_files[str(path)] = sha(path)
    snapshot = knowledge.get('graph_implementation_source')
    if snapshot:
        path = (knowledge_path.parent / snapshot).resolve()
        corpus_files[str(path)] = sha(path)
    return {'version': 'medgraphrag-rwe-batch-v1', 'task': 'clinical_review',
            'config': config.model_dump(), 'config_path': str(config_path.resolve()),
            'config_sha256': sha(config_path), 'patients_file_sha256': sha(patients_file),
            'implementation': implementation, 'corpus_files': corpus_files,
            'patients': plan,
            'scope': 'Existing exports including their trimming/masking; no new cutoff applied. '
                     'Clinical review, not an audited blinded accuracy experiment.'}


def snapshot_inputs(row, directory):
    directory.mkdir()
    source = Path(row['source'])
    if sha(source) != row['input_sha256']:
        raise ValueError('source_changed_after_planning')
    target = directory / source.name
    shutil.copyfile(source, target)
    if sha(target) != row['input_sha256']:
        raise ValueError('snapshot_checksum_mismatch')
    for name, entry in row['sidecars'].items():
        path = Path(entry['path'])
        if sha(path) != entry['sha256']:
            raise ValueError('source_sidecar_changed_after_planning')
        destination = target.with_suffix('.manifest.json' if name == 'trim' else '.masking.json')
        shutil.copyfile(path, destination)
        if sha(destination) != entry['sha256']:
            raise ValueError('snapshot_sidecar_checksum_mismatch')
    write_json(directory / 'source_provenance.json', row)
    return target


def artifacts(directory, mode):
    names = (['status.json', 'manifest.json', 'source_map.json'] if mode == 'prepare' else
             ['status.json', 'manifest.json', 'source_map.json', 'model_patient.json',
              'score_trends.json', 'retrieval.json', 'labels.json', 'prediction.json', 'report.md', 'run_audit.json'])
    return {name: sha(directory / name) for name in names}


def completed_receipt(row, attempt, signature):
    output = attempt / 'run'
    result = audit(output)
    if sha(attempt / 'input' / Path(row['source']).name) != row['input_sha256']:
        raise ValueError('input_snapshot_changed')
    if read(output / 'manifest.json')['input_hash'] != row['input_hash']:
        raise ValueError('worker_used_different_input')
    prediction = read(output / 'prediction.json')
    return {'patient_number': row['patient_number'], 'status': 'completed', 'binding_hash': signature,
            'attempt': str(attempt), 'input_sha256': row['input_sha256'],
            'labels': result['labels'], 'usage': prediction['usage'],
            'report': str(output / 'report.md'), 'labels_path': str(output / 'labels.json'),
            'artifact_sha256': artifacts(output, 'run'), 'finished_at': now()}


def resume_case(row, case, signature):
    path = case / 'latest.json'
    if not path.is_file():
        return None
    receipt = read(path)
    if receipt['binding_hash'] != signature:
        raise ValueError('case_binding_mismatch')
    if receipt['status'] not in {'completed', 'running'} or receipt.get('mode') == 'prepare':
        return None
    attempt = Path(receipt['attempt'])
    if not attempt.is_relative_to(case):
        raise ValueError('attempt_outside_case_directory')
    output = attempt / 'run'
    status = output / 'status.json'
    if not status.is_file() or read(status).get('status') != 'completed':
        return None
    for name, expected in receipt.get('artifact_sha256', {}).items():
        if sha(output / name) != expected:
            raise ValueError('completed_artifact_changed: ' + name)
    # Recover a finished worker even if the parent exited before publishing its receipt.
    return completed_receipt(row, attempt, signature)


def checkpoint(directory, summary):
    summary['updated_at'] = now()
    summary['counts'] = dict(Counter(row['status'] for row in summary['patients']))
    write_json(directory / 'summary.json', summary)
    fields = ['patient_number', 'status', 'cognitive_stage', 'etiology_label', 'evaluation_class',
              'assessment_status', 'report', 'labels_path', 'attempt', 'error']
    rows = [{**row, **row.get('labels', {})} for row in summary['patients']]
    temporary = directory / 'labels.csv.tmp'
    with temporary.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(directory / 'labels.csv')
    lines = ['# MedGraphRAG 27 例批处理', '',
             '标签是模型评估结果；流程成功与病因是否未决分别记录。', '',
             '| 患者 | 流程状态 | 认知阶段 | 病因 | 评估状态 | 报告 |', '|---|---|---|---|---|---|']
    for row in rows:
        link = ('[报告](' + Path(row['report']).relative_to(directory).as_posix() + ')') if row.get('report') else ''
        lines.append(f"| {row['patient_number']} | {row['status']} | {row.get('cognitive_stage', '')} | "
                     f"{row.get('etiology_label', '')} | {row.get('assessment_status', '')} | {link} |")
    (directory / 'index.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def run_batch(args):
    numbers = read_numbers(args.patients_file)
    if args.only and not set(args.only) <= set(numbers):
        raise ValueError('--only contains a patient outside the cohort')
    config = settings(args.config)
    directory = args.output_dir.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    with batch_lock(directory):
        plan = plan_inputs(numbers, args.source_dir.resolve(), config)
        contract = binding(config, args.config, args.patients_file, plan)
        write_json(directory / 'cohort_plan.json', {'created_at': now(), 'patients': plan,
                   'config': config.model_dump(), 'knowledge_files': len(contract['corpus_files']),
                   'model_called': False})
        if args.check:
            counts = dict(Counter(row['status'] for row in plan))
            print(json.dumps(counts, ensure_ascii=False), flush=True)
            print('离线检查：' + str(directory / 'cohort_plan.json'), flush=True)
            return int(any(row['status'] != 'ready' for row in plan))
        binding_path = directory / 'binding.json'
        # Compare offline fields before any service request or patient execution.
        previous = read(binding_path) if binding_path.exists() else None
        if previous and previous['contract'] != contract:
            raise ValueError('batch_inputs_config_code_or_corpus_changed: use_a_new_output_dir')
        if not args.prepare_only:
            models = LocalModel(config, directory).preflight()
            if previous and previous.get('models') not in (None, models):
                raise ValueError('batch_model_identity_changed: use_a_new_output_dir')
        else:
            models = previous.get('models') if previous else None
        signature = digest(contract)
        write_json(binding_path, {'binding_hash': signature, 'contract': contract, 'models': models})
        summary_path = directory / 'summary.json'
        summary = read(summary_path) if summary_path.exists() else {
            'version': contract['version'], 'binding_hash': signature, 'started_at': now(),
            'patients': [{'patient_number': n, 'status': 'pending'} for n in numbers]}
        if summary['binding_hash'] != signature:
            raise ValueError('batch_summary_binding_mismatch')
        summary.update(status='running', mode='prepare' if args.prepare_only else 'run')
        summary.pop('finished_at', None)
        checkpoint(directory, summary)
        selected = set(args.only or numbers)
        for index, (source, state) in enumerate(zip(plan, summary['patients']), 1):
            if source['patient_number'] not in selected:
                continue
            number = source['patient_number']
            case = directory / 'cases' / number
            case.mkdir(parents=True, exist_ok=True)
            if source['status'] != 'ready':
                state.update(status='input_failed', error=source['error'])
                checkpoint(directory, summary)
                continue
            try:
                resumed = resume_case(source, case, signature)
            except Exception as exc:
                state.clear()
                state.update(patient_number=number, status='integrity_failed', error=str(exc))
                checkpoint(directory, summary)
                continue
            if resumed:
                write_json(case / 'latest.json', resumed)
                state.clear()
                state.update(resumed)
                print(f'[{index}/{len(numbers)}] 已完成，校验后跳过 {number}', flush=True)
                checkpoint(directory, summary)
                continue
            attempts = [int(p.name.split('-')[1]) for p in case.glob('attempt-*')
                        if p.is_dir() and p.name.split('-')[1].isdigit()]
            attempt = case / f'attempt-{max(attempts, default=0) + 1:04d}'
            attempt.mkdir()
            state.clear()
            state.update(patient_number=number, status='running', mode=summary['mode'],
                         attempt=str(attempt), binding_hash=signature, started_at=now())
            write_json(case / 'latest.json', state)
            checkpoint(directory, summary)
            print(f'[{index}/{len(numbers)}] {summary["mode"]} {number} → {attempt.name}', flush=True)
            try:
                patient = snapshot_inputs(source, attempt / 'input')
                command = [sys.executable, '-u', str(ENTRY), '--patient', str(patient),
                           '--config', str(args.config.resolve()), '--task', 'clinical_review',
                           '--output', str(attempt / 'run')]
                if args.prepare_only:
                    command.append('--prepare-only')
                with (attempt / 'console.log').open('w', encoding='utf-8') as log:
                    subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                   timeout=args.patient_timeout, check=True)
                if args.prepare_only:
                    if read(attempt / 'run/status.json').get('status') != 'prepared':
                        raise ValueError('worker_did_not_prepare')
                    state.update(status='prepared', artifact_sha256=artifacts(attempt / 'run', 'prepare'))
                else:
                    receipt = completed_receipt(source, attempt, signature)
                    state.clear()
                    state.update(receipt)
            except KeyboardInterrupt:
                state.update(status='interrupted', error='Interrupted by user; rerun the same command to resume.')
                write_json(case / 'latest.json', state)
                summary['status'] = 'interrupted'
                checkpoint(directory, summary)
                return 130
            except Exception as exc:
                state.update(status='timed_out' if isinstance(exc, subprocess.TimeoutExpired) else 'failed',
                             error=f'{type(exc).__name__}: {exc}', finished_at=now())
                failure = attempt / 'run/prediction.json'
                if failure.exists():
                    try:
                        state['worker_error'] = read(failure).get('error')
                    except (ValueError, OSError):
                        state['worker_error'] = 'Unreadable worker failure receipt; see console.log.'
                print(f'  {number}: {state["error"]}；详见 console.log', flush=True)
            write_json(case / 'latest.json', state)
            checkpoint(directory, summary)
        success = {'prepared', 'completed'} if args.prepare_only else {'completed'}
        failed = any(r['status'] not in success for r in summary['patients'] if r['patient_number'] in selected)
        summary.update(status='finished_with_errors' if failed else (
            'completed' if all(r['status'] == 'completed' for r in summary['patients']) else 'incomplete'),
                       finished_at=now())
        checkpoint(directory, summary)
        print(json.dumps(summary['counts'], ensure_ascii=False), flush=True)
        print('汇总：' + str(summary_path), flush=True)
        return int(failed)


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument('--patients-file', type=Path, default=ROOT / 'configs/rwe_batch_patients.txt')
    result.add_argument('--source-dir', type=Path, default=ROOT / 'output/rwe_batch_repaired_v2/patients')
    result.add_argument('--config', type=Path, default=ROOT / 'configs/medgraphrag.rwe_batch.yaml')
    result.add_argument('--output-dir', type=Path, default=ROOT / 'output/baselines/medgraphrag/rwe27')
    result.add_argument('--only', nargs='+', help='Run only these cohort members; retain all 27 in summaries')
    result.add_argument('--patient-timeout', type=float, default=7500)
    result.add_argument('--check', action='store_true', help='Offline cohort/context/knowledge file checks only')
    result.add_argument('--prepare-only', action='store_true', help='Exercise each input converter without model calls')
    return result


def main(argv=None):
    arguments = parser()
    args = arguments.parse_args(argv)
    if args.patient_timeout <= 0:
        arguments.error('--patient-timeout must be positive')
    if args.check and args.prepare_only:
        arguments.error('--check and --prepare-only are mutually exclusive')
    try:
        return run_batch(args)
    except (BatchAlreadyRunning, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    except Exception:
        traceback.print_exc()
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
