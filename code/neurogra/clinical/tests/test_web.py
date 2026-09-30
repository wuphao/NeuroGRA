import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from neurogra.clinical.config import ClinicalConfig
from neurogra.clinical.web import Workbench, make_server


class WebTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = ClinicalConfig(project_root=self.root)
        self.release = threading.Event()
        self.started = threading.Event()
        self.finished = threading.Event()
        self.calls = []

        def runner(patient, config, rwe, on_run_created):
            self.calls.append(patient)
            run_id = self.app.store.create_run(config.model_dump(mode='json'), config.budget.model_dump())
            on_run_created(run_id)
            self.started.set()
            self.release.wait(5)
            self.finished.set()
            return SimpleNamespace(status='completed')

        self.app = Workbench(self.config, None, runner)
        self.server = make_server(self.app, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def request(self, path, body=None, headers=None):
        req = Request(self.url + path, data=None if body is None else json.dumps(body).encode(),
                      headers=headers or {})
        try:
            with urlopen(req, timeout=3) as r:
                return r.status, json.load(r)
        except HTTPError as e:
            return e.code, json.load(e)

    def test_security_boundary(self):
        self.assertEqual(self.request('/api/jobs', {}, {})[0], 403)
        self.assertEqual(self.request('/api/jobs', headers={'Host': 'evil.example'})[0], 403)
        self.assertEqual(self.request('/api/session', headers={'Origin': 'https://evil.example'})[0], 403)
        self.assertEqual(self.request('/api/session', headers={'Sec-Fetch-Site': 'cross-site'})[0], 403)
        self.assertEqual(self.request('/.env.rwe.local')[0], 404)
        self.assertEqual(self.request('/../pyproject.toml')[0], 404)

    def test_duplicate_patient_does_not_duplicate_work(self):
        headers = {'X-Workbench-Token': self.app.token}
        status, first = self.request('/api/jobs', {'patient_number': 'Arbitrary-Patient'}, headers)
        self.assertEqual(status, 202)
        self.assertTrue(self.started.wait(2))
        status, second = self.request('/api/jobs', {'patient_number': 'Arbitrary-Patient'}, headers)
        self.assertEqual(first['job_id'], second['job_id'])
        self.assertEqual(self.calls, ['Arbitrary-Patient'])
        self.assertEqual(self.request('/api/jobs', {'patient_number': 'Another'}, headers)[0], 400)
        status, detail = self.request('/api/jobs/' + first['job_id'])
        self.assertEqual(status, 200)
        self.assertIsNotNone(detail['run_id'])

    def test_invalid_inputs_and_missing_artifacts(self):
        headers = {'X-Workbench-Token': self.app.token}
        for body in ([], {}, {'patient_number': 1}, {'patient_number': ''}, {'patient_number': 'x' * 129}):
            self.assertEqual(self.request('/api/jobs', body, headers)[0], 400)
        self.assertEqual(self.request('/api/jobs/' + 'a'*32)[0], 404)
        self.assertEqual(self.request('/api/reports/' + 'a'*32)[0], 404)

    def test_artifact_identity_must_match(self):
        run_id = self.app.store.create_run({}, self.config.budget.model_dump())
        path = self.root / 'provenance.json'
        path.write_text(json.dumps({'run_id': run_id, 'patient_id': 'wrong'}))
        self.app.store.save(run_id, 'run_result', 'main', {
            'status': 'completed', 'report': {'run_id': run_id, 'patient_id': 'right'},
            'report_paths': {'provenance': str(path)}})
        self.assertEqual(self.request('/api/reports/' + run_id)[0], 500)
        path.write_text(json.dumps({'run_id': run_id, 'patient_id': 'right'}))
        self.assertEqual(self.request('/api/reports/' + run_id)[0], 200)

    def test_restart_marks_unfinished_jobs_interrupted(self):
        with self.app.store.connect() as db:
            db.execute("INSERT INTO web_jobs VALUES('old','patient',NULL,'running',0,NULL)")
        recovered = Workbench(self.config, None)
        self.assertEqual(recovered.job('old')['status'], 'interrupted')


if __name__ == '__main__':
    unittest.main()
