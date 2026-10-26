import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import patch

from neurogra_kg.config import KGConfig
from neurogra_kg.contracts.domain import AtomCondition, Block, PatientFact, RetrievalRequest, RetrievalResult, SourceRef
from neurogra_kg.runtime import Budget
from neurogra_kg.retrieval.gaps import identify_gaps
from neurogra_kg.retrieval.ranking import rank_evidence
from neurogra_kg.retrieval.supplementation import supplement
from neurogra_kg.tests import test_release_integrity as fixtures


class NoLLM:
    def execute(self, *args): raise AssertionError("patient/structural gaps must not need L6")


class SupplementationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = KGConfig(root=self.root)
        self.original, parsed = fixtures.ReleaseIntegrityTests().data()
        self.original.condition_status = "unresolved"
        self.original.status = "source_checked"
        note = Block(block_id="note", document_id="d", page=1, order=1, kind="footnote", text="Age >= 18")
        self.blocks = {"b": parsed.blocks[0], "note": note}
        self.corpus = {"documents": [parsed.document.model_dump(mode="json")], "edges": [
            {"source": "b", "target": "note", "relation": "NOTE_OF", "certainty": "explicit", "reason": "fixture"}]}
        self.condition = AtomCondition(condition_id="age", field="age", operator="gte", value=18, unit="year",
            source_refs=[SourceRef(block_id="note", start=0, end=9, quote=note.text)])
        self.refined = self.original.model_copy(update={"statement_id": "refined", "status": "approved",
            "condition_status": "constrained", "condition": self.condition}, deep=True)
        self.request = RetrievalRequest(question="X supports D", purpose="diagnostic_assessment",
            reference_time=datetime(2026, 1, 1, tzinfo=timezone.utc), max_supplement_rounds=2)
        self.llm = NoLLM()

    def result(self, statement=None):
        statement = statement or self.original
        groups = rank_evidence([statement], {statement.statement_id: 1.}, {statement.statement_id: ["lexical"]},
            self.request, "release", self.llm, Budget(10))
        gaps, requests = identify_gaps(groups, self.request.question)
        return RetrievalResult(run_id="run", release_id="release", applicable=groups["SATISFIED"],
            unknown=groups["UNKNOWN"], excluded=groups["VIOLATED"], gaps=gaps, data_requests=requests)

    def test_source_refinement_keeps_identity_and_does_not_mutate_original(self):
        result = self.result()
        with patch("neurogra_kg.retrieval.supplementation.ConstructionPipeline.run", return_value=([self.refined], [])):
            result = supplement(result, self.request, self.config, self.corpus, self.blocks, [],
                                self.llm, self.root, Budget(10))
        self.assertEqual(result.unknown[0].statement.statement_id, self.original.statement_id)
        self.assertTrue(result.unknown[0].temporary)
        self.assertEqual(self.original.condition_status, "unresolved")
        self.assertTrue(result.data_requests)
        self.assertTrue(any(g.kind == "knowledge_condition" and g.status == "resolved" for g in result.gaps))

    def test_patient_data_gap_never_uses_literature_to_fill_patient(self):
        result = self.result(self.refined)
        with patch("neurogra_kg.retrieval.supplementation.ConstructionPipeline.run", side_effect=AssertionError("no extraction")):
            result = supplement(result, self.request, self.config, self.corpus, self.blocks, [], self.llm, self.root, Budget(10))
        self.assertEqual(result.supplementation_trace, [])
        self.assertEqual(result.unknown[0].state, "UNKNOWN")
        self.assertEqual(self.request.facts, [])

    def test_verified_conditions_resolve_using_actual_patient_fact(self):
        self.request.facts = [PatientFact(fact_id="age", field="age", value=70, unit="year",
            source_ref=SourceRef(block_id="patient", start=0, end=2, quote="70"))]
        with patch("neurogra_kg.retrieval.supplementation.ConstructionPipeline.run", return_value=([self.refined], [])):
            result = supplement(self.result(), self.request, self.config, self.corpus, self.blocks, [],
                                self.llm, self.root, Budget(10))
        self.assertEqual(result.applicable[0].state, "SATISFIED")
        self.assertTrue(result.applicable[0].temporary)
        self.assertFalse(result.data_requests)

    def test_gap_is_not_resolved_when_its_evidence_falls_out_of_top_k(self):
        self.request.top_k = 1
        other = self.refined.model_copy(update={"assertion_text": "X supports D"}, deep=True)
        result = self.result()
        result.unknown[0].score_components["relevance"] = .01
        old_gap = next(g.gap_id for g in result.gaps if g.kind == "knowledge_condition")
        with patch("neurogra_kg.retrieval.supplementation.ConstructionPipeline.run", return_value=([other], [])):
            result = supplement(result, self.request, self.config, self.corpus, self.blocks, [],
                                self.llm, self.root, Budget(10))
        self.assertEqual(result.unknown[0].statement.statement_id, "refined")
        self.assertNotEqual(next(g.status for g in result.gaps if g.gap_id == old_gap), "resolved")

    def test_outside_corpus_plan_is_rejected_and_does_not_extract(self):
        from neurogra_kg.contracts.tasks import PlanningOutput, SearchQuery
        class ScopedLLM:
            def execute(self, task, payload, schema, budget):
                return PlanningOutput(gap_id=payload["gap"]["gap_id"], stop_reason=None,
                    queries=[SearchQuery(text="X", target_terms=["X"], document_scope=["outside"], expected_evidence="condition")])
        corpus = {**self.corpus, "edges": []}
        with patch("neurogra_kg.retrieval.supplementation.ConstructionPipeline.run", side_effect=AssertionError("must not extract")):
            result = supplement(self.result(), self.request, self.config, corpus, self.blocks, [],
                                ScopedLLM(), self.root, Budget(10))
        self.assertTrue(result.degraded)
        self.assertEqual(result.supplementation_trace[-1]["error_type"], "ValueError")
