"""Regression cases from the repository-wide review; no external services."""
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from neurogra.clinical.config import ClinicalConfig
from neurogra.clinical.intake import ingest_patient
from neurogra.clinical.llm import ModelFailure, ModelGateway
from neurogra.clinical.parsing import parse_attachment
from neurogra.clinical.schemas import Contract
from neurogra.clinical.storage import RunStore
from neurogra.clinical.utils import write_json


class Response(Contract):
    value: str


class RepositoryReviewTests(unittest.TestCase):
    def test_concurrent_atomic_writes_have_independent_staging_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "input.json"
            barrier = threading.Barrier(2)
            local = threading.local()
            replace = Path.replace

            def synchronized_replace(path, destination):
                if not getattr(local, 'started', False):
                    local.started = True
                    barrier.wait(timeout=5)
                return replace(path, destination)

            with patch.object(Path, "replace", synchronized_replace), ThreadPoolExecutor(2) as pool:
                futures = [pool.submit(write_json, target, {"value": value}) for value in (1, 2)]
                for future in futures:
                    future.result(timeout=10)
            self.assertIn(json.loads(target.read_text(encoding="utf-8"))["value"], (1, 2))
            self.assertEqual(list(Path(tmp).glob("*.tmp")), [])

    def test_hash_read_failure_becomes_parse_issue(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "report.txt").write_text("facts", encoding="utf-8")
            cfg = ClinicalConfig(project_root=root, patient_data_root=Path("."))
            attachment = ingest_patient({"患者ID": "p", "附件路径": "report.txt"}, cfg).attachments[0]
            with patch("neurogra.clinical.parsing.file_hash", side_effect=PermissionError):
                parsed = parse_attachment(attachment, cfg)
            self.assertEqual(parsed.status, "failed")
            self.assertEqual(parsed.issues[0].code, "parse_failed")

    def test_non_object_model_responses_are_settled_and_repaired(self):
        for invalid in (None, [], "invalid"):
            with self.subTest(response=invalid), tempfile.TemporaryDirectory() as tmp:
                store = RunStore(Path(tmp) / "runs.sqlite")
                cfg = ClinicalConfig(project_root=Path(tmp))
                run = store.create_run({}, {"seconds": 30, "llm": 2, "retrieval": 0, "image": 0})
                gateway = ModelGateway(cfg.model, store, run, transport=lambda *args: invalid)
                with self.assertRaises(ModelFailure):
                    gateway.generate_structured("test", {}, Response)
                self.assertEqual(store.summary(run)["calls"],
                                 [{"kind": "llm", "status": "invalid_output", "n": 2}])
