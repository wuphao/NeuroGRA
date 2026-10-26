"""Loopback HTTP transport; all business operations use Application services."""
import json
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from urllib.parse import urlsplit

from pydantic import ValidationError

from .application import Application
from .configuration import PROFILES
from .preflight import inspect_environment


def make_server(app, port=8767):
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, status, data, mime='application/json; charset=utf-8', download=None):
            if not isinstance(data, bytes):
                data = json.dumps(data, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; object-src 'none'")
            if download:
                self.send_header('Content-Disposition', 'attachment; filename="' + download + '"')
            self.end_headers()
            self.wfile.write(data)

        def same_origin(self):
            hosts = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
            return (self.headers.get('Host') in hosts
                    and self.headers.get('Sec-Fetch-Site') != 'cross-site'
                    and self.headers.get('Origin', 'http://' + self.headers.get('Host', '')) in {'http://' + h for h in hosts})

        def dispatch(self, method):
            if not self.same_origin():
                return self.reply(403, {'error': '仅允许本机同源访问。'})
            if method == 'POST' and not secrets.compare_digest(self.headers.get('X-Workbench-Token', ''), token):
                return self.reply(403, {'error': '会话已失效，请刷新页面。'})
            path = urlsplit(self.path).path
            parts = path.strip('/').split('/')
            try:
                body = {}
                if method == 'POST':
                    size = int(self.headers.get('Content-Length', '0'))
                    if not 0 < size <= 50_000_000:
                        raise ValueError('请求大小无效，JSON 文件最大 50 MB。')
                    if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                        raise ValueError('请求必须使用 JSON 格式。')
                    body = json.loads(self.rfile.read(size))
                    if not isinstance(body, dict):
                        raise ValueError('请求顶层必须为对象。')
                if method == 'GET':
                    assets = {'/': ('index.html', 'text/html; charset=utf-8'),
                              '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                              '/app.css': ('app.css', 'text/css; charset=utf-8')}
                    if path in assets:
                        name, mime = assets[path]
                        return self.reply(200, files('neurogra.platform').joinpath('static', name).read_bytes(), mime)
                    if path == '/api/session':
                        return self.reply(200, {'token': token})
                    if path == '/api/health':
                        return self.reply(200, inspect_environment(app.config))
                    if path == '/api/profiles':
                        return self.reply(200, {'profiles': PROFILES})
                    if path == '/api/cases':
                        return self.reply(200, {'cases': app.store.cases()})
                    if len(parts) == 4 and parts[:2] == ['api', 'cases'] and parts[3] == 'versions':
                        return self.reply(200, {'versions': [{k: v for k, v in r.items() if k != 'path'} for r in app.store.versions(parts[2])]})
                    if len(parts) == 3 and parts[:2] == ['api', 'versions']:
                        return self.reply(200, app.case_version(parts[2]))
                    if path == '/api/jobs':
                        return self.reply(200, {'jobs': [app.job(j['job_id']) for j in app.store.jobs()]})
                    if len(parts) == 3 and parts[:2] == ['api', 'jobs']:
                        return self.reply(200, app.job(parts[2]))
                    if len(parts) == 3 and parts[:2] == ['api', 'artifacts']:
                        row, resource = app.artifact(parts[2])
                        if urlsplit(self.path).query == 'download':
                            return self.reply(200, resource.read_bytes(), 'application/octet-stream', resource.name)
                        return self.reply(200, {'kind': row['kind'], 'text': resource.read_text(encoding='utf-8')})
                    if path == '/api/history':
                        from .integrity import inspect_history
                        return self.reply(200, inspect_history(app.config))
                elif method == 'POST':
                    if path == '/api/cases':
                        return self.reply(201, app.import_case(body.get('patient'), source='json_upload'))
                    if path == '/api/jobs':
                        # Browser parameters are a bounded set, never arbitrary filesystem roots.
                        settings = body.get('settings') or {}
                        allowed = {'main_agent', 'agents', 'budget', 'max_rounds', 'verify_narrative'}
                        if not isinstance(settings, dict) or not set(settings) <= allowed:
                            raise ValueError('分析设置包含不支持的字段。')
                        profile = body.get('profile', 'standard')
                        config = app.configuration(profile, settings)
                        return self.reply(202, app.submit(body.get('version_id'), config))
                    if len(parts) == 4 and parts[:2] == ['api', 'jobs']:
                        if parts[3] == 'cancel':
                            app.store.cancel(parts[2])
                            return self.reply(200, app.job(parts[2]))
                        if parts[3] in {'retry', 'resume'}:
                            return self.reply(202, app.retry_job(parts[2], resume=parts[3] == 'resume'))
                return self.reply(404, {'error': '页面或记录不存在。'})
            except KeyError:
                self.reply(404, {'error': '记录不存在。'})
            except ValidationError as exc:
                fields = ['.'.join(map(str, error['loc'])) for error in exc.errors()[:5]]
                self.reply(400, {'error': '资料或配置格式无效，请检查：' + '、'.join(fields)})
            except (ValueError, TypeError) as exc:
                self.reply(400, {'error': str(exc) if isinstance(exc, ValueError) else '请求字段类型无效。'})
            except Exception:
                self.reply(500, {'error': '操作未完成，请查看本地运行状态或重试。'})

        def do_GET(self):
            self.dispatch('GET')

        def do_POST(self):
            self.dispatch('POST')

    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


def serve(root, config=None, port=8767):
    app = Application(root, config)
    server = make_server(app, port)
    print(f'NeuroGRA 工作台：http://127.0.0.1:{server.server_port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
