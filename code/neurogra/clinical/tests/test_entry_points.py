import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from neurogra_kb import KBConfig, TextKnowledgeService
from neurogra.clinical.cli import main
from neurogra.clinical.config import ClinicalConfig
from neurogra.clinical.service import prepare_patient


class EntryPointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = ClinicalConfig(project_root=self.root)
        doc = self.root / 'doc.json'
        doc.write_text(json.dumps({'blocks': [{'kind': 'paragraph', 'page': 1, 'text': 'MMSE 认知原文，无图谱声明。'}]}), encoding='utf-8')
        self.kb = TextKnowledgeService(KBConfig(root=self.root))
        self.release = self.kb.publish([doc], vector=False)['release_id']

    def test_prepare_query_returns_raw_kb_when_kg_is_unavailable(self):
        preparation = prepare_patient({'患者ID': 'synthetic', '病史': '记忆下降'}, self.config,
                                      use_model=False, query='MMSE')
        self.assertIsNone(preparation.knowledge_release_id)
        self.assertTrue(preparation.evidence.items)
        self.assertIsNone(preparation.evidence.items[0].statement_id)
        self.assertEqual(preparation.evidence.items[0].version, self.release)

    def test_cli_search_uses_same_kb_tool_and_never_constructs_legacy_index(self):
        stream = io.StringIO()
        with patch('sys.argv', ['cli', 'search', '--query', 'MMSE']), \
                patch('neurogra.clinical.cli.load_config', return_value=self.config), \
                contextlib.redirect_stdout(stream):
            main()
        output = json.loads(stream.getvalue())
        self.assertEqual(output['result']['status'], 'found')
        self.assertEqual(output['result']['data']['kb_release_id'], self.release)

    def test_cli_index_inspection_does_not_rebuild_release(self):
        before = self.kb.get_release(self.release)
        stream = io.StringIO()
        with patch('sys.argv', ['cli', 'inspect-knowledge-index', '--module', 'kb']), \
                patch('neurogra.clinical.cli.load_config', return_value=self.config), \
                contextlib.redirect_stdout(stream):
            main()
        self.assertEqual(json.loads(stream.getvalue()), before)
        self.assertEqual(self.kb.get_release(self.release), before)
