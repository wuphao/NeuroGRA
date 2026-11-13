import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch

from pydantic import BaseModel

from neurogra.clinical.config import ClinicalConfig, ModelConfig
from neurogra.clinical.generation import (
    KEY_FILE,
    GenerationClient,
    GenerationError,
    patient_endpoint_allowed,
)
from neurogra.clinical.llm import ModelFailure, ModelGateway
from neurogra.clinical.storage import RunStore
from neurogra.platform.settings import model_settings_view, save_settings
from neurogra_common.model_admission import model_admission


class ExampleAnswer(BaseModel):
    answer: str


class ModelProviderTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.keys = self.root / KEY_FILE
        self.keys.write_text('OPENAI_API_KEY=synthetic-openai\nOPENROUTER_API_KEY=synthetic-router\n'
                             'DEEPSEEK_API_KEY=synthetic-deepseek\n', encoding='utf-8')

    def test_cloud_endpoints_headers_and_parameters(self):
        for provider, url, key, token_field in (
            ('openai', 'https://api.openai.com/v1', 'synthetic-openai', 'max_completion_tokens'),
            ('openrouter', 'https://openrouter.ai/api/v1', 'synthetic-router', 'max_completion_tokens'),
            ('deepseek', 'https://api.deepseek.com', 'synthetic-deepseek', 'max_tokens'),
        ):
            with self.subTest(provider=provider):
                transport = Mock(return_value={'choices': [{'message': {'content': 'OK'}}]})
                config = ModelConfig(provider=provider, model='sample-model', base_url='https://ignored.example')
                client = GenerationClient(config, self.root, patient_data=False, secure_transport=transport)
                self.assertEqual(client.complete([{'role': 'user', 'content': 'test'}], 2)['content'], 'OK')
                args, kwargs = transport.call_args
                self.assertEqual(args[0], url + '/chat/completions')
                self.assertEqual(kwargs['headers'], {'Authorization': f'Bearer {key}'})
                self.assertIn(token_field, args[1])
                self.assertNotIn('options', args[1])
                if provider == 'deepseek':
                    self.assertEqual(args[1]['thinking'], {'type': 'disabled'})

    def test_remote_patient_data_is_denied_without_local_flag(self):
        for config in (ModelConfig(provider='openai'), ModelConfig(base_url='http://192.0.2.10:11434'),
                       ModelConfig(model='sample:latest-cloud')):
            with self.assertRaisesRegex(GenerationError, '外发开关'):
                GenerationClient(config, self.root)
        self.assertTrue(GenerationClient(ModelConfig(), self.root).native)

    def test_retrieval_endpoints_obey_same_outbound_policy(self):
        self.assertTrue(patient_endpoint_allowed(self.root, 'http://127.0.0.1:11434', 'local'))
        self.assertFalse(patient_endpoint_allowed(self.root, 'http://192.0.2.10:11434', 'local'))
        self.assertFalse(patient_endpoint_allowed(self.root, 'http://localhost:11434', 'model:cloud'))
        self.assertFalse(patient_endpoint_allowed(self.root, 'bolt://remote.example:7687'))
        with self.keys.open('a', encoding='utf-8') as file:
            file.write('LLM_ALLOW_EXTERNAL_PATIENT_DATA=true\n')
        self.assertTrue(patient_endpoint_allowed(self.root, 'http://192.0.2.10:11434', 'local'))

    def test_key_is_absent_from_settings_and_saved_model(self):
        config = ClinicalConfig(project_root=self.root)
        saved = save_settings(self.root, config, {'model': {'provider': 'openai', 'model': 'sample-model'}})
        view = model_settings_view(saved)
        self.assertEqual(view['model']['provider'], 'openai')
        self.assertTrue(next(item for item in view['providers'] if item['id'] == 'openai')['key_configured'])
        self.assertNotIn('synthetic-openai', str(view))
        self.assertNotIn('synthetic-openai', (self.root / 'data/platform/settings.json').read_text())

    def test_invalid_endpoint_is_rejected_before_settings_write(self):
        config = ClinicalConfig(project_root=self.root)
        with self.assertRaises(GenerationError):
            save_settings(self.root, config, {'model': {'base_url': 'http://localhost:11434?invalid=1'}})
        self.assertFalse((self.root / 'data/platform/settings.json').exists())

    def test_cloud_structured_generation_uses_provider_protocol(self):
        with self.keys.open('a', encoding='utf-8') as file:
            file.write('LLM_ALLOW_EXTERNAL_PATIENT_DATA=true\n')
        config = ClinicalConfig(project_root=self.root, model=ModelConfig(provider='deepseek', model='sample-model'))
        store = RunStore(self.root / 'runs.sqlite')
        run = store.create_run(config.model_dump(mode='json'), config.budget.model_dump())
        response = {'choices': [{'message': {'content': '{"answer":"checked"}'}}],
                    'usage': {'prompt_tokens': 23, 'completion_tokens': 4}}
        with patch.object(GenerationClient, 'request', return_value=response) as request:
            answer = ModelGateway(config.model, store, run).generate_structured('system', {'input': 'fixture'}, ExampleAnswer)
        self.assertEqual(answer.answer, 'checked')
        payload = request.call_args.args[1]
        self.assertEqual(payload['response_format'], {'type': 'json_object'})
        self.assertIn('matching this schema', payload['messages'][0]['content'])
        self.assertNotIn('synthetic-deepseek', str(payload))

    def test_outbound_permission_is_rechecked_after_waiting_for_admission(self):
        with self.keys.open('a', encoding='utf-8') as file:
            file.write('LLM_ALLOW_EXTERNAL_PATIENT_DATA=true\n')
        model = ModelConfig(base_url='http://192.0.2.10:11434')
        config = ClinicalConfig(project_root=self.root, model=model)
        store = RunStore(self.root / 'runs.sqlite')
        run = store.create_run(config.model_dump(mode='json'), config.budget.model_dump())
        transport = Mock(return_value={'message': {'content': '{"answer":"unexpected"}'}})

        @contextmanager
        def revoke_while_waiting(_scope):
            self.keys.write_text('LLM_ALLOW_EXTERNAL_PATIENT_DATA=false\n', encoding='utf-8')
            yield

        with model_admission(revoke_while_waiting), self.assertRaises(ModelFailure):
            ModelGateway(model, store, run, transport=transport).generate_structured('system', {}, ExampleAnswer)
        transport.assert_not_called()
        self.assertEqual(store.summary(run)['calls'], [])
