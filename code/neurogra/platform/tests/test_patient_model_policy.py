import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from neurogra_kg.config import EmbeddingConfig, GraphConfig, KGConfig

from neurogra.clinical.config import ClinicalConfig
from neurogra.clinical.knowledge_adapter import retrieve_backend
from neurogra.clinical.schemas import RetrievalRequest


class PatientModelPolicyTests(unittest.TestCase):
    def test_remote_retrieval_rechecks_policy_between_calls(self):
        for backend in ('vector', 'graph'):
            with self.subTest(backend=backend), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                policy = root / '.env.llm.local'
                policy.write_text('LLM_ALLOW_EXTERNAL_PATIENT_DATA=true\n', encoding='utf-8')
                kg = KGConfig(root=root, graph=GraphConfig(uri='bolt://remote.example:7687'),
                              embedding=EmbeddingConfig(base_url='http://remote.example:11434'))
                config = ClinicalConfig(project_root=root, kg=kg, vector_enabled=True)
                request = RetrievalRequest(request_id='synthetic', question='合成病例问题')
                result = SimpleNamespace(applicable=[], unknown=[], excluded=[],
                    backend_status={backend: 'empty'}, release_id='release', data_requests=[], gaps=[], warnings=[])
                with patch('neurogra.clinical.knowledge_adapter.service') as service:
                    service.return_value.__enter__.return_value.retrieve.return_value = result
                    status, _, _ = retrieve_backend(config, 'release', request, backend, 10)
                    self.assertEqual(status, 'empty')
                    policy.write_text('LLM_ALLOW_EXTERNAL_PATIENT_DATA=false\n', encoding='utf-8')
                    status, items, _ = retrieve_backend(config, 'release', request, backend, 10)
                    self.assertEqual(status, 'blocked_patient_data_outbound')
                    self.assertEqual(items, [])
                    service.assert_called_once()

    def test_remote_graph_and_embedding_do_not_receive_patient_query_without_opt_in(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            kg = KGConfig(root=root, graph=GraphConfig(uri='bolt://remote.example:7687'),
                          embedding=EmbeddingConfig(base_url='http://remote.example:11434'))
            config = ClinicalConfig(project_root=root, kg=kg, vector_enabled=True)
            request = RetrievalRequest(request_id='synthetic', question='合成病例问题')
            with patch('neurogra.clinical.knowledge_adapter.service') as service:
                for backend in ('vector', 'graph'):
                    status, items, _ = retrieve_backend(config, 'release', request, backend, 10)
                    self.assertEqual(status, 'blocked_patient_data_outbound')
                    self.assertEqual(items, [])
                service.assert_not_called()


if __name__ == '__main__':
    unittest.main()
