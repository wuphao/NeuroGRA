import json
import tempfile
import unittest
from pathlib import Path

from neurogra_kg.config import ConstructionConfig
from neurogra_kg.construction.pipeline import ConstructionPipeline
from neurogra_kg.contracts.domain import Block, Document, ParsedDocument
from neurogra_kg.contracts.tasks import ExtractionOutput
from neurogra_kg.runtime import Budget


class FixtureLLM:
    def __init__(self, fail=False):
        self.fail, self.calls = fail, []
    def binding(self):
        return {"model": "fixture-v1"}
    def execute(self, task, payload, schema, budget):
        self.calls.append(payload["unit_id"])
        if self.fail and payload["unit_id"] == "b":
            raise TimeoutError("fixture_interruption")
        return ExtractionOutput(statements=[], conditions=[], issues=[])


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.parsed = ParsedDocument(document=Document(document_id="d", checksum="x",
            source_path="x", snapshot_path="x", title="x", version="1"), parser="fixture", parser_version="1",
            blocks=[Block(block_id=id, document_id="d", page=1, order=i, kind="paragraph", text=id)
                    for i, id in enumerate(["a", "b"])])

    def test_interruption_preserves_completed_units_and_resume_skips_them(self):
        first = ConstructionPipeline(ConstructionConfig(), FixtureLLM(fail=True))
        events = []
        with self.assertRaises(TimeoutError):
            first.run(self.parsed, self.directory, Budget(10), events.append)
        self.assertEqual(first.completed_units, 1)
        self.assertEqual(events[-1]["event"], "started")
        second = ConstructionPipeline(ConstructionConfig(), FixtureLLM())
        events = []
        second.run(self.parsed, self.directory, Budget(10), events.append)
        self.assertEqual(second.llm.calls, ["b"])
        self.assertEqual(second.completed_units, 2)
        self.assertTrue(any(e["event"] == "restored" for e in events))

    def test_changed_input_or_mutated_checkpoint_is_rejected(self):
        pipeline = ConstructionPipeline(ConstructionConfig(), FixtureLLM())
        pipeline.run(self.parsed, self.directory, Budget(10))
        self.parsed.blocks[0].text = "different"
        with self.assertRaisesRegex(ValueError, "input_changed"):
            pipeline.run(self.parsed, self.directory, Budget(10))
        self.parsed.blocks[0].text = "a"
        path = self.directory / "a.json"
        payload = json.loads(path.read_text()); payload["issues"] = ["modified"]
        path.write_text(json.dumps(payload))
        with self.assertRaisesRegex(ValueError, "checksum_mismatch"):
            pipeline.run(self.parsed, self.directory, Budget(10))
