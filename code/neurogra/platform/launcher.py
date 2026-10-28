"""Start the local UI and a detached worker with one command."""
import os
import subprocess
import sys
import time

from .application import Application
from .locking import exclusive
from .server import make_server


def start(config, port=8767):
    app = Application(config.project_root, config)
    server = make_server(app, port)  # Bind first; do not spawn workers on a port conflict.
    try:
        with exclusive(app.root, 'backup'), exclusive(app.root, 'worker-launch'):
            if not any(w['online'] and not w['stop_requested'] for w in app.store.workers()):
                directory = app.root / 'data/platform/logs'
                directory.mkdir(parents=True, exist_ok=True)
                with (directory / 'worker.log').open('ab') as log:
                    child = subprocess.Popen([sys.executable, '-m', 'neurogra.platform',
                        '--root', str(app.root), 'worker'], cwd=app.root, stdin=subprocess.DEVNULL,
                        stdout=log, stderr=subprocess.STDOUT,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                        start_new_session=os.name != 'nt')
                deadline = time.monotonic() + 10
                while not any(w['online'] for w in app.store.workers()):
                    if child.poll() is not None:
                        raise RuntimeError('后台进程启动失败，请检查本地 worker.log。')
                    if time.monotonic() > deadline:
                        raise RuntimeError('后台进程尚未报告就绪，请检查本地日志。')
                    time.sleep(.1)
        print(f'NeuroGRA 工作台：http://127.0.0.1:{server.server_port}', flush=True)
        print('后台执行进程已就绪。关闭页面服务不会中断正在执行的分析。', flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
