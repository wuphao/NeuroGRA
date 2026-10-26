"""Installed, workspace-independent NeuroGRA application entry point."""
import argparse
import json
from pathlib import Path

from .configuration import PROFILES, public_config, resolve_config


def main(argv=None):
    parser = argparse.ArgumentParser(description='NeuroGRA 本地分析平台')
    parser.add_argument('--root', type=Path, default=None)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--profile', choices=PROFILES, default='standard')
    commands = parser.add_subparsers(dest='command', required=True)
    doctor = commands.add_parser('doctor', help='检查环境，不启动模型推理')
    doctor.add_argument('--offline', action='store_true')
    commands.add_parser('config', help='查看脱敏后的生效配置')
    commands.add_parser('integrity', help='只读检查历史报告产物')
    commands.add_parser('jobs', help='查看平台任务')
    worker = commands.add_parser('worker', help='启动独立后台任务执行进程')
    worker.add_argument('--once', action='store_true')
    server = commands.add_parser('serve', help='启动本机工作台（另运行 worker 执行任务）')
    server.add_argument('--port', type=int, default=8767)
    analyze = commands.add_parser('analyze', help='分析本地 JSON 病例')
    analyze.add_argument('--patient', type=Path, required=True)
    analyze.add_argument('--check', action='store_true')
    analyze.add_argument('--wait', action='store_true', help='提交后在当前进程执行队列')
    args = parser.parse_args(argv)
    config = resolve_config(args.config, root=args.root, profile=args.profile)
    if args.command == 'serve':
        from .server import serve
        serve(config.project_root, config, args.port)
        return 0
    if args.command == 'worker':
        from .worker import Worker
        executor = Worker(config.project_root)
        executor.run_once() if args.once else executor.serve()
        return 0
    if args.command == 'jobs':
        from .application import Application
        app = Application(config.project_root, config)
        result = [app.job(j['job_id']) for j in app.store.jobs()]
    elif args.command == 'config':
        result = public_config(config)
    elif args.command == 'integrity':
        from .integrity import inspect_history
        result = inspect_history(config)
    elif args.command == 'doctor':
        from .preflight import inspect_environment
        result = inspect_environment(config, services=not args.offline)
    else:
        from neurogra.clinical.schemas import PatientInput
        path = config.resolve(args.patient)
        raw = json.loads(path.read_text(encoding='utf-8-sig'))
        PatientInput.model_validate(raw)
        if args.check:
            from neurogra.clinical.intake import ingest_patient
            result = {'status': 'valid', 'records': len(ingest_patient(raw, config).records),
                      'inference_executed': False}
        else:
            from .application import Application
            app = Application(config.project_root, config)
            case = app.import_case(raw)
            result = app.submit(case['version_id'])
            if args.wait:
                from .worker import Worker
                executor = Worker(config.project_root)
                while app.store.job(result['job_id'])['status'] in {'queued', 'running'}:
                    import time
                    if not executor.run_once():
                        time.sleep(.5)
                result = app.job(result['job_id'])
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.command == 'doctor' and not result['ready']:
        return 2
    return 0
