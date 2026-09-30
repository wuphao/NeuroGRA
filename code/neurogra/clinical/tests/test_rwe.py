import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from neurogra.clinical.config import ClinicalConfig
from neurogra.clinical.intake import ingest_patient
from neurogra.clinical.rwe import RweClient, RweConfig, RweFailure, adapt_export, export_patient


class FakeSource:
    def resolve_patient(self, number):
        if number == 'missing':
            raise RweFailure('patient_not_found')
        return {'patient_id': 123, 'patient_number': number}

    def fetch_form(self, form, patient_id):
        if form.key == 'unavailable':
            raise RweFailure('rwe_api_unavailable')
        return [{'id': 7, 'patient_id': patient_id, '访视日': '日期未明确',
                 '陌生字段/含~': 0, '阴性说明': None, '备注': '未检查，不能判断', '总分': '9.0'}]


class RweTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.config = ClinicalConfig(project_root=Path(tmp.name))
        self.rwe = RweConfig(project_id=42, forms=[{'key': 'arbitrary', 'form_id': 987,
                            'name': '未知量表', 'date_field': '访视日'}])

    def test_arbitrary_schema_preserves_values_and_source_pointer(self):
        raw, source, path = export_patient('new-patient', self.config, self.rwe, FakeSource())
        self.assertEqual(json.loads(path.read_text(encoding='utf-8')), source)
        record = raw['RWE资料'][0]
        self.assertEqual(record['时间'], '日期未明确')
        self.assertEqual(record['原始记录']['总分'], '9.0')
        self.assertIsNone(record['原始记录']['阴性说明'])
        self.assertIn('备注', record['原始记录'])
        intake = ingest_patient(raw, self.config)
        fact = next(r for r in intake.records if r.raw_value == 0)
        self.assertEqual(fact.locator['rwe']['value_pointer'], '/forms/0/records/0/陌生字段~1含~0')
        self.assertFalse(any('/来源/' in r.locator['json_pointer'] for r in intake.records))

    def test_failed_form_is_distinct_from_empty_or_missing_values(self):
        self.rwe.forms.append(self.rwe.forms[0].model_copy(update={'key': 'unavailable', 'form_id': 988}))
        raw, source, _ = export_patient('new-patient', self.config, self.rwe, FakeSource())
        self.assertEqual(source['forms'][1]['status'], 'failed')
        self.assertEqual(source['issues'][0]['code'], 'rwe_api_unavailable')
        self.assertEqual(len(raw['RWE资料']), 1)

    def test_unknown_patient_fails_before_export(self):
        with self.assertRaisesRegex(RweFailure, '^patient_not_found$'):
            export_patient('missing', self.config, self.rwe, FakeSource())

    def test_exports_are_isolated_even_for_same_patient(self):
        first = export_patient('new-patient', self.config, self.rwe, FakeSource())
        second = export_patient('new-patient', self.config, self.rwe, FakeSource())
        self.assertNotEqual(first[2], second[2])
        self.assertEqual(first[1]['patient'], second[1]['patient'])

    def response(self, payload, status=200):
        response = MagicMock()
        response.__enter__.return_value = response
        response.status_code = status
        response.json.return_value = payload
        return response

    def test_cross_patient_response_is_rejected(self):
        client = RweClient(self.rwe, {'RWE_API_TOKEN': 'test-token'})
        with patch('requests.post', return_value=self.response({'code': 0, 'data': {'list': [{'patient_id': 999}]}})):
            with self.assertRaisesRegex(RweFailure, 'rwe_patient_binding_failed'):
                client.fetch_form(self.rwe.forms[0], 123)

    def test_truncated_api_response_is_rejected(self):
        client = RweClient(self.rwe, {'RWE_API_TOKEN': 'test-token'})
        with patch('requests.post', return_value=self.response({'code': 0, 'data': {'list': [], 'total': 1}})):
            with self.assertRaisesRegex(RweFailure, 'rwe_incomplete_form_response'):
                client.fetch_form(self.rwe.forms[0], 123)

    def test_auth_failure_does_not_echo_remote_body_or_credentials(self):
        client = RweClient(self.rwe, {'RWE_API_TOKEN': 'private-test-token'})
        with patch('requests.post', return_value=self.response({'message': 'private response'}, status=401)):
            with self.assertRaisesRegex(RweFailure, '^rwe_authentication_or_permission_failed$'):
                client.fetch_form(self.rwe.forms[0], 123)

    def test_patient_with_no_forms_is_not_a_successful_report(self):
        client = FakeSource()
        client.fetch_form = lambda *args: []
        with self.assertRaisesRegex(RweFailure, '^patient_has_no_records:'):
            export_patient('new-patient', self.config, self.rwe, client)
