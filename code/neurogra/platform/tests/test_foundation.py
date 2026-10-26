import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from neurogra.platform.configuration import public_config, resolve_config
from neurogra.platform.integrity import inspect_history
from neurogra.platform.preflight import inspect_environment


class FoundationTests(unittest.TestCase):
    def test_config_precedence_and_nested_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'custom.yaml'
            path.write_text('budget:\n  seconds: 456\nmodel:\n  timeout_seconds: 77\n', encoding='utf-8')
            config = resolve_config(path, profile='quick', root=tmp,
                                    overrides={'budget': {'seconds': 789}})
            self.assertEqual(config.budget.seconds, 789)
            self.assertEqual(config.model.timeout_seconds, 77)
            self.assertEqual(config.max_rounds, 1)
            self.assertEqual(config.kb.root, Path(tmp).resolve())
            self.assertEqual(config.kg.root, config.project_root)

    def test_disabled_knowledge_never_initializes_sdk(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = resolve_config(profile='patient-only', root=tmp)
            with patch('neurogra_kb.TextKnowledgeService', side_effect=AssertionError('disabled')), \
                 patch('neurogra.clinical.retrieval.pin_release', side_effect=AssertionError('disabled')):
                result = inspect_environment(config, services=False)
            self.assertFalse(result['ready'])  # Generation service deliberately unverified.
            self.assertFalse(result['inference_executed'])
            self.assertFalse(any(c['required'] for c in result['checks'] if c['name'] in {'kb', 'kg'}))

    def test_unknown_profile_is_rejected(self):
        with self.assertRaises(ValueError):
            resolve_config(profile='invalid')

    def test_history_missing_artifact_does_not_change_execution_or_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = resolve_config(root=tmp)
            db_path = config.resolve(config.database)
            db_path.parent.mkdir(parents=True)
            with closing(sqlite3.connect(db_path)) as db, db:
                db.execute('CREATE TABLE objects(run_id TEXT,kind TEXT,version INTEGER,payload TEXT)')
                db.execute('INSERT INTO objects VALUES(?,?,?,?)', ('run1', 'run_result', 1,
                    json.dumps({'status': 'completed', 'report_paths': {'markdown': str(Path(tmp) / 'gone.md')}})))
            before = db_path.read_bytes()
            result = inspect_history(config)
            self.assertEqual(result['missing'], 1)
            self.assertEqual(result['runs'][0]['execution_status'], 'completed')
            self.assertEqual(result['runs'][0]['artifact_status'], 'unavailable')
            self.assertEqual(db_path.read_bytes(), before)

    def test_public_configuration_does_not_echo_secret(self):
        config = resolve_config()
        value = public_config(config)
        self.assertEqual(value['model']['model'], config.model.model)
