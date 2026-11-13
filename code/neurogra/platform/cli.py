"""Installed, workspace-independent NeuroGRA application entry point."""
import argparse
import json
from pathlib import Path

from .configuration import PROFILES, public_config, resolve_config


def main(argv=None):
    parser = argparse.ArgumentParser(description='NeuroGRA 本地分析平台')
    parser.add_argument('--root', type=Path, default=None)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--profile', choices=PROFILES)
    commands = parser.add_subparsers(dest='command', required=True)
    doctor = commands.add_parser('doctor', help='检查环境，不启动模型推理')
    doctor.add_argument('--offline', action='store_true')
    commands.add_parser('config', help='查看脱敏后的生效配置')
    commands.add_parser('integrity', help='只读检查历史报告产物')
    commands.add_parser('jobs', help='查看平台任务')
    commands.add_parser('storage', help='只读统计受管理数据空间')
    commands.add_parser('migrate-chat', help='只读导入旧 NeuroGRA 聊天记录到本地 SQLite')
    cleanup = commands.add_parser('cleanup', help='预览过期轮转日志，或执行已保存预览')
    cleanup.add_argument('--retention-days', type=int, default=30)
    cleanup.add_argument('--save-plan', type=Path)
    cleanup.add_argument('--apply-plan', type=Path)
    backup = commands.add_parser('backup', help='创建包含患者资料的本地私有一致备份')
    backup.add_argument('--destination', type=Path, required=True)
    verify_backup = commands.add_parser('verify-backup', help='校验备份清单、文件与输入引用')
    verify_backup.add_argument('--source', type=Path, required=True)
    restore_backup = commands.add_parser('restore', help='从备份恢复至不存在的新目录')
    restore_backup.add_argument('--source', type=Path, required=True)
    restore_backup.add_argument('--destination', type=Path, required=True)
    worker = commands.add_parser('worker', help='启动独立后台任务执行进程')
    worker.add_argument('--once', action='store_true')
    server = commands.add_parser('serve', help='启动本机工作台（另运行 worker 执行任务）')
    server.add_argument('--port', type=int, default=8767)
    start = commands.add_parser('start', help='一次启动本机工作台和独立后台进程')
    start.add_argument('--port', type=int, default=8767)
    commands.add_parser('stop-worker', help='请求后台进程完成当前任务后退出')
    analyze = commands.add_parser('analyze', help='分析本地 JSON 病例')
    analyze.add_argument('--patient', type=Path, required=True)
    analyze.add_argument('--check', action='store_true')
    analyze.add_argument('--wait', action='store_true', help='提交后在当前进程执行队列')
    args = parser.parse_args(argv)
    from .configuration import configuration_for_profile
    from .settings import load_settings
    root = resolve_config(root=args.root).project_root
    if args.command == 'migrate-chat':
        from .chat_migration import migrate_chat
        print(json.dumps(migrate_chat(root), ensure_ascii=False))
        return 0
    config = resolve_config(args.config, root=root) if args.config else load_settings(root)
    if args.profile:
        config = configuration_for_profile(config, args.profile)
    if args.command == 'start':
        from .launcher import start
        start(config, args.port)
        return 0
    if args.command == 'stop-worker':
        from .activity import active
        from .store import PlatformStore
        with active(root):
            PlatformStore(root).stop_workers()
        print('后台进程将在当前任务结束后退出。')
        return 0
    if args.command == 'serve':
        from .server import serve
        serve(config.project_root, config, args.port)
        return 0
    if args.command == 'worker':
        from .worker import Worker
        executor = Worker(config.project_root)
        executor.run_once() if args.once else executor.serve()
        return 0
    if args.command in {'backup', 'verify-backup', 'restore'}:
        from .backup import Backups, restore, verify
        if args.command == 'restore':
            result = restore(args.source, args.destination)
        elif args.command == 'verify-backup':
            manifest = verify(args.source)
            result = {'files': len(manifest['files']), 'integrity': 'passed',
                'known_unavailable_artifacts': manifest['known_unavailable_artifacts']}
        else:
            from .application import Application
            result = Backups(Application(root, config)).create(args.destination)
    elif args.command in {'storage', 'cleanup'}:
        from neurogra.clinical.utils import write_json

        from .application import Application
        from .lifecycle import Lifecycle
        lifecycle = Lifecycle(Application(root, config))
        if args.command == 'storage':
            result = lifecycle.inventory()
        elif args.apply_plan:
            if args.save_plan:
                parser.error('--save-plan 和 --apply-plan 不能同时使用')
            result = lifecycle.apply(json.loads(args.apply_plan.read_text(encoding='utf-8')))
        else:
            result = lifecycle.preview(args.retention_days)
            if args.save_plan:
                write_json(args.save_plan, result)
    elif args.command == 'jobs':
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
