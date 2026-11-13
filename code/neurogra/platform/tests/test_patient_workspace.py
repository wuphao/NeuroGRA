import json
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from http.client import HTTPConnection
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from neurogra.clinical.rwe_auth import AuthenticationFailure
from neurogra.platform.application import Application
from neurogra.platform.activity import maintenance
from neurogra.platform.chat_database import ChatDatabase
from neurogra.platform.configuration import resolve_config
from neurogra.platform.evochat_store import EvoChatStore
from neurogra.platform.locking import exclusive
from neurogra.platform.patient_chat import PatientChat
from neurogra.platform.patient_workspace import (
    PatientWorkspace,
    RweReader,
    WorkspaceError,
    read_rows,
)
from neurogra.platform.server import make_server


class FakeReader:
    def __init__(self, *args):
        self.calls = []
        self.foreign = False
        self.failed = False
        self.allowed_keys = {11, 12}

    def query(self, path, body):
        self.calls.append((path, body))
        if path == '/patient/queryPatient':
            rows = [{'id': 11, 'patientNumber': 'TEST-001', 'name': 'synthetic', 'gender': 2,
                     'dateOfBirth': '1960-01-01'}, {'id': 12, 'patientNumber': 'TEST-002', 'name': 'synthetic2'}]
            rows = [row for row in rows if row['id'] in self.allowed_keys]
            return {'list': rows, 'pagination': {'total': len(rows)}}
        if path == '/form/getFormDetailsBatch':
            return {'55': {'fields': [{'enName': 'score', 'cnName': '总分'}]}}
        if self.failed:
            raise WorkspaceError('upstream unavailable')
        result = {}
        for form in body['forms']:
            includes_first = body.get('patientId', 11) == 11 and 11 in body.get('patientIds', [11])
            records = ([{'patient_id': 999 if self.foreign else 11, 'score': 23,
                         '访视日期': '2026-11-01', 'id': 501, 'note': '<script>untrusted</script>'}]
                       if form['formId'] == 55 and includes_first else [])
            result[str(form['formId'])] = {'list': records, 'pagination': {'total': len(records)}}
        return result


class PatientWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.reader = FakeReader()
        self.workspace = PatientWorkspace(resolve_config(root=self.tmp.name), reader_factory=lambda *args: self.reader)
        with patch('neurogra.platform.patient_workspace.obtain_token', return_value='synthetic-upstream-token'):
            self.cookie = self.workspace.login('synthetic-account', 'synthetic-password')
        self.session = self.workspace.session(self.cookie)

    def test_login_keeps_upstream_token_server_side_and_no_password(self):
        public = self.workspace.public_session(self.session)
        self.assertNotIn('token', public)
        self.assertNotIn('password', self.session)
        self.assertNotIn(self.cookie, self.workspace.sessions)
        self.assertEqual(list(Path(self.tmp.name).rglob('*')), [])

    def test_snapshot_counts_and_distinguishes_no_records(self):
        result = self.workspace.snapshot(self.session)
        first, empty = result['patients']
        self.assertEqual(first['forms']['moca']['count'], 1)
        self.assertEqual(first['status'], 'incomplete')
        self.assertEqual(first['sex'], '女')
        self.assertEqual(empty['status'], 'pending')
        self.assertEqual(first['updated'], '2026-11-01')
        self.assertEqual(len(self.reader.calls), 2)
        self.workspace.snapshot(self.session)
        self.assertEqual(len(self.reader.calls), 2)

    def test_upstream_failure_is_never_empty_or_complete(self):
        self.reader.failed = True
        result = self.workspace.snapshot(self.session)
        self.assertTrue(result['issues'])
        self.assertTrue(all(patient['status'] == 'unavailable' for patient in result['patients']))
        self.assertIsNone(result['patients'][0]['forms']['moca']['count'])

    def test_foreign_patient_record_rejected(self):
        self.reader.foreign = True
        with self.assertRaisesRegex(WorkspaceError, '不匹配'):
            self.workspace.snapshot(self.session)
        self.assertIsNone(self.session['snapshot'])

    def test_form_authorization_checks_all_forms_in_one_batch_and_rejects_foreign_records(self):
        self.workspace.authorize_forms(self.session, '11')
        self.assertEqual(sum(path == '/form/queryDataBatch' for path, _ in self.reader.calls), 1)
        self.assertEqual(sum(path == '/form/getFormDetailsBatch' for path, _ in self.reader.calls), 1)
        self.reader.foreign = True
        with self.assertRaisesRegex(WorkspaceError, '不匹配'):
            self.workspace.authorize_forms(self.session, '11')

    def test_missing_metadata_for_a_form_with_records_fails_closed(self):
        original_query = self.reader.query

        def query(path, body):
            if path == '/form/getFormDetailsBatch':
                return {}
            return original_query(path, body)

        with patch.object(self.reader, 'query', side_effect=query):
            with self.assertRaisesRegex(WorkspaceError, '字段结构无法验证'):
                self.workspace.authorize_forms(self.session, '11')
            with self.assertRaisesRegex(WorkspaceError, '字段结构无法验证'):
                self.workspace.form(self.session, '11', 'moca')

    def test_patient_outside_authorized_roster_rejected_before_detail(self):
        with self.assertRaises(WorkspaceError) as failure:
            self.workspace.form(self.session, 999, 'moca')
        self.assertEqual(failure.exception.status, 404)
        self.assertFalse(any(path == '/form/queryDataBatch' for path, _ in self.reader.calls))

    def test_detail_maps_labels_and_omits_system_fields(self):
        result = self.workspace.form(self.session, 11, 'moca')
        fields = dict(result['records'][0]['fields'])
        self.assertEqual(fields['总分'], '23')
        self.assertNotIn('patient_id', fields)
        self.assertNotIn('id', fields)
        self.assertEqual(fields['note'], '<script>untrusted</script>')

    def test_unknown_form_rejected(self):
        with self.assertRaises(WorkspaceError) as failure:
            self.workspace.form(self.session, 11, 'arbitrary')
        self.assertEqual(failure.exception.status, 404)

    def test_partial_response_rejected(self):
        with self.assertRaises(WorkspaceError):
            read_rows({'list': [], 'pagination': {'total': 1}})

    def test_logout_and_expiry_revoke_session(self):
        self.workspace.logout(self.cookie)
        self.assertIsNone(self.workspace.session(self.cookie))
        with patch('neurogra.platform.patient_workspace.obtain_token', return_value='other-token'):
            cookie = self.workspace.login('synthetic', 'password')
        self.workspace.session(cookie)['seen'] = time.time() - 7201
        self.assertIsNone(self.workspace.session(cookie))

    def test_sessions_do_not_share_patient_cache(self):
        self.workspace.snapshot(self.session)
        with patch('neurogra.platform.patient_workspace.obtain_token', return_value='other-token'):
            cookie = self.workspace.login('other-account', 'password')
        second = self.workspace.session(cookie)
        self.assertIsNone(second['snapshot'])
        self.assertNotEqual(self.session['token'], second['token'])

    def test_failed_login_does_not_create_session(self):
        count = len(self.workspace.sessions)
        with patch('neurogra.platform.patient_workspace.obtain_token', side_effect=AuthenticationFailure('sensitive-detail')):
            with self.assertRaises(WorkspaceError) as failure:
                self.workspace.login('bad-account', 'wrong-password')
        self.assertEqual(failure.exception.status, 401)
        self.assertNotIn('sensitive-detail', str(failure.exception))
        self.assertEqual(len(self.workspace.sessions), count)

    def test_empty_credentials_do_not_call_upstream(self):
        with patch('neurogra.platform.patient_workspace.obtain_token') as login:
            with self.assertRaises(WorkspaceError):
                self.workspace.login('', '')
        login.assert_not_called()

    def test_mutating_upstream_path_is_not_available(self):
        with self.assertRaises(WorkspaceError):
            RweReader('http://unused', 'synthetic-token').query('/form/insertData', {})

    def test_foreign_record_is_rejected_when_loading_detail(self):
        self.workspace.snapshot(self.session)
        self.reader.foreign = True
        with self.assertRaisesRegex(WorkspaceError, '不匹配'):
            self.workspace.form(self.session, 11, 'moca')


class PatientWorkspaceHttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = Application(self.tmp.name)
        self.server = make_server(self.app, 0)
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=.05))
        self.thread.start()
        self.addCleanup(self.stop)
        self.reader = FakeReader()
        self.login_patch = patch('neurogra.platform.patient_workspace.obtain_token', return_value='synthetic-private-token')
        self.reader_patch = patch.object(RweReader, 'query', side_effect=self.reader.query)
        self.login_patch.start()
        self.reader_patch.start()
        self.addCleanup(self.login_patch.stop)
        self.addCleanup(self.reader_patch.stop)
        self.cookie = None
        _, _, bootstrap = self.request('GET', '/api/session')
        self.csrf = json.loads(bootstrap)['token']

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, method, path, body=None, headers=None):
        values = {'Content-Type': 'application/json'}
        if getattr(self, 'csrf', None):
            values['X-Workbench-Token'] = self.csrf
        if self.cookie:
            values['Cookie'] = self.cookie
        values.update(headers or {})
        connection = HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            connection.request(method, path, body=json.dumps(body) if body is not None else None, headers=values)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def login(self):
        status, headers, body = self.request('POST', '/api/auth/login', {'account': 'synthetic-account', 'password': 'synthetic-password'})
        self.assertEqual(status, 200)
        self.assertNotIn(b'synthetic-private-token', body)
        self.cookie = headers['Set-Cookie'].split(';')[0]
        return headers

    def test_unauthenticated_patient_and_local_apis_are_protected(self):
        for path in ['/api/patients', '/api/patients/11/forms/moca', '/api/cases', '/api/jobs']:
            status, _, _ = self.request('GET', path)
            self.assertEqual(status, 401)
        status, headers, _ = self.request('GET', '/patients')
        self.assertEqual(status, 303)
        self.assertEqual(headers['Location'], '/login')

    def test_http_requests_do_not_enter_during_maintenance(self):
        with maintenance(self.app.root):
            status, _, body = self.request('GET', '/api/session')
        self.assertEqual(status, 503)
        self.assertIn('维护', json.loads(body)['error'])
        self.assertEqual(self.request('GET', '/api/session')[0], 200)
        self.assertEqual(len(self.reader.calls), 0)

    def test_login_real_http_cookie_and_logout_revocation(self):
        headers = self.login()
        self.assertIn('HttpOnly', headers['Set-Cookie'])
        self.assertIn('SameSite=Strict', headers['Set-Cookie'])
        status, _, data = self.request('GET', '/api/patients')
        self.assertEqual(status, 200)
        self.assertEqual(len(json.loads(data)['patients']), 2)
        status, _, data = self.request('GET', '/api/patients/11/forms/moca')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)['records'][0]['date'], '2026-11-01')
        saved_cookie = self.cookie
        status, _, _ = self.request('POST', '/api/auth/logout', {})
        self.assertEqual(status, 200)
        self.cookie = saved_cookie
        self.assertEqual(self.request('GET', '/api/patients')[0], 401)

    def test_invalid_cookie_cross_origin_and_missing_csrf(self):
        self.cookie = 'neurogra_session=forged'
        self.assertEqual(self.request('GET', '/api/patients')[0], 401)
        self.assertEqual(self.request('GET', '/api/auth/session', headers={'Origin': 'http://untrusted.example'})[0], 403)
        self.assertEqual(self.request('POST', '/api/auth/login', {}, headers={'X-Workbench-Token': ''})[0], 403)

    def test_login_page_is_public_but_does_not_contain_secrets(self):
        status, headers, body = self.request('GET', '/login')
        self.assertEqual(status, 200)
        self.assertIn(b'/login.js', body)
        self.assertIn('no-store', headers['Cache-Control'])
        self.assertNotIn(b'synthetic-password', body)

    def test_unknown_patient_is_not_read_from_forms(self):
        self.login()
        self.assertEqual(self.request('GET', '/api/patients/999/forms/moca')[0], 404)
        self.assertFalse(any('patientId' in body and body['patientId'] == 999 for _, body in self.reader.calls))

    def test_patient_scope_required_for_chat_writes(self):
        self.login()
        self.assertEqual(self.request('POST', '/api/chat', {})[0], 400)
        self.assertEqual(self.request('POST', '/api/chat/legacy', {'message': '测试'})[0], 400)
        self.assertEqual(self.request('POST', '/api/chat/legacy/analysis', {})[0], 400)
        status, _, payload = self.request('POST', '/api/patients/11/chat', {})
        self.assertEqual(status, 201)
        sid = json.loads(payload)['id']
        self.assertEqual(self.request('GET', f'/api/patients/11/chat/{sid}')[0], 200)
        self.assertEqual(self.request('GET', f'/api/patients/12/chat/{sid}')[0], 404)
        self.assertEqual(self.request('POST', f'/api/patients/12/chat/{sid}/analysis',
                                      {'request_id': 'wrong-patient'})[0], 404)

    def test_cached_roster_does_not_preserve_revoked_chat_access(self):
        self.login()
        status, _, payload = self.request('POST', '/api/patients/11/chat', {})
        self.assertEqual(status, 201)
        sid = json.loads(payload)['id']
        self.reader.allowed_keys.remove(11)
        self.assertEqual(self.request('GET', f'/api/patients/11/chat/{sid}')[0], 404)

    def test_comparison_selection_persists_and_rechecks_access(self):
        self.login()
        _, _, payload = self.request('POST', '/api/patients/11/chat', {})
        sid = json.loads(payload)['id']
        path = f'/api/patients/11/chat/{sid}'
        status, _, payload = self.request('POST', path + '/subjects', {'patient_ids': ['12']})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(payload)['patients'][0]['patientKey'], '12')
        restored = json.loads(self.request('GET', path)[2])['selected_patients']
        self.assertEqual([patient['patientKey'] for patient in restored], ['12'])
        self.reader.allowed_keys.remove(12)
        self.assertEqual(json.loads(self.request('GET', path)[2])['selected_patients'], [])
        self.assertEqual(self.request('POST', path + '/subjects', {'patient_ids': ['12']})[0], 404)

    def test_generic_case_routes_reject_unbound_or_revoked_cases(self):
        self.login()
        local = self.app.import_case({'患者ID': 'unbound-local', '病史': 'synthetic'})
        workspace = PatientWorkspace(self.app.config)
        scoped = self.app.import_case({'患者ID': 'TEST-001', '病史': 'synthetic'}, source={
            'kind': 'rwe_workspace', 'source_id': workspace.source_id,
            'project_id': workspace.schema.project_id, 'patient_id': '11'})
        status, _, data = self.request('GET', '/api/cases')
        self.assertEqual(status, 200)
        self.assertEqual([row['case_id'] for row in json.loads(data)['cases']], [scoped['case_id']])
        self.assertEqual(self.request('GET', f"/api/versions/{local['version_id']}")[0], 404)
        self.assertEqual(self.request('GET', f"/api/versions/{scoped['version_id']}")[0], 200)
        self.reader.allowed_keys.remove(11)
        self.assertEqual(self.request('GET', f"/api/versions/{scoped['version_id']}")[0], 404)

    def test_async_http_turn_survives_model_contention_and_reload(self):
        self.login()
        status, _, payload = self.request('POST', '/api/patients/11/chat', {})
        self.assertEqual(status, 201)
        sid = json.loads(payload)['id']
        path = f'/api/patients/11/chat/{sid}'

        @contextmanager
        def memory(_store, _scope):
            yield SimpleNamespace(format_for_system_prompt=lambda _: '')

        def model(_chat, *args, model_access=None, **kwargs):
            with model_access('http-test'):
                return '合成回复。', []

        with patch.object(EvoChatStore, 'memory', memory), patch.object(PatientChat, 'model_reply', model):
            with exclusive(self.app.root, 'model_resource'):
                status, _, payload = self.request('POST', path, {'message': '你好', 'request_id': 'http-turn-1',
                    'comparison_patient_ids': [], 'capabilities': ['async_turns_v1', 'interactions_v1']})
                self.assertEqual(status, 202)
                self.assertIn(json.loads(payload)['turn']['status'], {'accepted', 'running', 'waiting_model'})
                deadline = time.time() + 5
                while time.time() < deadline:
                    state = json.loads(self.request('GET', path + '/turns/http-turn-1')[2])['turn']['status']
                    if state == 'waiting_model':
                        break
                    time.sleep(.05)
                self.assertEqual(state, 'waiting_model')
                messages = json.loads(self.request('GET', path)[2])['messages']
                self.assertTrue(any(message.get('turn', {}).get('status') == 'waiting_model' for message in messages))
            deadline = time.time() + 5
            while time.time() < deadline:
                turn = json.loads(self.request('GET', path + '/turns/http-turn-1')[2])
                if turn['turn']['status'] == 'completed':
                    break
                time.sleep(.05)
            self.assertEqual(turn['reply']['text'], '合成回复。')
            messages = json.loads(self.request('GET', path)[2])['messages']
            self.assertEqual([item['text'] for item in messages], ['你好', '合成回复。'])

    def test_collaboration_progress_http_validates_scope_and_cursor(self):
        self.login()
        status, _, payload = self.request('POST', '/api/patients/11/chat', {})
        self.assertEqual(status, 201)
        sid = json.loads(payload)['id']
        ChatDatabase(self.app.root).begin_turn(sid, 'http-progress', 'digest', '11', 'owner', '合成问题')
        path = f'/api/patients/11/chat/{sid}/turns/http-progress/collaboration'
        status, _, payload = self.request('GET', path + '?after=0&limit=10')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(payload), {'available': False, 'events': [], 'next_cursor': 0})
        for query in ('?after=-1', '?limit=201', '?limit=x', '?after=1&after=2', '?extra=1'):
            self.assertEqual(self.request('GET', path + query)[0], 400)
        self.assertEqual(self.request('GET', path.replace('/patients/11/', '/patients/12/'))[0], 404)
        self.cookie = None
        self.assertEqual(self.request('GET', path)[0], 401)

    def test_resume_http_disallows_overrides_and_dispatches(self):
        self.login()
        _, _, payload = self.request('POST', '/api/patients/11/chat', {})
        sid = json.loads(payload)['id']
        path = f'/api/patients/11/chat/{sid}/turns/http-resume/resume'
        with patch('neurogra.platform.async_chat.AsyncChat.resume', return_value={
                'turn': {'request_id': 'http-resume', 'status': 'accepted'},
                'reply': {'kind': 'assistant', 'text': '已接收'}}) as resume:
            self.assertEqual(self.request('POST', path, {'message': '替换原问题'})[0], 400)
            resume.assert_not_called()
            status, _, payload = self.request('POST', path, {})
            self.assertEqual(status, 202)
            self.assertEqual(json.loads(payload)['turn']['status'], 'accepted')
            self.assertEqual(resume.call_args.args[2:], ('11', sid, 'http-resume'))
        self.cookie = None
        self.assertEqual(self.request('POST', path, {})[0], 401)

    def test_expired_upstream_authentication_revokes_local_session(self):
        self.login()
        with patch.object(RweReader, 'query', side_effect=WorkspaceError('登录失效', 401)):
            self.assertEqual(self.request('GET', '/api/patients')[0], 401)
        self.assertFalse(json.loads(self.request('GET', '/api/auth/session')[2])['authenticated'])


if __name__ == '__main__':
    unittest.main()
