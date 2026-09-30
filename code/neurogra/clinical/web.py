"""Loopback-only workbench: persistent jobs and the existing grounded RWE workflow."""
import argparse
import json
import re
import secrets
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .config import load_config
from .rwe import analyze_rwe, load_rwe_config, RweFailure
from .storage import RunStore


class Workbench:
    def __init__(self, config, rwe_config, runner=analyze_rwe):
        self.config, self.rwe_config, self.runner = config, rwe_config, runner
        self.store = RunStore(config.resolve(config.database))
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.Lock()
        with self.store.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS web_jobs (
                job_id TEXT PRIMARY KEY, patient_number TEXT NOT NULL,
                run_id TEXT, status TEXT NOT NULL, created REAL NOT NULL,
                error TEXT)''')
            db.execute("UPDATE web_jobs SET status='interrupted', error='服务重启，任务已中断；可重新发起分析。' WHERE status='running'")

    def jobs(self):
        with self.store.connect() as db:
            jobs = [dict(r) for r in db.execute('SELECT * FROM web_jobs ORDER BY created DESC LIMIT 100')]
            known = {j['run_id'] for j in jobs}
            for row in db.execute('''SELECT run_id,
                    json_extract(payload,'$.report.patient_id') patient_number,
                    json_extract(payload,'$.status') status
                    FROM objects WHERE kind='run_result' ORDER BY rowid DESC LIMIT 100'''):
                if row['run_id'] not in known:
                    jobs.append(dict(row) | {'job_id': None, 'created': None, 'error': None})
                    known.add(row['run_id'])
            return jobs

    def submit(self, patient):
        if not isinstance(patient, str) or not patient.strip() or len(patient) > 128 or any(ord(c) < 32 for c in patient):
            raise ValueError('请输入有效的 RWE 患者编号（最长 128 字符）。')
        patient = patient.strip()
        with self.lock, self.store.connect() as db:
            active = db.execute("SELECT * FROM web_jobs WHERE status='running'").fetchone()
            if active:
                if active['patient_number'] == patient:
                    return dict(active)
                raise ValueError('已有分析任务运行中，请等待完成后再提交。')
            job_id = uuid.uuid4().hex
            db.execute('INSERT INTO web_jobs VALUES(?,?,NULL,?,?,NULL)',
                       (job_id, patient, 'running', time.time()))
            threading.Thread(target=self._run, args=(job_id, patient), daemon=True).start()
        return self.job(job_id)

    def _run(self, job_id, patient):
        def bind(run_id):
            with self.store.connect() as db:
                db.execute('UPDATE web_jobs SET run_id=? WHERE job_id=?', (run_id, job_id))
        try:
            result = self.runner(patient, self.config, self.rwe_config, on_run_created=bind)
            status, error = result.status, None
        except RweFailure as exc:
            status, error = 'failed', str(exc).split(':export_id=')[0]
        except Exception as exc:
            status, error = 'failed', '分析失败：' + type(exc).__name__
        with self.store.connect() as db:
            db.execute('UPDATE web_jobs SET status=?,error=? WHERE job_id=?', (status, error, job_id))

    def job(self, job_id):
        with self.store.connect() as db:
            row = db.execute('SELECT * FROM web_jobs WHERE job_id=?', (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        job = dict(row)
        if job['run_id']:
            summary = self.store.summary(job['run_id'])
            job['phase'] = summary['status']
            job['calls'] = summary['calls']
            job['events'] = summary['events'][-80:]
        else:
            job.update(phase='rwe_export', calls=[], events=[])
        return job

    def artifact(self, run_id):
        rows = self.store.objects(run_id, 'run_result')
        if not rows:
            raise KeyError(run_id)
        result = max(rows, key=lambda r: r['version'])['payload']
        path = Path(result['report_paths']['provenance']).resolve()
        # Only return an artifact written beneath this configured project.
        if not path.is_relative_to(self.config.project_root.resolve()):
            raise ValueError('报告文件不在项目目录中。')
        provenance = json.loads(path.read_text(encoding='utf-8'))
        report = result['report']
        if provenance['run_id'] != run_id or report['run_id'] != run_id or provenance['patient_id'] != report['patient_id']:
            raise ValueError('报告与依据不匹配。')
        return {'report': report, 'provenance': provenance, 'execution': self.store.summary(run_id)}


def make_server(app, port=8767):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # Avoid patient identifiers in access logs.

        def reply(self, code, value, content_type='application/json; charset=utf-8'):
            data = json.dumps(value, ensure_ascii=False).encode() if content_type.startswith('application/json') else value
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(data)

        def allowed(self):
            hosts = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
            return (self.headers.get('Host') in hosts
                    and self.headers.get('Sec-Fetch-Site') not in {'cross-site'}
                    and self.headers.get('Origin', 'http://' + self.headers.get('Host', '')) in {'http://' + h for h in hosts})

        def do_GET(self):
            if not self.allowed():
                return self.reply(403, {'error': '仅允许本机同源访问。'})
            path = urlsplit(self.path).path
            try:
                if path == '/api/session':
                    return self.reply(200, {'token': app.token})
                if path == '/api/jobs':
                    return self.reply(200, {'jobs': app.jobs()})
                if re.fullmatch(r'/api/jobs/[a-f0-9]{32}', path):
                    return self.reply(200, app.job(path.rsplit('/', 1)[1]))
                if re.fullmatch(r'/api/reports/[a-f0-9]{32}', path):
                    return self.reply(200, app.artifact(path.rsplit('/', 1)[1]))
                assets = {'/': ('rwe-workbench.html', 'text/html; charset=utf-8'),
                          '/workbench-api.js': ('workbench-api.js', 'text/javascript; charset=utf-8')}
                if path in assets:
                    name, mime = assets[path]
                    data = (app.config.project_root / 'frontend' / name).read_bytes()
                    if path == '/':
                        # Do not initialize the offline demo case in the connected UI.
                        data = data.replace(b"mode='demo'", b"mode='empty'", 1)
                        data = data.replace(b'value="DEMO-RWE"', b'value=""')
                        data = data.replace(b'</body>', b'<script src="/workbench-api.js"></script></body>')
                    return self.reply(200, data, mime)
                self.reply(404, {'error': '未找到页面或记录。'})
            except KeyError:
                self.reply(404, {'error': '报告尚未生成或记录不存在。'})
            except Exception:
                self.reply(500, {'error': '读取结果失败，请检查服务端数据。'})

        def do_POST(self):
            if not self.allowed() or not secrets.compare_digest(self.headers.get('X-Workbench-Token', '').encode(), app.token.encode()):
                return self.reply(403, {'error': '会话无效，请刷新页面。'})
            if self.path != '/api/jobs':
                return self.reply(404, {'error': '接口不存在。'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 4096:
                    raise ValueError('请求大小无效。')
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError('请求必须为 JSON 对象。')
                self.reply(202, app.submit(body.get('patient_number')))
            except (ValueError, UnicodeError) as exc:
                self.reply(400, {'error': str(exc)})

    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


def main():
    parser = argparse.ArgumentParser(description='NeuroGRA local web workbench')
    parser.add_argument('--port', type=int, default=8767)
    parser.add_argument('--config', default='configs/clinical.rwe.yaml')
    parser.add_argument('--rwe-config', default='configs/rwe.project8.yaml')
    args = parser.parse_args()
    config = load_config(args.config)
    store = RunStore(config.resolve(config.database))
    with store.workflow_lock('web_service'):
        app = Workbench(config, load_rwe_config(config.resolve(Path(args.rwe_config))))
        server = make_server(app, args.port)
        print(f'NeuroGRA: http://127.0.0.1:{server.server_port}', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


if __name__ == '__main__':
    main()
