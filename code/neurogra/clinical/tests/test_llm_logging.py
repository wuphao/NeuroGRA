import contextlib
import io
import json
import runpy
import tempfile
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from neurogra.clinical.config import ClinicalConfig
from neurogra.clinical.llm import ModelFailure, ModelGateway
from neurogra.clinical.schemas import Contract
from neurogra.clinical.storage import RunStore
from neurogra_common.llm_log import configure_llm_log, logged_model_call
from neurogra_kg.adapters.ollama_llm import OllamaLLMAdapter
from neurogra_kg.config import LLMConfig


class Answer(Contract):
    ok: bool


class LLMLogTests(unittest.TestCase):
    def setUp(self):
        base = Path(__file__).resolve().parents[4] / 'output/test_llm_logging'
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(configure_llm_log, None)
        self.root = Path(self.temp.name)
        self.path = configure_llm_log(self.root / 'calls.jsonl')

    def records(self):
        return [json.loads(line) for line in self.path.read_text(encoding='utf-8').splitlines()]

    def test_clinical_retry_and_repair_preserve_full_context_and_raw_response(self):
        config = ClinicalConfig(project_root=self.root)
        store = RunStore(self.root / 'runs.sqlite')
        run = store.create_run(config.model_dump(mode='json'), config.budget.model_dump())
        attempts = []
        def transport(url, payload, timeout):
            attempts.append(payload)
            if len(attempts) == 1:
                raise urllib.error.URLError(ConnectionRefusedError('synthetic connection refused'))
            return {'message': {'content': '不是JSON' if len(attempts) == 2 else '{"ok":true}'},
                    'prompt_eval_count': 10, 'eval_count': 3}
        gateway = ModelGateway(config.model, store, run, transport=transport, scope='history')
        self.assertTrue(gateway.generate_structured('中文完整提示词', {'病史': '完整上下文'}, Answer).ok)
        rows = self.records()
        requests = [r for r in rows if r['event'] == 'request']
        self.assertEqual(len(requests), 3)
        self.assertEqual(len({r['call_id'] for r in requests}), 3)
        self.assertIn('中文完整提示词', requests[0]['request']['messages'][0]['content'])
        self.assertEqual(json.loads(requests[0]['request']['messages'][1]['content']), {'病史': '完整上下文'})
        self.assertEqual(len(requests[0]['request']['messages']), 2)
        self.assertEqual(len(requests[-1]['request']['messages']), 3)
        self.assertEqual(requests[0]['metadata']['run_id'], run)
        self.assertEqual([r['response']['message']['content'] for r in rows if r['event'] == 'response'],
                         ['不是JSON', '{"ok":true}'])
        self.assertEqual(len([r for r in rows if r['event'] == 'error']), 1)

    def test_timeout_after_send_is_indeterminate_and_not_retried(self):
        config = ClinicalConfig(project_root=self.root)
        store = RunStore(self.root / 'runs.sqlite')
        run = store.create_run(config.model_dump(mode='json'), config.budget.model_dump())
        transport = unittest.mock.Mock(side_effect=TimeoutError('synthetic timeout'))
        with self.assertRaisesRegex(ModelFailure, 'model_call_indeterminate'):
            ModelGateway(config.model, store, run, transport=transport).generate_structured('system', {}, Answer)
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(store.summary(run)['calls'], [{'kind': 'llm', 'status': 'indeterminate', 'n': 1}])

    def test_kg_repair_and_cache_are_audited(self):
        calls = []
        def transport(url, payload, timeout):
            if url.endswith('/api/tags'):
                return {'models': [{'name': 'qwen3.8:27b', 'digest': 'test_digest'}]}
            calls.append(payload)
            return {'message': {'content': 'bad' if len(calls) == 1 else '{"ok":true}'}}
        adapter = OllamaLLMAdapter(LLMConfig(), self.root / 'kg', transport=transport)
        self.assertTrue(adapter.execute('probe', {'上下文': 'KG原始文档'}, Answer).ok)
        self.assertTrue(adapter.execute('probe', {'上下文': 'KG原始文档'}, Answer).ok)
        rows = self.records()
        requests = [r for r in rows if r['event'] == 'request']
        self.assertEqual(len(requests), 2)
        self.assertIn('KG原始文档', requests[0]['request']['messages'][1]['content'])
        self.assertEqual(requests[0]['metadata']['component'], 'kg')
        self.assertEqual([r['response']['message']['content'] for r in rows if r['event'] == 'response'],
                         ['bad', '{"ok":true}'])
        self.assertEqual(len([r for r in rows if r['event'] == 'cache_hit']), 1)

    def test_concurrent_calls_remain_complete_json_lines(self):
        from concurrent.futures import ThreadPoolExecutor
        def one(i):
            return logged_model_call(lambda *_: {'message': {'content': str(i)}},
                'http://localhost/api/chat', {'messages': [{'role': 'user', 'content': str(i)}]}, 1)
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(one, range(12)))
        rows = self.records()
        self.assertEqual(len(rows), 24)
        self.assertEqual({r['call_id'] for r in rows if r['event'] == 'request'},
                         {r['call_id'] for r in rows if r['event'] == 'response'})

    def test_main_json_input_prints_full_report_and_creates_log(self):
        root = Path(__file__).resolve().parents[4]
        entry = runpy.run_path(str(root / 'code/main.py'))
        namespace = entry['main'].__globals__
        namespace['LLM_LOG_DIRECTORY'] = self.root / 'entry_logs'
        patient = self.root / 'patient.json'
        patient.write_text('{"患者ID":"synthetic","病史":"记忆下降"}', encoding='utf-8')
        namespace['PATIENT_FILE'] = patient
        report = self.root / 'report.md'
        report.write_text('# 完整报告\n病史与证据、分析结果、未决问题。', encoding='utf-8')
        result = SimpleNamespace(run_id='code_run', status='completed', stop_reason='finished',
                                 report_paths={'markdown': str(report)})
        captured = io.StringIO()
        with patch('sys.argv', ['main.py', '--patient', str(patient), '--log-dir', str(self.root / 'entry_logs')]), patch('neurogra.clinical.workflow.analyze_patient', return_value=result) as analyze, \
             contextlib.redirect_stdout(captured):
            self.assertIs(entry['main'](), result)
        self.assertEqual(analyze.call_args.args[0], {'患者ID': 'synthetic', '病史': '记忆下降'})
        self.assertIn(report.read_text(encoding='utf-8'), captured.getvalue())
        logs = list((self.root / 'entry_logs').glob('*.jsonl'))
        self.assertEqual(len(logs), 1)
        self.assertEqual(json.loads(logs[0].read_text(encoding='utf-8').splitlines()[-1])['event'], 'session_complete')

    def test_full_json_workflow_preserves_report_and_logs_on_invalid_model_output(self):
        from neurogra.clinical.config import AgentConfig, KnowledgeAccess
        root = Path(__file__).resolve().parents[4]
        entry = runpy.run_path(str(root / 'code/main.py'))
        namespace = entry['main'].__globals__
        namespace['LLM_LOG_DIRECTORY'] = self.root / 'workflow_logs'
        patient = self.root / 'patient.json'
        patient.write_text('{"患者ID":"synthetic","病史":"记忆下降"}', encoding='utf-8')
        namespace['PATIENT_FILE'] = patient
        config = ClinicalConfig(project_root=self.root, text_knowledge_config=None,
            main_agent=KnowledgeAccess(use_kb=False, use_kg=False),
            agents={'history': AgentConfig(use_kb=False, use_kg=False)})
        namespace['build_config'] = lambda *args, **kwargs: config
        console = io.StringIO()
        with patch('sys.argv', ['main.py', '--patient', str(patient), '--log-dir', str(self.root / 'workflow_logs')]), \
             patch('neurogra.clinical.llm.http_json', return_value={'message': {'content': 'invalid raw model output'}}), \
             contextlib.redirect_stdout(console):
            result = entry['main']()
        self.assertTrue(Path(result.report_paths['markdown']).is_file())
        self.assertIn('记忆下降', console.getvalue())
        self.assertIn('最终分析报告', console.getvalue())
        rows = [json.loads(line) for line in next((self.root / 'workflow_logs').glob('*.jsonl')).read_text(encoding='utf-8').splitlines()]
        requests = [r for r in rows if r['event'] == 'request']
        responses = [r for r in rows if r['event'] == 'response']
        self.assertGreater(len(requests), 0)
        self.assertEqual(len(requests), len(responses))
        self.assertTrue(all(r['response']['message']['content'] == 'invalid raw model output' for r in responses))
        self.assertEqual(rows[-1]['run_id'], result.run_id)


if __name__ == '__main__':
    unittest.main()
