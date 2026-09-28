import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from neurogra.knowledge.indexing.vector import *
from neurogra.clinical.retrieval import fuse_evidence
from neurogra.clinical.schemas import EvidenceItem
from neurogra.clinical.evaluation import retrieval_metrics


class FakeEmbedding:
    model = 'fixture:1'
    dimension = 2
    identity = 'digest1'
    def model_digest(self): return self.identity
    def embed(self, texts):
        return [normalize([1, 0] if 'alpha' in t else [0, 1], self.dimension) for t in texts]


def chunk(cid, text):
    return SimpleNamespace(chunk_id=cid, role='child', document_id='doc', span_ids=['s' + cid],
        retrieval_text=text, original_text=text, linked_clause_ids=[])


class VectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.chunks = [chunk('a', 'alpha'), chunk('b', 'beta')]
        self.client = FakeEmbedding()
        self.path, self.manifest = build_vector_index('r', self.chunks, self.root, self.client, max_chars=100)

    def retriever(self): return VectorRetriever(self.path, self.chunks, 'r', self.client, 100)

    def test_real_index_protocol_ranks_and_reuses_immutable_build(self):
        self.assertEqual(self.retriever().search('alpha', 1)[0][0], 'a')
        path, manifest = build_vector_index('r', self.chunks, self.root, self.client, max_chars=100)
        self.assertEqual(path, self.path)
        self.assertEqual(manifest.index_id, self.manifest.index_id)

    def test_mismatched_release_dimension_and_view_rejected(self):
        for release, dim, chars in [('other', 2, 100), ('r', 3, 100), ('r', 2, 101)]:
            with self.subTest(release=release, dim=dim, chars=chars):
                client = FakeEmbedding(); client.dimension = dim
                with self.assertRaises(ValueError):
                    VectorRetriever(self.path, self.chunks, release, client, chars)

    def test_mutated_corpus_and_index_bytes_rejected(self):
        altered = [chunk('a', 'changed'), self.chunks[1]]
        with self.assertRaises(ValueError): VectorRetriever(self.path, altered, 'r', self.client, 100)
        (self.path.parent / 'vectors.json').write_text('[]', encoding='utf-8')
        with self.assertRaises(ValueError): self.retriever()

    def test_model_digest_changed_rejected(self):
        self.client.identity = 'changed'
        with self.assertRaises(ValueError): self.retriever().search('alpha', 1)

    def test_nonfinite_zero_and_wrong_dimension_rejected(self):
        for vector in [[0, 0], [float('nan'), 1], [float('inf'), 1], [1], [True, 1]]:
            with self.subTest(vector=vector), self.assertRaises(ValueError): normalize(vector, 2)

    def test_embed_shape_validation(self):
        client = EmbeddingClient('http://fixture', 'fixture', 2, transport=lambda *args: {'embeddings': [[1, 0]]})
        with self.assertRaises(ValueError): client.embed(['a', 'b'])

    def test_expired_embedding_deadline(self):
        client = EmbeddingClient('http://fixture', 'fixture', 2, deadline=1,
            transport=lambda *args: self.fail('must not call'))
        with self.assertRaises(TimeoutError): client.embed(['a'])

    def test_rrf_deduplicates_within_backend_and_merges_sources(self):
        def item(sid):
            return EvidenceItem(evidence_id=sid, document_id='d', span_id=sid, quote='source', version='r', matched_by=[])
        a,b=item('a'),item('b')
        result=fuse_evidence({'bm25':[a,a,b], 'vector':[b,a]}, 2)
        self.assertEqual(len(result),2)
        self.assertEqual(result[0].backend_ranks, {'bm25':1, 'vector':2})
        self.assertEqual(result[0].matched_by, ['bm25','vector'])
        self.assertEqual(result[0].allowed_use,'background')
        self.assertAlmostEqual(result[0].fusion_score,1/61+1/62)

    def test_hit_and_recall_are_distinct(self):
        self.assertEqual(retrieval_metrics(['a','z'],['a','b'],2), {'hit_at_k':1.0,'recall_at_k':0.5})
        with self.assertRaises(ValueError): retrieval_metrics([],[],5)

    def test_membership_in_manifest_detects_injected_row(self):
        path=self.path.parent/'vectors.json'
        rows=json.loads(path.read_text()); rows[0]['chunk_id']='foreign'
        raw=canonical(rows).encode();path.write_bytes(raw)
        manifest=json.loads(self.path.read_text());manifest['vectors_sha256']=hashlib.sha256(raw).hexdigest()
        self.path.write_text(json.dumps(manifest),encoding='utf-8')
        with self.assertRaises(ValueError): self.retriever()


if __name__ == '__main__': unittest.main()
