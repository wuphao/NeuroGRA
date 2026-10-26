import json
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from neurogra_kg.adapters.ollama_embedding import OllamaEmbeddingAdapter
from neurogra_kg.adapters.ollama_llm import OllamaLLMAdapter
from neurogra_kg.config import EmbeddingConfig, KGConfig, LLMConfig
from neurogra_kg.contracts.domain import AtomCondition, KnowledgeStatement, SourceRef
from neurogra_kg.contracts.tasks import ProbeOutput, TASK_SCHEMAS
from neurogra_kg.runtime import Budget


class FoundationTests(unittest.TestCase):
    def test_unknown_config_field_rejected(self):
        with self.assertRaises(ValidationError):
            KGConfig.model_validate({"cloud_fallback": True})

    def test_existence_cannot_accept_string(self):
        with self.assertRaises(ValidationError):
            AtomCondition(condition_id="c", field="symptom", operator="exists", value="false",
                          source_refs=[SourceRef(block_id="b", start=0, end=1, quote="x")])

    def test_all_prompt_schemas_exist(self):
        root = Path(__file__).resolve().parents[1] / "prompts"
        for task, schema in TASK_SCHEMAS.items():
            self.assertTrue((root / (task + ".system.txt")).is_file())
            self.assertEqual(json.loads((root / (task + ".schema.json")).read_text(encoding="utf-8")),
                             schema.model_json_schema())

    def test_embedding_malformed_shape_not_cached(self):
        with tempfile.TemporaryDirectory() as tmp:
            def transport(url, payload, timeout):
                if url.endswith("/api/tags"):
                    return {"models": [{"name": "fixture", "digest": "v1"}]}
                return {"embeddings": [[1.0, 0.0]]}
            adapter = OllamaEmbeddingAdapter(EmbeddingConfig(model="fixture"), Path(tmp), transport)
            with self.assertRaisesRegex(ValueError, "row_count"):
                adapter.embed(["a", "b"])
            self.assertFalse(list(Path(tmp).rglob("*.json")))

    def test_generation_model_change_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tags = iter(["v1", "v2"])
            def transport(url, payload, timeout):
                if url.endswith("/api/tags"):
                    return {"models": [{"name": "fixture", "digest": next(tags)}]}
                return {"message": {"content": '{"ok":true,"source_refs":[]}'}}
            adapter = OllamaLLMAdapter(LLMConfig(model="fixture"), Path(tmp), transport=transport)
            with self.assertRaisesRegex(ValueError, "model_changed"):
                adapter.execute("probe", {}, ProbeOutput)
            self.assertFalse(list((Path(tmp) / "llm_cache").glob("*.json")))

    def test_budget_bounds_calls(self):
        budget = Budget(max_seconds=10, max_llm_calls=1)
        budget.reserve("llm")
        with self.assertRaisesRegex(RuntimeError, "call_budget"):
            budget.reserve("llm")

    def test_semantically_rejected_output_is_quarantined_and_not_replayed(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls = []
            def transport(url, payload, timeout):
                if url.endswith("/api/tags"):
                    return {"models": [{"name": "fixture", "digest": "v1"}]}
                calls.append(payload)
                return {"message": {"content": '{"ok":true,"source_refs":[]}'}}
            adapter = OllamaLLMAdapter(LLMConfig(model="fixture"), Path(tmp), transport=transport)
            adapter.execute("probe", {}, ProbeOutput)
            adapter.execute("probe", {}, ProbeOutput)
            self.assertEqual(len(calls), 1)
            adapter.invalidate_last("source_quote_invalid")
            self.assertFalse(list((Path(tmp) / "llm_cache").glob("*.json")))
            rejected = next((Path(tmp) / "llm_rejected").glob("*.json"))
            self.assertEqual(json.loads(rejected.read_text())["status"], "semantic_rejected")
            adapter.execute("probe", {}, ProbeOutput)
            self.assertEqual(len(calls), 2)
            self.assertTrue(any('source_quote_invalid' in m['content'] for m in calls[-1]['messages']))

    def test_input_limit_increase_reuses_only_identical_inference_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls=[]
            def transport(url,payload,timeout):
                if url.endswith('/api/tags'):return {'models':[{'name':'fixture','digest':'v1'}]}
                calls.append(payload)
                return {'message':{'content':'{"ok":true,"source_refs":[]}'}}
            old=OllamaLLMAdapter(LLMConfig(model='fixture',max_input_chars=14000),Path(tmp),transport=transport)
            old.execute('probe',{},ProbeOutput)
            new=OllamaLLMAdapter(LLMConfig(model='fixture',max_input_chars=28000),Path(tmp),transport=transport)
            new.execute('probe',{},ProbeOutput)
            self.assertEqual(len(calls),1)
            changed=OllamaLLMAdapter(LLMConfig(model='fixture',max_input_chars=28000,temperature=.1),Path(tmp),transport=transport)
            changed.execute('probe',{},ProbeOutput)
            self.assertEqual(len(calls),2)


if __name__ == "__main__":
    unittest.main()
