import json
import tempfile
import unittest
from pathlib import Path

from neurogra_common.llm_log import configure_llm_log, logged_model_call, record_llm_event


class LogPolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(configure_llm_log, None)
        self.path = Path(self.tmp.name) / 'calls.jsonl'

    def test_compact_mode_retains_call_link_without_copying_patient_or_model_text(self):
        configure_llm_log(self.path, full_content=False, max_bytes=4096)
        response = logged_model_call(lambda *a: {'message': {'content': 'private-result'}},
            'http://localhost/api/chat', {'messages': ['private-patient']}, 3, component='clinical')
        record_llm_event('cache_hit', messages=['private-patient'], output=response)
        text = self.path.read_text(encoding='utf-8')
        self.assertNotIn('private-patient', text)
        self.assertNotIn('private-result', text)
        rows = [json.loads(line) for line in text.splitlines()]
        self.assertEqual(rows[0]['call_id'], rows[1]['call_id'])
        self.assertEqual(rows[0]['metadata']['component'], 'clinical')
        self.assertGreater(rows[0]['request_summary']['bytes'], 0)
        self.assertIn('output_summary', rows[2])

    def test_rotation_caps_total_storage_and_keeps_valid_json_lines(self):
        configure_llm_log(self.path, full_content=True, max_bytes=1024, backup_count=2)
        for index in range(30):
            record_llm_event('response', response='x' * 260, sequence=index)
        files = list(self.path.parent.glob('calls.jsonl*'))
        self.assertEqual(len(files), 3)
        self.assertLessEqual(sum(p.stat().st_size for p in files), 3072)
        for path in files:
            for line in path.read_text(encoding='utf-8').splitlines():
                self.assertEqual(json.loads(line)['event'], 'response')
        last = json.loads(self.path.read_text(encoding='utf-8').splitlines()[-1])
        self.assertEqual(last['sequence'], 29)

    def test_oversized_debug_record_is_bounded_and_explicitly_marked(self):
        configure_llm_log(self.path, max_bytes=1024)
        record_llm_event('response', call_id='synthetic-call', response='x' * 20000)
        row = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(row['call_id'], 'synthetic-call')
        self.assertEqual(row['omitted'], 'record_exceeds_log_limit')
        self.assertLessEqual(self.path.stat().st_size, 1024)
