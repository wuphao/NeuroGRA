import json
import tempfile
import unittest
from pathlib import Path

from neurogra_kb import KBConfig, TextKnowledgeService, load_config
from neurogra_kg.config import EmbeddingConfig


class Embedding:
    def __init__(self):
        self.config = EmbeddingConfig(dimension=2)
        self.digest = "test_digest"

    def model_digest(self):
        return self.digest

    def embed(self, texts, budget=None):
        return [[1., 0.] if "alpha" in text else [0., 1.] for text in texts]


class PublicationTests(unittest.TestCase):
    def test_config_file_controls_root_instead_of_package_install_directory(self):
        directory = self.root / 'configs'
        directory.mkdir()
        path = directory / 'kb.yaml'
        path.write_text('data_root: data/kb\noutput_root: output/kb\n', encoding='utf-8')
        loaded = load_config(path)
        self.assertEqual(loaded.root, self.root)
        self.assertEqual(loaded.resolve(loaded.output_root), self.root / 'output/kb')
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.doc = self.root / "source.json"
        self.doc.write_text(json.dumps({"blocks": [
            {"kind": "paragraph", "page": 1, "text": "alpha 原文没有图谱声明关联。"},
            {"kind": "paragraph", "page": 1, "text": "beta 另一段原始资料。"}]}), encoding="utf-8")
        self.embedding = Embedding()
        self.kb = TextKnowledgeService(KBConfig(root=self.root, embedding=self.embedding.config),
                                      self.embedding)

    def test_raw_only_hybrid_publication_without_graph(self):
        manifest = self.kb.publish([self.doc])
        result = self.kb.search_text("alpha", release_id=manifest["release_id"], top_k=1)
        self.assertEqual(result["backend_status"], {"lexical": "ok", "vector": "ok"})
        self.assertIn("alpha", result["items"][0]["text"])
        self.assertNotIn("statement_id", result["items"][0])
        self.assertEqual(result["items"][0]["allowed_use"], "background")
        expanded = self.kb.expand_source(result["items"][0]["id"], release_id=manifest["release_id"])
        self.assertEqual(len(expanded["context"]), 2)
        self.assertEqual(self.kb.publish([self.doc])["release_id"], manifest["release_id"])

    def test_pinned_release_does_not_follow_active_publication(self):
        old = self.kb.publish([self.doc], vector=False)["release_id"]
        self.doc.write_text(json.dumps({"blocks": [
            {"kind": "paragraph", "page": 1, "text": "gamma 新版原文。"}]}), encoding="utf-8")
        new = self.kb.publish([self.doc], vector=False)["release_id"]
        self.assertNotEqual(old, new)
        self.assertEqual(self.kb.active_release(), new)
        self.assertTrue(self.kb.search_text("alpha", release_id=old)["items"])
        self.assertFalse(self.kb.search_text("alpha", release_id=new)["items"])

    def test_artifact_mutation_and_cross_release_references_rejected(self):
        release_id = self.kb.publish([self.doc], vector=False)["release_id"]
        with self.assertRaises(ValueError):
            self.kb.expand_source("fake", release_id=release_id)
        with self.assertRaises(ValueError):
            self.kb.search_text("alpha", release_id=release_id, document_ids=["other_doc"])
        path = self.kb._directory(release_id) / "resources.json"
        path.write_text("[]", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "checksum"):
            self.kb.get_release(release_id)

    def test_embedding_binding_mismatch_explicitly_degrades(self):
        release_id = self.kb.publish([self.doc])["release_id"]
        self.embedding.digest = "changed_digest"
        result = self.kb.search_text("alpha", release_id=release_id)
        self.assertTrue(result["backend_status"]["vector"].startswith("unavailable:"))
        self.assertIn("alpha", result["items"][0]["text"])
