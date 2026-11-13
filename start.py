"""Source-checkout launcher used by start.cmd; frontend, API and worker together."""
import argparse
import os
from pathlib import Path
import sys
import threading
import webbrowser


PROJECT_ROOT = Path(__file__).resolve().parent


def source_environment():
    source = str(PROJECT_ROOT / 'code')
    if source not in sys.path:
        sys.path.insert(0, source)
    # Detached workers must import the same checkout, including uninstalled edits.
    previous = os.environ.get('PYTHONPATH', '')
    paths = previous.split(os.pathsep) if previous else []
    os.environ['PYTHONPATH'] = os.pathsep.join([source, *[p for p in paths if p != source]])


def main(argv=None):
    parser = argparse.ArgumentParser(description='启动 NeuroGRA 前端、API 和后台任务进程')
    parser.add_argument('--port', type=int, default=8767)
    parser.add_argument('--root', type=Path, default=PROJECT_ROOT, help='数据工作区，默认使用脚本所在项目')
    parser.add_argument('--profile', help='可选配置方案；不指定时沿用保存的配置')
    parser.add_argument('--no-browser', action='store_true', help='不自动打开浏览器')
    parser.add_argument('--check', action='store_true', help='只核验启动条件，不启动服务或后台进程')
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error('端口必须在 0～65535 之间，0 表示自动分配')
    source_environment()
    try:
        from neurogra.platform.configuration import PROFILES, configuration_for_profile
        from neurogra.platform.settings import load_settings
        from neurogra.platform.launcher import start

        if args.profile and args.profile not in PROFILES:
            parser.error('未知配置方案，可选：' + ', '.join(PROFILES))
        root = args.root.resolve()
        config = load_settings(root)
        if args.profile:
            config = configuration_for_profile(config, args.profile)
        static = PROJECT_ROOT / 'code/neurogra/platform/static'
        if not all((static / name).is_file() for name in ('login.html', 'chat.html', 'chat.js', 'chat.css')):
            raise RuntimeError('前端资源缺失，请检查当前代码目录。')
        print(f'工作区：{root}', flush=True)
        if args.check:
            print('启动条件检查通过：前端资源、后端模块与配置可加载。未启动服务；外部模型和 RWE 连通性需另行检查。')
            return 0

        def ready(url):
            if not args.no_browser:
                def open_page():
                    try:
                        if not webbrowser.open(url):
                            print(f'请手动打开：{url}', flush=True)
                    except Exception:
                        print(f'请手动打开：{url}', flush=True)
                threading.Thread(target=open_page, daemon=True).start()

        start(config, args.port, on_ready=ready)
        return 0
    except ImportError as error:
        print(f'启动失败：缺少运行依赖（{error.name}）。请先按 README.md 安装依赖。', file=sys.stderr)
    except OSError as error:
        if getattr(error, 'winerror', None) == 10048 or getattr(error, 'errno', None) == 98:
            print('启动失败：端口已被占用。请关闭旧服务，或使用 start.cmd --port 8768。', file=sys.stderr)
        else:
            print(f'启动失败：系统错误 {type(error).__name__}，请检查端口和目录权限。', file=sys.stderr)
    except (RuntimeError, ValueError) as error:
        print(f'启动失败：{error}', file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
