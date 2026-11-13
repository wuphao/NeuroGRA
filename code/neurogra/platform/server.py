"""Loopback HTTP transport; all business operations use Application services."""
import base64
import json
import secrets
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from urllib.parse import parse_qs, urlsplit

from pydantic import ValidationError

from .application import Application
from .configuration import PROFILES
from .preflight import inspect_environment


def make_server(app, port=8767):
    token = secrets.token_urlsafe(32)
    from .patient_workspace import PatientWorkspace, WorkspaceError
    workspace = PatientWorkspace(app.config)
    from .async_chat import AsyncChat
    from .patient_analysis import PatientAnalysis
    from .patient_chat import PatientChat
    analyses = PatientAnalysis(app, workspace)
    chat = PatientChat(app, workspace, analyses)
    async_chat = AsyncChat(chat, workspace)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, status, data, mime='application/json; charset=utf-8', download=None, cookie=None):
            if not isinstance(data, bytes):
                data = json.dumps(data, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Referrer-Policy', 'same-origin')
            if cookie:
                self.send_header('Set-Cookie', cookie)
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; object-src 'none'")
            if download:
                self.send_header('Content-Disposition', 'attachment; filename="' + download + '"')
            self.end_headers()
            self.wfile.write(data)

        def session_id(self):
            try:
                cookies = SimpleCookie(self.headers.get('Cookie', ''))
                return cookies[workspace.COOKIE].value if workspace.COOKIE in cookies else None
            except Exception:
                return None

        def redirect(self, location):
            self.send_response(303)
            self.send_header('Location', location)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', '0')
            self.end_headers()

        def same_origin(self):
            hosts = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
            return (self.headers.get('Host') in hosts
                    and self.headers.get('Sec-Fetch-Site') != 'cross-site'
                    and self.headers.get('Origin', 'http://' + self.headers.get('Host', '')) in {'http://' + h for h in hosts})

        def dispatch(self, method):
            from .activity import active
            from .locking import ResourceBusy
            try:
                with active(app.root):
                    return self.dispatch_active(method)
            except ResourceBusy:
                return self.reply(503, {'error': '系统正在维护或处理任务，请稍后重试。'})

        def dispatch_active(self, method):
            if not self.same_origin():
                return self.reply(403, {'error': '仅允许本机同源访问。'})
            if method == 'POST' and not secrets.compare_digest(self.headers.get('X-Workbench-Token', ''), token):
                return self.reply(403, {'error': '会话已失效，请刷新页面。'})
            path = urlsplit(self.path).path
            parts = path.strip('/').split('/')
            try:
                session_id = self.session_id()
                session = workspace.session(session_id)
                if path in {'/', '/chat', '/patients', '/workbench'} and not session:
                    return self.redirect('/login')
                public_api = {'/api/session', '/api/auth/session', '/api/auth/login'}
                if path.startswith('/api/') and path not in public_api and not session:
                    return self.reply(401, {'error': '请先登录。'})
                allowed_cases = None
                checked_materials = set()

                def case_index():
                    nonlocal allowed_cases
                    if allowed_cases is None:
                        roster = workspace.snapshot(session, refresh=True)
                        patient_keys = {patient['patientKey'] for patient in roster['patients']}
                        with app.store.connect() as db:
                            rows = db.execute('''SELECT case_id,patient_key FROM patient_scope
                                WHERE source_id=? AND project_id=?''',
                                (workspace.source_id, str(workspace.schema.project_id))).fetchall()
                        allowed_cases = {row['case_id']: row['patient_key'] for row in rows
                                         if row['patient_key'] in patient_keys}
                    return allowed_cases

                def require_case(case_id, *, materials=False):
                    patient_key = case_index().get(case_id)
                    if patient_key is None:
                        raise WorkspaceError('病例不存在或无权查看。', 404)
                    if materials and patient_key not in checked_materials:
                        workspace.authorize_forms(session, patient_key)
                        checked_materials.add(patient_key)
                    return patient_key

                def require_version(version_id, *, materials=False):
                    version = app.store.version(version_id)
                    return require_case(version['case_id'], materials=materials)

                def require_job(job_id, *, materials=False):
                    job = app.job(job_id)
                    if not job['version_id']:
                        raise WorkspaceError('任务不存在或无权查看。', 404)
                    patient_key = require_version(job['version_id'], materials=materials)
                    binding = job['payload'].get('patient_workspace')
                    if binding and (binding.get('source_id') != workspace.source_id or
                            str(binding.get('project_id')) != str(workspace.schema.project_id) or
                            binding.get('patient_id') != patient_key):
                        raise WorkspaceError('任务不存在或无权查看。', 404)
                    return job

                def visible_jobs():
                    result = []
                    for row in app.store.jobs():
                        try:
                            result.append(require_job(row['job_id'], materials=True))
                        except WorkspaceError as error:
                            if error.status == 401:
                                raise
                        except KeyError:
                            pass
                    return result

                def require_batch(batch_id):
                    batch = app.batch(batch_id)
                    if not batch['jobs'] and not batch['not_submitted']:
                        raise WorkspaceError('批次不存在或无权查看。', 404)
                    for job in batch['jobs']:
                        require_job(job['job_id'], materials=True)
                    for version_id in batch['not_submitted']:
                        require_version(version_id, materials=True)
                    return batch

                body = {}
                chat_route = len(parts) in {4, 5} and parts[:2] == ['api', 'patients'] and parts[3] == 'chat'
                general_chat = len(parts) in {2, 3} and parts[:2] == ['api', 'chat']
                chat_analysis = (len(parts) == 6 and parts[:2] == ['api', 'patients']
                                 and parts[3] == 'chat' and parts[5] == 'analysis')
                chat_turn = (len(parts) == 7 and parts[:2] == ['api', 'patients']
                             and parts[3] == 'chat' and parts[5] == 'turns')
                chat_collaboration = (len(parts) == 8 and parts[:2] == ['api', 'patients']
                                      and parts[3] == 'chat' and parts[5] == 'turns'
                                      and parts[7] == 'collaboration')
                chat_resume = (len(parts) == 8 and parts[:2] == ['api', 'patients']
                               and parts[3] == 'chat' and parts[5] == 'turns' and parts[7] == 'resume')
                chat_subjects = (len(parts) == 6 and parts[:2] == ['api', 'patients']
                                 and parts[3] == 'chat' and parts[5] == 'subjects')
                legacy_analysis = len(parts) == 4 and parts[:2] == ['api', 'chat'] and parts[3] == 'analysis'
                if method == 'POST':
                    size = int(self.headers.get('Content-Length', '0'))
                    limit = 8192 if chat_route or general_chat or chat_analysis or chat_subjects or chat_resume or legacy_analysis or path.startswith(('/api/auth/', '/api/llm')) else 50_000_000
                    if not 0 < size <= limit:
                        raise ValueError('请求大小超过接口限制或内容为空。')
                    if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                        raise ValueError('请求必须使用 JSON 格式。')
                    body = json.loads(self.rfile.read(size))
                    if not isinstance(body, dict):
                        raise ValueError('请求顶层必须为对象。')
                if method == 'GET':
                    if path in {'/patients', '/workbench'}:
                        return self.redirect('/chat')
                    assets = {'/': ('chat.html', 'text/html; charset=utf-8'),
                              '/chat': ('chat.html', 'text/html; charset=utf-8'),
                              '/chat.js': ('chat.js', 'text/javascript; charset=utf-8'),
                              '/chat.css': ('chat.css', 'text/css; charset=utf-8'),
                              '/report-view.js': ('report-view.js', 'text/javascript; charset=utf-8'),
                              '/login': ('login.html', 'text/html; charset=utf-8'),
                              '/login.js': ('login.js', 'text/javascript; charset=utf-8'),
                              '/login.css': ('login.css', 'text/css; charset=utf-8')}
                    if path in assets:
                        name, mime = assets[path]
                        return self.reply(200, files('neurogra.platform').joinpath('static', name).read_bytes(), mime)
                    if path == '/api/session':
                        return self.reply(200, {'token': token, 'authenticated': bool(session)})
                    if path == '/api/auth/session':
                        return self.reply(200, workspace.public_session(session) if session else {'authenticated': False})
                    if path == '/api/patients':
                        return self.reply(200, workspace.snapshot(session, refresh=urlsplit(self.path).query == 'refresh=1'))
                    if chat_collaboration:
                        query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
                        if not set(query) <= {'after', 'limit'} or any(len(value) != 1 for value in query.values()):
                            raise WorkspaceError('协作进度游标无效。', 400)
                        try:
                            after = int(query.get('after', ['0'])[0])
                            limit = int(query.get('limit', ['100'])[0])
                        except ValueError:
                            raise WorkspaceError('协作进度游标无效。', 400) from None
                        return self.reply(200, async_chat.collaboration_progress(
                            session, parts[2], parts[4], parts[6], after=after, limit=limit))
                    if chat_turn:
                        return self.reply(200, async_chat.get(session, parts[2], parts[4], parts[6]))
                    if chat_route:
                        result = chat.sessions(session, parts[2]) if len(parts) == 4 else chat.messages(session, parts[2], parts[4])
                        return self.reply(200, result)
                    if general_chat:
                        result = chat.sessions(session, None) if len(parts) == 2 else chat.messages(session, None, parts[2])
                        return self.reply(200, result)
                    if len(parts) == 4 and parts[:2] == ['api', 'patients'] and parts[3] == 'forms':
                        return self.reply(200, workspace.patient(session, parts[2], refresh=True))
                    if len(parts) == 5 and parts[:2] == ['api', 'patients'] and parts[3] == 'forms':
                        return self.reply(200, workspace.form(session, parts[2], parts[4]))
                    if len(parts) == 4 and parts[:2] == ['api', 'patients'] and parts[3] == 'analysis':
                        return self.reply(200, analyses.history(session, parts[2]))
                    if len(parts) == 5 and parts[:2] == ['api', 'patients'] and parts[3] == 'analysis':
                        return self.reply(200, analyses.job(session, parts[2], parts[4]))
                    if len(parts) == 7 and parts[:2] == ['api', 'patients'] and parts[3] == 'analysis' and parts[5] == 'artifacts':
                        row, resource = analyses.artifact(session, parts[2], parts[4], parts[6])
                        if urlsplit(self.path).query == 'download':
                            return self.reply(200, resource.read_bytes(), 'application/octet-stream', resource.name)
                        return self.reply(200, {'kind': row['kind'], 'text': resource.read_text(encoding='utf-8')})
                    if path == '/api/health':
                        return self.reply(200, inspect_environment(app.config) | {'workers': app.store.workers()})
                    if path == '/api/settings':
                        from .settings import settings_view
                        return self.reply(200, settings_view(app.config))
                    if path == '/api/llm':
                        from .settings import model_settings_view
                        return self.reply(200, model_settings_view(app.config))
                    if path == '/api/profiles':
                        return self.reply(200, {'profiles': PROFILES})
                    if path == '/api/rwe/status':
                        from .knowledge import rwe_readiness
                        return self.reply(200, rwe_readiness(app.config))
                    if path in {'/api/documents', '/api/knowledge'}:
                        from .knowledge import KnowledgeManager
                        manager = KnowledgeManager(app)
                        return self.reply(200, {'documents': manager.documents()} if path.endswith('documents') else manager.versions())
                    if path == '/api/cases':
                        allowed = case_index()
                        return self.reply(200, {'cases': [case for case in app.store.cases() if case['case_id'] in allowed]})
                    if len(parts) == 4 and parts[:2] == ['api', 'cases'] and parts[3] == 'versions':
                        require_case(parts[2], materials=True)
                        return self.reply(200, {'versions': [{k: v for k, v in r.items() if k != 'path'} for r in app.store.versions(parts[2])]})
                    if len(parts) == 3 and parts[:2] == ['api', 'versions']:
                        require_version(parts[2], materials=True)
                        return self.reply(200, app.case_version(parts[2]))
                    if path == '/api/jobs':
                        return self.reply(200, {'jobs': visible_jobs()})
                    if path == '/api/batches':
                        batches = []
                        for batch in app.batches():
                            try:
                                batches.append(require_batch(batch['batch_id']))
                            except WorkspaceError as error:
                                if error.status == 401:
                                    raise
                        return self.reply(200, {'batches': batches})
                    if len(parts) == 3 and parts[:2] == ['api', 'batches']:
                        return self.reply(200, require_batch(parts[2]))
                    if len(parts) == 3 and parts[:2] == ['api', 'jobs']:
                        return self.reply(200, require_job(parts[2], materials=True))
                    if len(parts) == 3 and parts[:2] == ['api', 'artifacts']:
                        with app.store.connect() as db:
                            binding = db.execute('''SELECT a.job_id FROM artifacts f JOIN attempts a
                                ON f.attempt_id=a.attempt_id WHERE f.artifact_id=?''', (parts[2],)).fetchone()
                        if binding is None:
                            raise WorkspaceError('报告不存在或无权查看。', 404)
                        job = require_job(binding['job_id'], materials=True)
                        patient_key = require_version(job['version_id'])
                        row, resource = analyses.artifact(session, patient_key, job['job_id'], parts[2])
                        if urlsplit(self.path).query == 'download':
                            return self.reply(200, resource.read_bytes(), 'application/octet-stream', resource.name)
                        return self.reply(200, {'kind': row['kind'], 'text': resource.read_text(encoding='utf-8')})
                    if path == '/api/history':
                        from .integrity import inspect_history
                        allowed_runs = {attempt['run_id'] for job in visible_jobs() for attempt in job['attempts']
                                        if attempt.get('run_id')}
                        history = inspect_history(app.config)
                        history['runs'] = [run for run in history['runs'] if run['run_id'] in allowed_runs]
                        history['checked'] = sum(len(run['artifacts']) for run in history['runs'])
                        history['missing'] = sum(item['status'] != 'available' for run in history['runs'] for item in run['artifacts'])
                        return self.reply(200, history)
                elif method == 'POST':
                    if chat_resume:
                        if body:
                            raise WorkspaceError('恢复请求不接受覆盖原问题或患者范围。', 400)
                        return self.reply(202, async_chat.resume(session_id, session, parts[2], parts[4], parts[6]))
                    if path in {'/api/llm/models', '/api/llm/test'}:
                        from .settings import model_operation
                        return self.reply(200, model_operation(app.config, body, parts[-1]))
                    if path == '/api/llm':
                        from .settings import model_settings_view, save_settings
                        app.config = save_settings(app.root, app.config, {'model': body})
                        return self.reply(200, model_settings_view(app.config))
                    if legacy_analysis:
                        raise WorkspaceError('旧会话仅供查看。请选择患者后开始分析。', 400)
                    if chat_analysis:
                        return self.reply(202, chat.start_analysis(session, parts[2], parts[4], body))
                    if chat_subjects:
                        return self.reply(200, chat.set_subjects(session, parts[2], parts[4], body))
                    if chat_route or general_chat:
                        patient_id = parts[2] if chat_route else None
                        if len(parts) == (4 if chat_route else 2):
                            return self.reply(201, chat.create(session, patient_id))

                        def check_chat_session():
                            if workspace.session(session_id) is not session:
                                raise WorkspaceError('登录已失效，请重新登录。', 401)

                        if (chat_route and body.get('action_response') is None and
                                isinstance(body.get('capabilities'), list) and 'async_turns_v1' in body['capabilities']):
                            return self.reply(202, async_chat.start(session_id, session, patient_id, parts[-1], body))
                        return self.reply(200, chat.send(session, patient_id, parts[-1], body, check_chat_session))
                    if len(parts) == 4 and parts[:2] == ['api', 'patients'] and parts[3] == 'analysis':
                        if body:
                            raise ValueError('智能分析不接受患者资料或配置覆盖。')
                        return self.reply(202, analyses.submit(session, parts[2]))
                    if path == '/api/auth/login':
                        identifier = workspace.login(body.get('account'), body.get('password'))
                        cookie = f'{workspace.COOKIE}={identifier}; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800'
                        return self.reply(200, {'authenticated': True}, cookie=cookie)
                    if path == '/api/auth/logout':
                        workspace.logout(session_id)
                        return self.reply(200, {'authenticated': False}, cookie=f'{workspace.COOKIE}=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0')
                    if path == '/api/rwe/import':
                        raise WorkspaceError('请选择已授权的患者后发起分析。', 403)
                    if path == '/api/documents':
                        from .knowledge import KnowledgeManager
                        content = base64.b64decode(body.get('content', ''), validate=True)
                        return self.reply(201, KnowledgeManager(app).upload(body.get('name', ''), content))
                    if path in {'/api/knowledge/build', '/api/documents/preview'}:
                        from .knowledge import KnowledgeManager
                        flags = {key: body.get(key, default) for key, default in [('kb', True), ('kg', False), ('vector', True)]}
                        if not all(isinstance(v, bool) for v in flags.values()):
                            raise ValueError('知识开关必须为布尔值。')
                        return self.reply(202, KnowledgeManager(app).submit(body.get('document_ids'),
                            preview=path.endswith('preview'), **flags))
                    if path == '/api/knowledge/activate':
                        from .knowledge import KnowledgeManager
                        return self.reply(200, KnowledgeManager(app).activate(body.get('module'), body.get('release_id')))
                    if path == '/api/settings':
                        from .settings import save_settings, settings_view
                        app.config = save_settings(app.root, app.config, body)
                        return self.reply(200, settings_view(app.config))
                    if path == '/api/cases':
                        raise WorkspaceError('患者会话中不支持导入未绑定来源的病例。', 403)
                    if path == '/api/jobs':
                        # Browser parameters are a bounded set, never arbitrary filesystem roots.
                        settings = body.get('settings') or {}
                        allowed = {'main_agent', 'agents', 'budget', 'max_rounds', 'verify_narrative'}
                        if not isinstance(settings, dict) or not set(settings) <= allowed:
                            raise ValueError('分析设置包含不支持的字段。')
                        profile = body.get('profile', 'standard')
                        config = app.configuration(profile, settings)
                        patient_key = require_version(body.get('version_id'), materials=True)
                        patient = workspace.patient(session, patient_key, refresh=True)
                        return self.reply(202, app.submit(body.get('version_id'), config,
                            context={'source_id': workspace.source_id, 'project_id': workspace.schema.project_id,
                                     'patient_id': patient_key, 'patient_number': patient['id'], 'profile': profile}))
                    if path == '/api/batches':
                        profile = body.get('profile', 'kb-only')
                        for version_id in body.get('version_ids') or []:
                            require_version(version_id, materials=True)
                        return self.reply(202, app.submit_batch(body.get('version_ids'),
                            app.configuration(profile), body.get('name', '病例批量分析')))
                    if len(parts) == 4 and parts[:2] == ['api', 'jobs']:
                        require_job(parts[2], materials=True)
                        if parts[3] == 'cancel':
                            app.store.cancel(parts[2])
                            return self.reply(200, app.job(parts[2]))
                        if parts[3] in {'retry', 'resume'}:
                            return self.reply(202, app.retry_job(parts[2], resume=parts[3] == 'resume'))
                return self.reply(404, {'error': '页面或记录不存在。'})
            except WorkspaceError as exc:
                if exc.status == 401:
                    workspace.logout(session_id)
                self.reply(exc.status, {'error': str(exc)})
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
