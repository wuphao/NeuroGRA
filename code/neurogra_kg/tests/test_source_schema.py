import unittest

from neurogra_kg.adapters.ollama_llm import source_bound_schema
from neurogra_kg.contracts.tasks import ExtractionOutput


class SourceSchemaTests(unittest.TestCase):
    def test_source_ids_are_constrained_without_mutating_contract_schema(self):
        payload = {"blocks": [{"block_id": "b2"}, {"block_id": "b1"}],
                   "nested": {"source_refs": [{"block_id": "b1", "quote": "x"}]}}
        bound = source_bound_schema(ExtractionOutput, payload)
        self.assertEqual(bound["$defs"]["QuoteRef"]["properties"]["block_id"]["enum"], ["b1", "b2"])
        self.assertNotIn("enum", ExtractionOutput.model_json_schema()["$defs"]["QuoteRef"]["properties"]["block_id"])
        self.assertNotIn("enum", source_bound_schema(ExtractionOutput, {})["$defs"]["QuoteRef"]["properties"]["block_id"])
