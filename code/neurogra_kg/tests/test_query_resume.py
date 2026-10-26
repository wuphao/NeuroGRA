import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import patch

from neurogra_kg.api import KnowledgeGraphService
from neurogra_kg.config import KGConfig
from neurogra_kg.contracts.domain import RetrievalRequest
from neurogra_kg.runtime import Budget
from neurogra_kg.storage.query_journal import QueryJournal
from neurogra_kg.storage.releases import create_release
from neurogra_kg.tests import test_release_integrity as fixtures


class FixtureLLM:
    def binding(self): return {"model": "fixture"}


class QueryResumeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        config = KGConfig(root=Path(self.tmp.name))
        config.graph.provider = "memory"
        self.kg = KnowledgeGraphService(config, llm=FixtureLLM(), embedding=fixtures.FixtureEmbedding())
        self.addCleanup(self.kg.close)
        statement, parsed = fixtures.ReleaseIntegrityTests().data()
        self.release = create_release(self.kg.data_root, self.kg.registry, "build", [statement], [parsed],
            self.kg.embedding, self.kg.graph, Budget(10))
        self.request = RetrievalRequest(question="X", purpose="diagnostic_assessment", run_id="query-fixture",
            reference_time=datetime(2026, 1, 1, tzinfo=timezone.utc), max_seconds=60,
            max_supplement_rounds=0, backends=["lexical"], max_llm_calls=2)

    def test_completed_query_is_reusable_but_changed_request_is_rejected(self):
        result = self.kg.retrieve(self.request)
        self.assertEqual(self.kg.inspect_query(result.run_id)["status"], "completed")
        with patch("neurogra_kg.api.retrieve", side_effect=AssertionError("must reuse result")):
            self.assertEqual(self.kg.resume_query(result.run_id), result)
        changed = self.request.model_copy(update={"question": "different"})
        with self.assertRaisesRegex(ValueError, "input_or_runtime_changed"):
            self.kg.retrieve(changed)

    def test_interrupted_supplement_resumes_checkpoint_without_recalling(self):
        request = self.request.model_copy(update={"max_supplement_rounds": 1})
        def interrupted(result, *args, **kwargs):
            kwargs["on_checkpoint"](result, {"round_index": 0, "seen_queries": ["done"]})
            args[7].reserve("llm")
            raise KeyboardInterrupt()
        with patch("neurogra_kg.retrieval.supplementation.supplement", side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt): self.kg.retrieve(request)
        journal = self.kg.inspect_query(request.run_id)
        self.assertEqual(journal["status"], "interrupted")
        self.assertEqual(journal["llm_calls"], 1)
        def resumed(result, *args, **kwargs):
            self.assertEqual(kwargs["resume_state"]["seen_queries"], ["done"])
            self.assertEqual(args[7].max_llm_calls, 1)
            return result
        with patch("neurogra_kg.api.retrieve", side_effect=AssertionError("must not recall")), \
             patch("neurogra_kg.retrieval.supplementation.supplement", side_effect=resumed):
            self.kg.resume_query(request.run_id)
        self.assertEqual(self.kg.inspect_query(request.run_id)["status"], "completed")

    def test_checkpoint_tampering_and_active_lease_are_rejected(self):
        root = Path(self.tmp.name) / "journals"
        with QueryJournal(root, "run") as journal:
            journal.save(status="running")
            with self.assertRaisesRegex(RuntimeError, "already_active"):
                with QueryJournal(root, "run"): pass
        path = root / "run" / "checkpoint.json"
        path.write_text(path.read_text().replace("running", "completed"))
        with self.assertRaisesRegex(ValueError, "checksum_mismatch"):
            QueryJournal.read(path)
        with self.assertRaises(ValueError): QueryJournal(root, "../outside")

    def test_original_deadline_is_not_reset_after_interruption(self):
        def interrupted(*args, **kwargs): raise KeyboardInterrupt()
        with patch("neurogra_kg.api.retrieve", side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt): self.kg.retrieve(self.request)
        root = self.kg.output_root / "query_runs"
        with QueryJournal(root, self.request.run_id) as journal:
            journal.save(deadline=0)
        with patch("neurogra_kg.api.retrieve", side_effect=AssertionError("must not launch")):
            with self.assertRaises(TimeoutError): self.kg.resume_query(self.request.run_id)

    def test_offline_models_do_not_prevent_lexical_retrieval(self):
        with patch.object(self.kg.llm, "binding", side_effect=ConnectionError("offline")), \
             patch.object(self.kg.embedding, "model_digest", side_effect=ConnectionError("offline")):
            result = self.kg.retrieve(self.request)
            self.assertTrue(result.degraded)
            self.assertTrue(result.applicable)
            self.assertEqual(result.backend_status["lexical"], "ok")
            self.assertTrue(any(w.startswith("embedding_unavailable:") for w in result.warnings))
            self.assertEqual(self.kg.resume_query(result.run_id), result)

    def test_automatic_run_id_changes_with_runtime_and_can_resume(self):
        request = self.request.model_copy(update={"run_id": None})
        first = self.kg.retrieve(request)
        self.assertEqual(self.kg.resume_query(first.run_id), first)
        with patch.object(self.kg.llm, "binding", return_value={"model": "new-fixture"}):
            second = self.kg.retrieve(request)
        self.assertNotEqual(first.run_id, second.run_id)

    def test_model_binding_time_consumes_original_query_deadline(self):
        clock = [100.0]
        def slow_binding():
            clock[0] = 200.0
            return {"model": "fixture"}
        with patch('neurogra_kg.api.time.time', side_effect=lambda: clock[0]), \
             patch.object(self.kg.llm, 'binding', side_effect=slow_binding), \
             patch('neurogra_kg.api.retrieve', side_effect=AssertionError('must not recall after deadline')):
            with self.assertRaises(TimeoutError):
                self.kg.retrieve(self.request)
        self.assertEqual(self.kg.inspect_query(self.request.run_id)['deadline'], 160.0)
