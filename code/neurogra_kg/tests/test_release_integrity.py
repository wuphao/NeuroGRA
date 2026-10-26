import tempfile
import unittest
from pathlib import Path

from neurogra_kg.adapters.graph import MemoryGraphStore
from neurogra_kg.config import EmbeddingConfig
from neurogra_kg.contracts.domain import Block, Document, Entity, KnowledgeStatement, ParsedDocument, SourceRef
from neurogra_kg.storage.registry import Registry
from neurogra_kg.storage.releases import create_release, load_release
from neurogra_kg.runtime import Budget


class FixtureEmbedding:
    config = EmbeddingConfig(model="fixture", dimension=2)
    def model_digest(self):
        return "fixture-version"
    def embed(self, texts, budget=None):
        return [[1., 0.] for text in texts]


class ReleaseIntegrityTests(unittest.TestCase):
    def data(self):
        block = Block(block_id="b", document_id="d", page=1, order=0, kind="paragraph", text="X supports D.")
        parsed = ParsedDocument(document=Document(document_id="d", checksum="digest", source_path="fixture",
            snapshot_path="fixture", title="fixture", version="1"), blocks=[block], parser="fixture", parser_version="1")
        statement = KnowledgeStatement(statement_id="s", subject=Entity(entity_id="h", name="X", entity_type="other"),
            predicate="supports", object=Entity(entity_id="t", name="D", entity_type="other"),
            assertion_text=block.text, condition_status="explicit_unrestricted", status="approved",
            purposes=["diagnostic_assessment"], source_refs=[SourceRef(block_id="b", start=0, end=len(block.text), quote=block.text)])
        return statement, parsed

    def test_tampering_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = Registry(root / "registry.sqlite")
            try:
                statement, parsed = self.data()
                manifest = create_release(root, registry, "build", [statement], [parsed], FixtureEmbedding(), MemoryGraphStore(), Budget(10))
                load_release(manifest)
                (Path(manifest["directory"]) / "corpus.json").write_text("{}", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "checksum_mismatch"):
                    load_release(manifest)
            finally:
                registry.close()

    def test_graph_failure_never_sets_active_pointer(self):
        class FailedGraph:
            def publish(self, *args):
                raise ConnectionError("fixture")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = Registry(root / "registry.sqlite")
            try:
                statement, parsed = self.data()
                with self.assertRaises(ConnectionError):
                    create_release(root, registry, "build", [statement], [parsed], FixtureEmbedding(), FailedGraph(), Budget(10))
                with self.assertRaisesRegex(ValueError, "no_active"):
                    registry.active_release_id()
            finally:
                registry.close()

    def test_container_source_is_preserved_but_only_fragments_are_indexed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = Registry(root / "registry.sqlite")
            try:
                statement, parsed = self.data()
                original = parsed.blocks[0].model_copy(deep=True, update={
                    "block_id": "container", "text": "X supports D." * 1000,
                    "issues": ["container_only"]})
                parsed.blocks.append(original)
                parsed.blocks[0].fragment_of = "container"
                manifest = create_release(root, registry, "build", [statement], [parsed],
                    FixtureEmbedding(), MemoryGraphStore(), Budget(10))
                _, blocks, resources, _ = load_release(manifest)
                self.assertIn("container", blocks)
                self.assertNotIn("block:container", [row["id"] for row in resources])
                self.assertIn("block:b", [row["id"] for row in resources])
            finally:
                registry.close()


if __name__ == "__main__":
    unittest.main()
