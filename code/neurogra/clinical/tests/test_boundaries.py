"""Focused service boundary tests; graph service is isolated with a fake driver."""
import asyncio
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from neurogra.clinical.config import ClinicalConfig
from neurogra.clinical.schemas import *
from neurogra.clinical.storage import RunStore, BudgetExceeded
from neurogra.clinical.retrieval import KnowledgeService
from neurogra.clinical.intake import ingest_patient
from neurogra.clinical.parsing import parse_attachment, inspect_image
from neurogra.clinical.profiling import profile_case
from neurogra.clinical.orchestration import Executor, fallback_plan
from neurogra.clinical.llm import ModelGateway


class BoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = ClinicalConfig(project_root=self.root, patient_data_root=Path("."))
        self.store = RunStore(self.root / "runs.sqlite")
        self.run = self.store.create_run({}, {"llm": 10, "retrieval": 5, "image": 0, "seconds": 30})
        # These tests isolate source hydration with fake repositories, not process transport.
        backend = patch.object(KnowledgeService, '_run_backend', KnowledgeService._backend)
        backend.start()
        self.addCleanup(backend.stop)

    def test_scoped_model_repair_budget(self):
        def invalid(*args):
            return {"message": {"content": "invalid"}}
        gateway = ModelGateway(self.config.model, self.store, self.run, transport=invalid,
                               scope="task", scope_limit=1)
        with self.assertRaises(BudgetExceeded):
            gateway.generate_structured("x", {}, Contract)
        self.assertEqual(sum(c["n"] for c in self.store.summary(self.run)["calls"]), 1)

    def test_attachment_declared_time_retained(self):
        (self.root / "r.txt").write_text("量表总分20", encoding="utf-8")
        intake = ingest_patient({"患者ID": "x", "附件": {"时间": "2026-08", "路径": "r.txt"}}, self.config)
        parsed = parse_attachment(intake.attachments[0], self.config)
        self.assertEqual(parsed.segments[0].context["时间"], "2026-08")
        snapshot = profile_case(intake, [parsed], self.config)
        self.assertEqual(snapshot.observations[0].time.precision, "month")

    def test_graph_backend_passes_pinned_release_and_deadline(self):
        with patch("neurogra.clinical.retrieval.retrieve_backend", return_value=("empty", [], {})) as retrieve:
            service = KnowledgeService(self.config, "release1", self.store, self.run)
            result = service.search_knowledge(RetrievalRequest(request_id="q", question="x", requested_backends=["graph"]))
        self.assertEqual(result.backend_status, {"graph": "empty"})
        args = retrieve.call_args.args
        self.assertEqual(args[1], "release1")
        self.assertEqual(args[3], "graph")
        self.assertGreater(args[4], 0)
        self.assertLessEqual(args[4], 30)

    def test_retrieval_degrades_and_deduplicates(self):
        item = EvidenceItem(evidence_id="e", document_id="d", span_id="s", quote="source", version="r", matched_by=[])
        def backend(config, release, request, name, remaining, snapshot):
            return ("ok", [item, item], {}) if name == "bm25" else ("not_configured", [], {})
        with patch("neurogra.clinical.retrieval.retrieve_backend", side_effect=backend):
            result = KnowledgeService(self.config, "r", self.store, self.run).search_knowledge(
                RetrievalRequest(request_id="q", question="x", requested_backends=["bm25", "vector"]))
        self.assertEqual(result.status, "degraded")
        self.assertEqual(len(result.items), 1)
        self.assertEqual(result.items[0].matched_by, ["bm25"])

    def test_release_mismatch_rejected_before_query(self):
        service = KnowledgeService(self.config, "r1", self.store, self.run)
        with self.assertRaises(ValueError):
            service.search_knowledge(RetrievalRequest(request_id="q", question="x", knowledge_release_id="r2"))

    def test_vector_failure_does_not_block_bm25(self):
        item = EvidenceItem(evidence_id="e", document_id="d", span_id="s", quote="source", version="r", matched_by=[])
        for error in [TimeoutError("offline"), ValueError("embedding_model_binding_mismatch")]:
            def backend(config, release, request, name, remaining, snapshot):
                if name == "vector": raise error
                return "ok", [item], {}
            with patch("neurogra.clinical.retrieval.retrieve_backend", side_effect=backend):
                result = KnowledgeService(self.config, "r", self.store, self.run).search_knowledge(
                    RetrievalRequest(request_id=str(type(error)), question="x", requested_backends=["bm25", "vector"]))
            self.assertEqual(result.backend_status, {"bm25": "ok", "vector": "failed"})
            self.assertEqual(result.items[0].quote, "source")

    def test_unreadable_attachment_stays_in_inventory(self):
        intake = ingest_patient({"患者ID":"x","认知评估":{"路径":"missing.pdf"}}, self.config)
        snapshot = profile_case(intake, [parse_attachment(intake.attachments[0], self.config)], self.config)
        item = next(i for i in snapshot.inventory if i.domain == "cognition")
        self.assertEqual(item.availability, "unreadable")

    def test_different_lab_names_not_conflicts(self):
        raw={"患者ID":"x","检验":[{"名称":"A","时间":"2026-09-12","数值":1},
                                {"名称":"B","时间":"2026-09-12","数值":2}]}
        self.assertEqual(profile_case(ingest_patient(raw,self.config),[],self.config).conflict_groups,[])

    def test_metadata_dicom_series_and_conflicting_modality(self):
        try:
            from pydicom.dataset import FileDataset, FileMetaDataset
            from pydicom.uid import ExplicitVRLittleEndian, generate_uid
        except ImportError:
            self.skipTest("pydicom optional")
        path = self.root / "synthetic.dcm"
        meta=FileMetaDataset()
        meta.TransferSyntaxUID=ExplicitVRLittleEndian
        meta.MediaStorageSOPClassUID=generate_uid()
        meta.MediaStorageSOPInstanceUID=generate_uid()
        dataset=FileDataset(str(path),{},file_meta=meta,preamble=b"\0"*128)
        dataset.SeriesInstanceUID=generate_uid()
        dataset.Modality="CT"
        dataset.StudyDate="20260912"
        dataset.save_as(path, enforce_file_format=True)
        asset=ImageAsset(asset_id="image1",modality="MRI",path=str(path),status="available")
        inspected=inspect_image(asset,self.config)
        self.assertTrue(inspected.metadata["modality_conflict"])
        self.assertFalse(inspected.metadata["classification_executed"])
        self.assertEqual(len(inspected.metadata["series_candidates"]),1)

    def test_handler_timeout(self):
        snapshot=profile_case(ingest_patient({"患者ID":"x","病史":"高血压"},self.config),[],self.config)
        plan=fallback_plan(snapshot)
        plan.tasks[0].budget.timeout_seconds=0.01
        class Slow:
            async def analyze(self,*args):
                await asyncio.sleep(1)
        executor=Executor(self.store,self.run)
        executor.register("history",Slow())
        result=asyncio.run(executor.execute_tasks(plan,snapshot))
        self.assertEqual(result[0].status,"failed")

    def test_numeric_fact_remains_observation(self):
        from neurogra.clinical.profiling import Candidate, CandidateBatch
        intake=ingest_patient({"患者ID":"x","年龄":68},self.config)
        ref=intake.records[-1].record_id
        class Model:
            def generate_structured(self,*args):
                return CandidateBatch(candidates=[Candidate(record_id=ref,quote="68",domains=["background"],
                                                            name="年龄",status="present")])
        snapshot=profile_case(intake,[],self.config,Model())
        self.assertEqual(snapshot.observations[0].status,"observed")


    def test_incomplete_model_extraction_keeps_remaining_source(self):
        from neurogra.clinical.profiling import Candidate, CandidateBatch
        intake=ingest_patient({"患者ID":"x","家属描述":"记忆下降；吃药需要提醒"},self.config)
        ref=intake.records[-1].record_id
        class Model:
            def generate_structured(self,*args):
                return CandidateBatch(candidates=[Candidate(record_id=ref,quote="记忆下降",
                                                            domains=["history"],name="记忆下降")])
        snapshot=profile_case(intake,[],self.config,Model())
        self.assertTrue(any("吃药需要提醒" in o.quote for o in snapshot.observations))
        self.assertTrue(any(i.code=="extraction_incomplete" for i in snapshot.issues))

    def test_no_backend_selection_is_invalid(self):
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            RetrievalRequest(request_id="q",question="x",requested_backends=[])

    def test_handler_cannot_fabricate_evidence_reference(self):
        snapshot=profile_case(ingest_patient({"患者ID":"x","病史":"高血压"},self.config),[],self.config)
        class Fabricating:
            async def analyze(self,task,context):
                return AgentResult(result_id="r",task_id=task.task_id,case_version=1,status="completed",
                    claims=[Claim(claim_id="c",text="解释",kind="interpretation",level="etiology",
                        observation_ids=task.input_refs,evidence_ids=["made_up"],strength="tentative")])
        executor=Executor(self.store,self.run)
        executor.register("history",Fabricating())
        results=asyncio.run(executor.execute_tasks(fallback_plan(snapshot),snapshot))
        self.assertEqual(results[0].status,"failed")

    def test_only_background_does_not_start_agents(self):
        snapshot=profile_case(ingest_patient({"患者ID":"x","年龄":68,"性别":"女"},self.config),[],self.config)
        self.assertEqual(fallback_plan(snapshot).tasks,[])


    def test_preparation_snapshots_are_run_scoped(self):
        from neurogra.clinical.service import prepare_patient
        raw={"患者ID":"x"}
        first=prepare_patient(raw,self.config,use_model=False)
        second=prepare_patient(raw,self.config,use_model=False)
        self.assertNotEqual(first.run_id,second.run_id)
        base=self.config.resolve(self.config.data_root)/"cases"/first.snapshot.raw_input_hash
        self.assertTrue((base/first.run_id/"snapshot.json").is_file())
        self.assertTrue((base/second.run_id/"snapshot.json").is_file())


if __name__ == "__main__":
    unittest.main()
