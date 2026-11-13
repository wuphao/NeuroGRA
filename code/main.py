"""Compatibility launcher. Use --patient and --config instead of editing source."""
import argparse
import json
from pathlib import Path
from datetime import datetime, timezone

from neurogra.platform.configuration import resolve_config, PROFILES
from neurogra_common import new_id
from neurogra_common.llm_log import configure_llm_log, record_llm_event

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PATIENT_FILE = None
GENERATION_MODEL = 'qwen3.8:27b'


def build_config(path=None, *, profile='standard', root=None, overrides=None):
    return resolve_config(path, profile=profile, root=root or PROJECT_ROOT, overrides=overrides)


def read_patient_json(path=None, root=None):
    from neurogra.clinical.schemas import PatientInput
    path = path or PATIENT_FILE
    if path is None:
        raise ValueError('请通过 --patient 指定患者 JSON 文件。')
    path = Path(path)
    path = path if path.is_absolute() else Path(root or PROJECT_ROOT) / path
    raw = json.loads(path.read_text(encoding='utf-8-sig'))
    PatientInput.model_validate(raw)
    json.dumps(raw, ensure_ascii=False, allow_nan=False)
    return raw


def print_report(result, log_path):
    print('\n======================== 最终分析报告 ========================')
    print(Path(result.report_paths['markdown']).read_text(encoding='utf-8'))
    print('运行 ID：' + result.run_id)
    print('报告文件：' + result.report_paths['markdown'])
    print('LLM 调用日志：' + str(log_path))


def main(argv=None):
    parser = argparse.ArgumentParser(description='NeuroGRA 病例分析')
    parser.add_argument('--patient', required=True, type=Path)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--profile', choices=PROFILES, default='standard')
    parser.add_argument('--root', type=Path, default=PROJECT_ROOT)
    parser.add_argument('--check', action='store_true', help='离线校验，不启动推理')
    parser.add_argument('--log-dir', type=Path, default=Path('output/llm_logs'))
    args = parser.parse_args(argv)
    config = build_config(args.config, profile=args.profile, root=args.root)
    raw = read_patient_json(args.patient, config.project_root)
    if args.check:
        from neurogra.clinical.intake import ingest_patient
        intake = ingest_patient(raw, config)
        print(json.dumps({'status': 'valid', 'message': '校验通过', 'records': len(intake.records),
                          'roles': config.enabled_roles(), 'inference_executed': False}, ensure_ascii=False))
        return
    from neurogra_common.activity import active
    from neurogra_common.locking import exclusive
    with active(config.project_root, timeout=30), exclusive(config.project_root, 'model_resource'):
        return run_analysis(args, config, raw)


def run_analysis(args, config, raw):
    from neurogra.clinical.workflow import analyze_patient
    log_path = config.resolve(args.log_dir) / (
        datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S') + '-' + new_id('session') + '.jsonl')
    configure_llm_log(log_path)
    record_llm_event('session_start', model=config.model.model)
    try:
        result = analyze_patient(raw, config)
    except Exception as exc:
        record_llm_event('session_failed', error_type=type(exc).__name__)
        raise
    record_llm_event('session_complete', run_id=result.run_id, status=result.status,
                     stop_reason=result.stop_reason, report_paths=result.report_paths)
    print_report(result, log_path)
    return result


if __name__ == '__main__':
    main()
