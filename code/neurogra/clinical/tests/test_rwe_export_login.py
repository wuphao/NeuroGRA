"""Standalone exporter authentication without contacting RWE."""
import argparse
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import requests


ROOT = Path(__file__).resolve().parents[4]
SPEC = importlib.util.spec_from_file_location('rwe_login_test_exporter', ROOT / 'code/tools/export_rwe_patient.py')
EXPORTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EXPORTER)


class ExportLoginTests(unittest.TestCase):
    def setUp(self):
        self.args = argparse.Namespace(api_base_url='http://localhost:8081', timeout=30,
                                      login_account='test-account', login_password='test-secret')
        self.http = Mock(RequestException=requests.RequestException)
        self.http.post.return_value.status_code = 200

    def test_login_request_and_token(self):
        self.http.post.return_value.json.return_value = {'code': 0, 'data': {'token': 'new-token'}}
        self.assertEqual(EXPORTER.login_token(self.http, self.args), 'new-token')
        self.http.post.assert_called_once_with('http://localhost:8081/account/login',
            json={'phone': 'test-account', 'password': 'test-secret'},
            timeout=30, allow_redirects=False)

    def test_invalid_login_responses(self):
        for payload in ({'code': 1}, {'code': 0, 'data': None}, [],
                        {'code': 0, 'data': {'token': 'bad\ntoken'}},
                        {'code': 0, 'data': {'token': ''}}):
            with self.subTest(payload=payload):
                self.http.post.return_value.json.return_value = payload
                with self.assertRaises(EXPORTER.ExportFailure):
                    EXPORTER.login_token(self.http, self.args)

    def test_http_and_transport_errors(self):
        self.http.post.return_value.status_code = 403
        with self.assertRaisesRegex(EXPORTER.ExportFailure, 'HTTP 403'):
            EXPORTER.login_token(self.http, self.args)
        self.http.post.side_effect = requests.ConnectionError('private detail')
        with self.assertRaises(EXPORTER.ExportFailure) as caught:
            EXPORTER.login_token(self.http, self.args)
        self.assertNotIn('private detail', str(caught.exception))

    def test_export_refreshes_old_token_before_resolving_patient(self):
        self.args.token = 'expired-token'
        with patch.object(EXPORTER, 'dependencies', return_value=(object(), self.http)), \
             patch.object(EXPORTER, 'login_token', return_value='fresh-token') as login, \
             patch.object(EXPORTER, 'resolve_patient', side_effect=EXPORTER.ExportFailure('stop')):
            with self.assertRaisesRegex(EXPORTER.ExportFailure, 'stop'):
                EXPORTER.export_patient(self.args)
        login.assert_called_once_with(self.http, self.args)
        self.assertEqual(self.args.token, 'fresh-token')


if __name__ == '__main__':
    unittest.main()
