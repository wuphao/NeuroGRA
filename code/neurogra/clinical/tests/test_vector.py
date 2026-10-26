"""Published KG vector protocol and clinical evidence fusion boundaries."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from neurogra_kg.storage.indexes import LocalVectorStore
from neurogra.clinical.retrieval import fuse_evidence
from neurogra.clinical.schemas import EvidenceItem
from neurogra.clinical.evaluation import retrieval_metrics


class FakeEmbedding:
    identity = "digest1"
    config = SimpleNamespace(model="fixture", dimension=2, query_template_version="q1", document_template_version="d1")
    def model_digest(self): return self.identity
    def embed(self, texts, budget=None):
        return [[1., 0.] if "alpha" in t else [0., 1.] for t in texts]


class VectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.client = FakeEmbedding()
        self.resources = [{"id": "a", "text": "alpha"}, {"id": "b", "text": "beta"}]
        LocalVectorStore.build(self.root, self.resources, self.client, "release1")

    def test_published_index_ranks(self):
        index = LocalVectorStore(self.root, self.client)
        self.assertEqual(index.query("alpha", 1)[0][0], "a")
        self.assertEqual(index.manifest["release_id"], "release1")

    def test_model_and_template_binding_changes_rejected(self):
        index = LocalVectorStore(self.root, self.client)
        self.client.identity = "changed"
        with self.assertRaises(ValueError): index.query("alpha", 1)
        self.client.identity = "digest1"
        self.client.config = SimpleNamespace(model="fixture", dimension=2, query_template_version="q2", document_template_version="d1")
        with self.assertRaises(ValueError): index.query("alpha", 1)

    def test_mutated_index_rejected(self):
        path = self.root / "vectors.npy"
        path.write_bytes(path.read_bytes() + b"changed")
        with self.assertRaises(ValueError): LocalVectorStore(self.root, self.client)

    def test_distinct_conditions_in_same_source_are_not_merged(self):
        def item(statement, state):
            return EvidenceItem(evidence_id=statement, document_id="d", span_id="s", statement_id=statement,
                quote="source", version="r", matched_by=[], applicability=state)
        applicable = item("a", "applicable")
        violated = item("b", "not_applicable")
        result = fuse_evidence({"bm25": [violated, applicable], "vector": [applicable]}, 5)
        self.assertEqual([r.statement_id for r in result], ["a", "b"])
        self.assertEqual(result[0].matched_by, ["bm25", "vector"])

    def test_rrf_deduplicates_within_backend_and_merges_sources(self):
        def item(sid):
            return EvidenceItem(evidence_id=sid, document_id="d", span_id=sid, quote="source", version="r", matched_by=[])
        a, b = item("a"), item("b")
        result = fuse_evidence({"bm25": [a, a, b], "vector": [b, a]}, 2)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].backend_ranks, {"bm25": 1, "vector": 2})
        self.assertAlmostEqual(result[0].fusion_score, 1/61 + 1/62)

    def test_hit_and_recall_are_distinct(self):
        self.assertEqual(retrieval_metrics(["a", "z"], ["a", "b"], 2), {"hit_at_k": 1., "recall_at_k": .5})
        with self.assertRaises(ValueError): retrieval_metrics([], [], 5)
