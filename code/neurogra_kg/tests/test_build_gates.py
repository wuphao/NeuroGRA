import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from neurogra_kg.api import KnowledgeGraphService
from neurogra_kg.config import KGConfig
from neurogra_kg.contracts.domain import DocumentInput, BuildRequest, SourceRef
from neurogra_kg.tests import test_release_integrity as fixtures


class FixtureLLM:
    def binding(self): return {"model": "fixture"}


class BuildGateTests(unittest.TestCase):
    def test_unparsed_scan_page_blocks_publication_even_when_other_units_succeed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "source.json"
            path.write_text(json.dumps({"blocks": [{"block_id": "body", "kind": "paragraph", "page": 1,
                "text": "X supports D."}], "issues": ["page_2:ocr_required_unavailable"]}))
            config = KGConfig(root=root); config.graph.provider = "memory"
            with KnowledgeGraphService(config, llm=FixtureLLM(), embedding=fixtures.FixtureEmbedding()) as kg:
                parsed = kg.parser.parse(DocumentInput(path=path))
                statement, _ = fixtures.ReleaseIntegrityTests().data()
                statement.source_refs = [SourceRef(block_id=parsed.blocks[0].block_id, start=0, end=13, quote="X supports D.")]
                with patch("neurogra_kg.api.ConstructionPipeline") as constructor:
                    pipeline = constructor.return_value
                    pipeline.run.return_value = ([statement], parsed.issues)
                    pipeline.completed_units = 1; pipeline.failed_units = 0
                    result = kg.build(BuildRequest(documents=[DocumentInput(path=path)], corpus_version="fixture"))
                self.assertEqual(result.status, "partial")
                self.assertEqual(result.failed_units, 1)
                with self.assertRaisesRegex(ValueError, "build_not_completed"):
                    kg.publish(result.build_id)
