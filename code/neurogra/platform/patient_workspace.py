"""Session-scoped, read-only RWE patient workspace. No patient data is written to disk."""
import hashlib
import json
import re
import secrets
import threading
import time
from collections import defaultdict
from datetime import date

from neurogra.clinical.rwe import SYSTEM_FIELDS, local_settings
from neurogra.clinical.rwe_auth import AuthenticationFailure, obtain_token

from .knowledge import rwe_configuration

PRIVATE_FIELD = re.compile(r'姓名|证件|身份证|手机号|电话|地址|邮箱|患者编号|住院号|门诊号|openid|email|phone|address|patient.?id|patient.?number|id.?card|^name$', re.I)


class WorkspaceError(ValueError):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


class RweReader:
    PATHS = {'/patient/queryPatient', '/form/queryDataBatch', '/form/getFormDetailsBatch'}

    def __init__(self, base_url, token, timeout=20):
        self.base_url, self.token, self.timeout = base_url.rstrip('/'), token, timeout

    def query(self, path, body):
        import requests
        if path not in self.PATHS:
            raise WorkspaceError('不支持该操作。', 400)
        token = self.token if self.token.lower().startswith('bearer ') else 'Bearer ' + self.token
        try:
            with requests.post(self.base_url + path, json=body, headers={'Authorization': token},
                               timeout=self.timeout, allow_redirects=False, stream=True) as response:
                if response.status_code in {401, 403}:
                    raise WorkspaceError('登录已失效或无权读取该资料，请重新登录。', 401)
                if response.status_code != 200:
                    raise WorkspaceError('RWE 服务暂时不可用，请稍后重试。')
                chunks, size = [], 0
                for chunk in response.iter_content(65536):
                    size += len(chunk)
                    if size > 32 * 1024 * 1024:
                        raise WorkspaceError('资料量超过单次读取限制，请联系管理员缩小项目范围。')
                    chunks.append(chunk)
                payload = json.loads(b''.join(chunks))
        except (requests.RequestException, ValueError) as exc:
            if isinstance(exc, WorkspaceError):
                raise
            raise WorkspaceError('无法连接 RWE，请检查服务状态。') from None
        if not isinstance(payload, dict) or payload.get('code') != 0:
            raise WorkspaceError('RWE 拒绝了读取请求，请确认当前账号的项目和表单权限。', 403)
        return payload.get('data')


def read_rows(data):
    if not isinstance(data, dict) or not isinstance(data.get('list'), list):
        raise WorkspaceError('RWE 返回的资料格式无效。')
    rows = data['list']
    pagination = data.get('pagination') or {}
    if not isinstance(pagination, dict):
        raise WorkspaceError('RWE 返回的分页信息无效。')
    total = pagination.get('total', data.get('total'))
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        raise WorkspaceError('RWE 未返回有效记录总数，暂不能可靠展示。')
    if total != len(rows):
        raise WorkspaceError('RWE 未返回完整记录，已停止展示，避免把缺失资料当作无记录。')
    if not all(isinstance(row, dict) for row in rows):
        raise WorkspaceError('RWE 返回的资料格式无效。')
    return rows


def display_value(value):
    if value is None or value == '':
        return '—'
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return '是' if value else '否'
    return str(value)


def basic_patient(row, forms):
    identifier = str(row.get('patientNumber') or row.get('patient_number') or row['id'])
    age = row.get('age')
    if age is None:
        birth = row.get('dateOfBirth') or row.get('date_of_birth')
        if birth:
            try:
                birth = date.fromisoformat(str(birth)[:10])
                today = date.today()
                age = today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))
                if not 0 <= age <= 120:
                    age = None
            except ValueError:
                pass
    return {'id': identifier, 'patientKey': str(row['id']), 'name': row.get('name') or identifier,
            'sex': {1: '男', 2: '女', '1': '男', '2': '女'}.get(row.get('gender'), '未知'),
            'age': age, 'source': 'RWE', 'education': '—',
            'institution': '—', 'created': str(row.get('enrollTime') or row.get('create_time') or row.get('createTime') or '')[:10],
            'available': 0, 'status': 'pending', 'updated': '',
            'forms': {form.key: {'count': None, 'date': '', 'status': 'unavailable'} for form in forms}}


class PatientWorkspace:
    COOKIE = 'neurogra_session'

    def __init__(self, config, *, reader_factory=RweReader):
        self.config = config
        self.schema = rwe_configuration(config)
        settings = local_settings(config.resolve(self.schema.settings_file))
        self.base_url = settings.get('RWE_API_BASE_URL', 'http://localhost:8081').rstrip('/')
        # Stable configured source identity; the raw URL or optional operator ID is never persisted.
        source_identity = settings.get('RWE_SOURCE_ID', '').strip() or self.base_url
        self.source_id = 'rwe-' + hashlib.sha256(source_identity.encode('utf-8')).hexdigest()
        self.reader_factory = reader_factory
        self.sessions, self.failures = {}, []
        self.lock = threading.RLock()

    def login(self, account, password):
        import requests
        if (not isinstance(account, str) or not 1 <= len(account.strip()) <= 128 or
                not isinstance(password, str) or not 1 <= len(password) <= 512):
            raise WorkspaceError('请输入账号和密码。', 400)
        now = time.time()
        with self.lock:
            self.failures = [timestamp for timestamp in self.failures if now - timestamp < 60]
            if len(self.failures) >= 5:
                raise WorkspaceError('登录尝试过于频繁，请稍后重试。', 429)
            self.failures.append(now)
        try:
            token = obtain_token(requests, self.base_url, account.strip(), password, timeout=15)
        except AuthenticationFailure:
            raise WorkspaceError('登录失败，请检查账号、密码或 RWE 服务。', 401) from None
        session_id = secrets.token_urlsafe(32)
        session = {'account': account.strip(), 'token': token, 'created': now, 'seen': now,
                   'snapshot': None, 'snapshot_at': 0, 'roster': None, 'roster_at': 0,
                   'load_lock': threading.Lock()}
        with self.lock:
            self.failures.clear()
            self._prune(now)
            if len(self.sessions) >= 64:
                raise WorkspaceError('会话数量已达上限，请稍后重试。', 503)
            self.sessions[hashlib.sha256(session_id.encode()).hexdigest()] = session
        return session_id

    def _prune(self, now):
        for key, session in list(self.sessions.items()):
            if now - session['seen'] > 7200 or now - session['created'] > 28800:
                del self.sessions[key]

    def session(self, session_id):
        with self.lock:
            self._prune(time.time())
            session = self.sessions.get(hashlib.sha256(session_id.encode()).hexdigest()) if session_id else None
            if session:
                session['seen'] = time.time()
            return session

    def logout(self, session_id):
        with self.lock:
            if session_id:
                self.sessions.pop(hashlib.sha256(session_id.encode()).hexdigest(), None)

    def public_session(self, session):
        account = session['account']
        masked = account[:3] + '****' + account[-2:] if len(account) > 5 else '已登录'
        return {'authenticated': True, 'account': masked, 'project_id': self.schema.project_id}

    def reader(self, session):
        return self.reader_factory(self.base_url, session['token'], min(self.schema.timeout_seconds, 30))

    def snapshot(self, session, *, refresh=False):
        with session['load_lock']:
            if not refresh and session['snapshot'] is not None and time.time() - session['snapshot_at'] < 60:
                return session['snapshot']
            result = self._load_snapshot(session)
            session['snapshot'], session['snapshot_at'] = result, time.time()
            session['roster'], session['roster_at'] = result['patients'], time.time()
            return result

    def _read_roster(self, session):
        reader = self.reader(session)
        data = reader.query('/patient/queryPatient', {'projectId': self.schema.project_id,
            'page': 1, 'pageSize': 0, 'includeExited': True, 'criteria': []})
        rows = read_rows(data)
        if any(not isinstance(row.get('id'), int) or row['id'] <= 0 for row in rows):
            raise WorkspaceError('RWE 返回的患者标识无效。')
        if len(rows) > 5000:
            raise WorkspaceError('当前项目患者超过 5000 位，请先缩小项目范围。')
        if len({str(row.get('id')) for row in rows}) != len(rows):
            raise WorkspaceError('患者列表存在重复记录，暂不能可靠展示。')
        return rows

    def _load_snapshot(self, session):
        rows = self._read_roster(session)
        reader = self.reader(session)
        patients = {str(row['id']): basic_patient(row, self.schema.forms) for row in rows}
        issues = []
        for offset in range(0, len(rows), 100):
            ids = [int(row['id']) for row in rows[offset:offset + 100]]
            try:
                batch = reader.query('/form/queryDataBatch', {'forms': [{'formId': form.form_id} for form in self.schema.forms],
                    'patientIds': ids, 'page': 1, 'pageSize': 0})
                if not isinstance(batch, dict):
                    raise WorkspaceError('表单响应无效。')
                for form in self.schema.forms:
                    form_rows = read_rows(batch.get(str(form.form_id)))
                    grouped = defaultdict(list)
                    for record in form_rows:
                        patient_id = str(record.get('patient_id', record.get('patientId')))
                        if patient_id not in {str(i) for i in ids}:
                            raise WorkspaceError('表单与患者不匹配，已停止展示。')
                        grouped[patient_id].append(record)
                    for patient_id in ids:
                        records = grouped[str(patient_id)]
                        dates = [str(record.get(form.date_field) or '')[:10] for record in records] if form.date_field else []
                        patients[str(patient_id)]['forms'][form.key] = {'count': len(records), 'date': max(dates, default=''), 'status': 'available'}
            except WorkspaceError as exc:
                if exc.status == 401 or '不匹配' in str(exc):
                    raise
                issues.append('部分表单读取失败，请刷新重试。')
        for patient in patients.values():
            counts = patient['forms'].values()
            patient['available'] = sum((item['count'] or 0) > 0 for item in counts)
            patient['status'] = ('unavailable' if any(item['count'] is None for item in counts) else
                                 'complete' if patient['available'] == len(self.schema.forms) else
                                 'pending' if patient['available'] == 0 else 'incomplete')
            patient['updated'] = max((item['date'] for item in counts), default='')
        return {'patients': list(patients.values()), 'forms': [
            {'key': form.key, 'label': {'demographics': '基本信息表', 'plasma': '血浆', 'apoe': '基因风险', 'csf': '脑脊液'}.get(form.key, form.key.upper()),
             'title': form.name} for form in self.schema.forms],
            'issues': list(dict.fromkeys(issues)), 'project_id': self.schema.project_id}

    def patient(self, session, patient_id, *, refresh=False):
        with session['load_lock']:
            if refresh or session.get('roster') is None or time.time() - session.get('roster_at', 0) >= 60:
                rows = self._read_roster(session)
                session['roster'] = [basic_patient(row, self.schema.forms) for row in rows]
                session['roster_at'] = time.time()
            patient = next((row for row in session['roster'] if row['patientKey'] == str(patient_id)), None)
        if patient is None:
            raise WorkspaceError('患者不存在或无权查看。', 404)
        return patient

    def authorize_forms(self, session, patient_id):
        """Fail closed if the current session can no longer read any configured source form."""
        self.patient(session, patient_id, refresh=True)
        reader = self.reader(session)
        forms = list(self.schema.forms)
        if not forms:
            return
        batch = reader.query('/form/queryDataBatch', {
            'forms': [{'formId': form.form_id} for form in forms],
            'patientId': int(patient_id), 'page': 1, 'pageSize': 0})
        metadata = reader.query('/form/getFormDetailsBatch', {'formIds': [form.form_id for form in forms]})
        if not isinstance(batch, dict) or not isinstance(metadata, dict):
            raise WorkspaceError('表单权限或结构无法验证。', 403)
        for form in forms:
            records = read_rows(batch.get(str(form.form_id)))
            definition = metadata.get(str(form.form_id))
            if records and (not isinstance(definition, dict) or not isinstance(definition.get('fields'), list)):
                raise WorkspaceError('有记录表单的字段结构无法验证。', 403)
            if any(str(row.get('patient_id', row.get('patientId'))) != str(patient_id) for row in records):
                raise WorkspaceError('表单与患者不匹配，已停止展示。')

    def form(self, session, patient_id, key, *, with_sources=False, refresh=True):
        self.patient(session, patient_id, refresh=refresh)
        form = next((item for item in self.schema.forms if item.key == key), None)
        if form is None:
            raise WorkspaceError('表单不存在。', 404)
        reader = self.reader(session)
        body = {'forms': [{'formId': form.form_id}], 'patientId': int(patient_id), 'page': 1, 'pageSize': 0}
        data = reader.query('/form/queryDataBatch', body)
        records = read_rows(data.get(str(form.form_id)) if isinstance(data, dict) else None)
        if any(str(row.get('patient_id', row.get('patientId'))) != str(patient_id) for row in records):
            raise WorkspaceError('表单与患者不匹配，已停止展示。')
        metadata = reader.query('/form/getFormDetailsBatch', {'formIds': [form.form_id]})
        definition = metadata.get(str(form.form_id)) if isinstance(metadata, dict) else None
        if records and (not isinstance(definition, dict) or not isinstance(definition.get('fields'), list)):
            raise WorkspaceError('有记录表单的字段结构无法验证。', 403)
        fields = (metadata.get(str(form.form_id), {}).get('fields') or []) if isinstance(metadata, dict) else []
        labels = {field.get('enName', field.get('en_name')): field.get('name') or field.get('cnName') or field.get('enName')
                  for field in fields if isinstance(field, dict)}
        normalized = []
        for row in records:
            item = {'date': str(row.get(form.date_field) or '')[:10] if form.date_field else '',
                'fields': [[labels.get(name) or name, display_value(value)] for name, value in row.items()
                           if name not in SYSTEM_FIELDS and name not in {'openid', 'id_card_number', 'idCardNumber'}]}
            if with_sources:
                item.update({'record_id': str(row['id']) if row.get('id') is not None else None,
                    'source_fields': [[name, labels.get(name) or name, display_value(value)] for name, value in row.items()
                                      if name not in SYSTEM_FIELDS and name not in {'openid', 'id_card_number', 'idCardNumber'}]})
            normalized.append(item)
        normalized.sort(key=lambda row: row['date'], reverse=True)
        return {'key': key, 'title': form.name, 'records': normalized}
