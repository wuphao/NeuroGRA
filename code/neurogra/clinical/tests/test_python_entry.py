import json
import runpy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from neurogra.clinical.config import ClinicalConfig, AgentConfig, KnowledgeAccess
from neurogra.clinical.service import prepare_patient
from neurogra.clinical.storage import RunStore
from neurogra.clinical.main_tools import MainToolExecutor
from neurogra.clinical.orchestration import MainAgent
from neurogra.clinical.iteration import run_main_loop
from neurogra.clinical.schemas import AgentResult, RequestContent
from neurogra.clinical.utils import identity
from neurogra_kb import KBConfig, TextKnowledgeService


class PythonEntryTests(unittest.TestCase):
    def test_code_entry_constructs_config_without_yaml(self):
        root = Path(__file__).resolve().parents[4]
        with patch('yaml.safe_load', side_effect=AssertionError('YAML must not be read')), \
             patch.dict('sys.modules', {'neurogra.clinical.rwe': None}):
            entry = runpy.run_path(str(root / 'code/main.py'))
            config = entry['build_config']()
        self.assertEqual(config.project_root, root)
        self.assertEqual(config.model.model, 'qwen3.8:27b')
        self.assertEqual(config.kb.embedding.model, 'qwen3-embedding:0.6b')
        restored = ClinicalConfig.model_validate_json(config.model_dump_json())
        self.assertEqual(restored, config)

    def test_file_input_validates_existing_format_and_relative_paths(self):
        root = Path(__file__).resolve().parents[4]
        entry = runpy.run_path(str(root / 'code/main.py'))
        namespace = entry['read_patient_json'].__globals__
        namespace['PATIENT_FILE'] = 'code/neurogra/clinical/tests/fixtures/multispecialty.json'
        patient = entry['read_patient_json']()
        self.assertIn('患者ID', patient)
        self.assertIn('认知评估', patient)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'invalid.json'
            path.write_text('[]', encoding='utf-8')
            namespace['PATIENT_FILE'] = path
            with self.assertRaises(ValueError):
                entry['read_patient_json']()

    def test_all_sources_disabled_do_not_initialize_or_pin_knowledge(self):
        with tempfile.TemporaryDirectory() as directory:
            config = ClinicalConfig(project_root=Path(directory),
                main_agent=KnowledgeAccess(use_kb=False, use_kg=False),
                agents={'history': AgentConfig(use_kb=False, use_kg=False)})
            with patch('neurogra.clinical.service.pin_release', side_effect=AssertionError('KG disabled')), \
                 patch('neurogra.clinical.main_tools.TextKnowledgeService', side_effect=AssertionError('KB disabled')):
                prep = prepare_patient({'患者ID': 'synthetic', '病史': '记忆下降'}, config, use_model=False)
                tool = MainToolExecutor(prep.snapshot, config, RunStore(config.resolve(config.database)), prep.run_id)
            self.assertIsNone(tool.text)
            self.assertIsNone(prep.knowledge_release_id)
            self.assertFalse(any(i.code == 'knowledge_unavailable' for i in prep.issues))

    def test_independent_permissions_and_disabled_specialists_in_loop(self):
        # Main has no knowledge access; history can use raw KB, cognition cannot.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = ClinicalConfig(project_root=root, text_knowledge_config=None,
                main_agent=KnowledgeAccess(use_kb=False, use_kg=False), agents={
                    'history': AgentConfig(use_kb=True, use_kg=False),
                    'cognition': AgentConfig(use_kb=False, use_kg=False),
                    'laboratory': AgentConfig(enabled=False),
                    'imaging': AgentConfig(enabled=False)})
            doc = root / 'doc.json'
            doc.write_text(json.dumps({'blocks': [{'kind': 'paragraph', 'page': 1,
                'text': '记忆下降 病史 整理实际提供的资料并指出解释限制 MMSE 认知量表。'}]}, ensure_ascii=False), encoding='utf-8')
            kb = TextKnowledgeService(KBConfig(root=root))
            kb.publish([doc], vector=False)
            prep = prepare_patient({'患者ID': 'synthetic', '病史': '记忆下降', 'MMSE总分': 24,
                                    '血浆p-tau217': 0.3}, config, use_model=False)
            self.assertNotIn('laboratory', [t.agent_type for t in prep.plan.tasks])
            store = RunStore(config.resolve(config.database))
            tools = MainToolExecutor(prep.snapshot, config, store, prep.run_id, text_service=kb)
            captured = {}
            class Handler:
                async def analyze(self, task, context):
                    captured[task.agent_type] = context
                    return AgentResult(result_id=identity('result', task.task_id), task_id=task.task_id,
                        case_version=task.case_version, status='completed',
                        requests=[RequestContent(type='knowledge', question='不要为关闭知识的认知角色查询', reason='test')]
                            if task.agent_type == 'cognition' else [])
            state = run_main_loop(prep, config, store, handlers={r: Handler() for r in config.enabled_roles()},
                tools=tools, main_factory=lambda _: MainAgent(None))
            self.assertTrue(captured['history'].evidence)
            self.assertEqual(captured['cognition'].evidence, [])
            self.assertFalse(captured['cognition'].request_outcomes)
            self.assertNotIn('laboratory', state['results'])
            self.assertFalse(state['pending'])

    def test_backend_switch_prevents_query_and_source_expansion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = ClinicalConfig(project_root=root, text_knowledge_config=None)
            prep = prepare_patient({'患者ID': 'synthetic', '病史': '记忆下降'}, config, use_model=False)
            doc = root / 'doc.json'
            doc.write_text(json.dumps({'blocks': [{'kind': 'paragraph', 'page': 1, 'text': 'MMSE 原文'}]}), encoding='utf-8')
            kb = TextKnowledgeService(KBConfig(root=root))
            kb.publish([doc], vector=False)
            store = RunStore(config.resolve(config.database))
            tools = MainToolExecutor(prep.snapshot, config, store, prep.run_id, text_service=kb)
            allowed = tools.execute(RequestContent(type='knowledge', question='MMSE', reason='test'), scope='allowed')
            ref = allowed['data']['evidence'][0]['span_id']
            denied = KnowledgeAccess(use_kb=False, use_kg=False)
            with patch.object(kb, 'search_text', side_effect=AssertionError('disabled query')), \
                 patch.object(kb, 'expand_source', side_effect=AssertionError('disabled expansion')):
                result = tools.execute(RequestContent(type='knowledge', question='MMSE', reason='test'),
                    scope='denied', knowledge_access=denied)
                source = tools.execute(RequestContent(type='source_context', question='read', reason='test', source_refs=[ref]),
                    scope='denied_source', knowledge_access=denied)
            self.assertFalse(result['data']['evidence'])
            self.assertEqual(source['status'], 'failed')

    def test_mixed_results_are_scoped_before_specialist_delivery(self):
        with tempfile.TemporaryDirectory() as directory:
            config = ClinicalConfig(project_root=Path(directory), text_knowledge_config=None,
                max_rounds=1, agents={'history': AgentConfig(use_kb=True, use_kg=False),
                                     'cognition': AgentConfig(use_kb=False, use_kg=True)})
            prep = prepare_patient({'患者ID': 'synthetic', '病史': '记忆下降', 'MMSE总分': 24},
                                   config, use_model=False)
            store = RunStore(config.resolve(config.database))
            tools = MainToolExecutor(prep.snapshot, config, store, prep.run_id)
            mixed = {'evidence': [
                {'evidence_id': 'evidence_test_kb', 'document_id': 'doc', 'span_id': 'span_kb',
                 'quote': '原文知识', 'version': 'kb_release_test', 'matched_by': ['kb_lexical']},
                {'evidence_id': 'evidence_test_kg', 'document_id': 'doc', 'span_id': 'span_kg',
                 'quote': '图谱知识', 'version': 'kg_release_test', 'matched_by': ['kg'], 'statement_id': 'statement_test'}],
                'backend_status': {'kb_lexical': 'ok', 'kg_graph': 'ok'},
                'kb_release_id': 'kb_release_test', 'kg_release_id': 'kg_release_test', 'gaps': [], 'data_requests': []}
            captured = {}
            class Handler:
                async def analyze(self, task, context):
                    captured[task.agent_type] = context
                    return AgentResult(result_id=identity('result', task.task_id), task_id=task.task_id,
                        case_version=task.case_version, status='completed')
            with patch.object(tools, '_knowledge', return_value=mixed):
                run_main_loop(prep, config, store, handlers={r: Handler() for r in config.enabled_roles()}, tools=tools)
            self.assertEqual([e.evidence_id for e in captured['history'].evidence], ['evidence_test_kb'])
            self.assertEqual([e.evidence_id for e in captured['cognition'].evidence], ['evidence_test_kg'])
            self.assertNotIn('kg_release_id', captured['history'].request_outcomes[0]['data'])
            self.assertNotIn('kb_release_id', captured['cognition'].request_outcomes[0]['data'])


if __name__ == '__main__':
    unittest.main()
