import tempfile
import unittest
from pathlib import Path

import numpy as np

from neurogra_kg.completion.complex import ComplExModel, complex_score, score_gradients
from neurogra_kg.config import CompletionConfig
from neurogra_kg.contracts.domain import Entity, KnowledgeStatement, SourceRef
from neurogra_kg.completion.candidates import predict_candidates
from neurogra_kg.completion.verification import verify_candidate
from neurogra_kg.retrieval.gaps import identify_gaps
from neurogra_kg.contracts.domain import Block
from neurogra_kg.utils import mentions_term


class ComplExTests(unittest.TestCase):
    def test_multi_relation_candidates_require_source_verified_full_statements(self):
        entities = [Entity(entity_id=name, name=name, entity_type=kind) for name, kind in
            [("Marker X", "biomarker"), ("Marker Y", "biomarker"), ("State D", "disease"), ("State E", "disease")]]
        block = Block(block_id="b", document_id="d", page=1, order=0, kind="paragraph", text="synthetic")
        def statement(id, h, r, t):
            return KnowledgeStatement(statement_id=id, subject=entities[h], predicate=r, object=entities[t],
                assertion_text=block.text, status="approved", condition_status="explicit_unrestricted",
                source_refs=[SourceRef(block_id="b", start=0, end=len(block.text), quote=block.text)])
        known = [statement("s1", 0, "supports", 2), statement("s2", 1, "supports", 3),
                 statement("s3", 0, "associated_with", 3)]
        model = ComplExModel.train(known, "release", CompletionConfig(dimension=8, epochs=20))
        proposals = predict_candidates(model, known, "Marker X supports State E?", 20)
        candidate = next(p for p in proposals if (p["h"], p["r"], p["t"]) == ("Marker X", "supports", "State E"))
        self.assertEqual(candidate["status"], "unverified")
        self.assertEqual(verify_candidate(candidate, known, {"b": block}), [])
        supported = statement("heldout", 0, "supports", 3)
        self.assertEqual(verify_candidate(candidate, [supported], {"b": block}), [supported])
        supported.status = "source_checked"
        self.assertEqual(verify_candidate(candidate, [supported], {"b": block}), [])

    def test_connection_gap_is_detected_even_when_other_evidence_exists(self):
        from neurogra_kg.tests.test_release_integrity import ReleaseIntegrityTests
        first, _ = ReleaseIntegrityTests().data()
        second = first.model_copy(deep=True, update={"statement_id": "s2"})
        second.subject = Entity(entity_id="y", name="Y", entity_type="other")
        second.object = Entity(entity_id="e", name="E", entity_type="other")
        from types import SimpleNamespace
        groups = {"SATISFIED": [SimpleNamespace(statement=first)], "UNKNOWN": [], "VIOLATED": []}
        gaps, _ = identify_gaps(groups, "X 与 E 的关系", [first, second])
        self.assertTrue(any(g.missing_items == ["E", "X"] for g in gaps))
        self.assertFalse(mentions_term("lead", "AD"))
        self.assertFalse(mentions_term("test", ""))
        self.assertTrue(mentions_term("AD患者", "AD"))
    def test_score_gradients_match_finite_differences(self):
        rng = np.random.default_rng(7)
        values = [rng.normal(size=(2, 3)) for _ in range(3)]
        gradients = score_gradients(*values)
        for arg in range(3):
            for i in range(2):
                for j in range(3):
                    plus, minus = [value.copy() for value in values], [value.copy() for value in values]
                    plus[arg][i, j] += 1e-6
                    minus[arg][i, j] -= 1e-6
                    numeric = (complex_score(*plus) - complex_score(*minus)) / 2e-6
                    self.assertAlmostEqual(gradients[arg][i, j], numeric, places=6)

    def test_training_binding_and_roundtrip(self):
        entities = [Entity(entity_id=str(i), name=str(i), entity_type="other") for i in range(3)]
        statements = [KnowledgeStatement(statement_id=str(i), subject=entities[i], predicate="associated_with",
            object=entities[i + 1], assertion_text="synthetic", condition_status="explicit_unrestricted",
            status="approved", source_refs=[SourceRef(block_id="b", start=0, end=1, quote="x")]) for i in range(2)]
        model = ComplExModel.train(statements, "release", CompletionConfig(dimension=8, epochs=100, learning_rate=.1))
        self.assertLess(model.manifest["loss_last"], model.manifest["loss_first"])
        with tempfile.TemporaryDirectory() as tmp:
            model.save(Path(tmp))
            loaded = ComplExModel.load(Path(tmp), "release")
            self.assertEqual(model.score("0", "associated_with", "1"), loaded.score("0", "associated_with", "1"))
            with self.assertRaisesRegex(ValueError, "release_binding"):
                ComplExModel.load(Path(tmp), "another-release")


if __name__ == "__main__":
    unittest.main()
