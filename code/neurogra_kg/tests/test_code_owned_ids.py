import hashlib
import json
import unittest

from pydantic import ValidationError

from neurogra_common import stable_id, validate_references
from neurogra.clinical.utils import identity as clinical_id
from neurogra_kg.contracts.tasks import ExtractionDraft, FactOutput, LogicDraft
from neurogra_kg.construction.extraction import extract
from neurogra_kg.construction.logic import number_logic, assemble_logic
from neurogra_kg.contracts.domain import Block


class CodeOwnedIdsTests(unittest.TestCase):
    def test_legacy_ids_are_preserved(self):
        value = {"病例": [1, True]}
        for encoding, separators in [("compact", (",", ":")),
                                     ("legacy_clinical", (", ", ": "))]:
            expected = "case_" + hashlib.sha256(json.dumps(value, ensure_ascii=False,
                sort_keys=True, separators=separators).encode()).hexdigest()[:24]
            self.assertEqual(stable_id("case", value, encoding=encoding), expected)
            if encoding == "legacy_clinical":
                self.assertEqual(clinical_id("case", value), expected)

    def test_model_cannot_create_candidate_ids(self):
        condition = {"condition_text": "年龄不小于18", "category": "population",
                     "scope_hint": "local", "source_refs": [self.quote()]}
        with self.assertRaises(ValidationError):
            ExtractionDraft.model_validate({"statements": [], "conditions": [
                {**condition, "local_id": "invented"}], "issues": []})
        class Model:
            def execute(self, task, payload, schema, budget):
                return schema.model_validate({"statements": [], "conditions": [condition], "issues": []})
        block = self.block()
        first = extract(Model(), "unit", [block], [], None)
        again = extract(Model(), "unit", [block], [], None)
        other = extract(Model(), "other_unit", [block], [], None)
        self.assertEqual(first.conditions[0].local_id, again.conditions[0].local_id)
        self.assertNotEqual(first.conditions[0].local_id, other.conditions[0].local_id)

    def quote(self):
        return {"block_id": "b", "quote": "年龄不小于18", "occurrence": 0}

    def block(self):
        return Block(block_id="b", document_id="d", page=1, order=0,
                     kind="paragraph", text="年龄不小于18")

    def test_nested_logic_gets_code_ids_and_validates_reference(self):
        from neurogra_kg.contracts.tasks import ExtractedCondition
        node = {"kind": "ATOM", "condition_id": "c", "field": "age", "operator": "gte",
                "value": 18, "unit": "year", "method": None, "time_scope": None,
                "population": None, "source_refs": [self.quote()]}
        data = {"statement_id": "s", "root": node, "unresolved_items": [], "logic_evidence": []}
        with self.assertRaises(ValidationError):
            LogicDraft.model_validate({**data, "root": {**node, "node_id": "invented"}})
        numbered = number_logic(LogicDraft.model_validate(data))
        self.assertTrue(numbered.root_id.startswith("logic_node_"))
        self.assertEqual(numbered.model_dump(), number_logic(LogicDraft.model_validate(data)).model_dump())
        condition = ExtractedCondition(local_id="c", condition_text="年龄不小于18",
            category="population", scope_hint="local", source_refs=[self.quote()])
        tree = assemble_logic(numbered, "s", [condition], {"b": self.block()})
        self.assertEqual(tree.condition_id, "c")
        numbered.nodes[0].condition_id = "invented"
        with self.assertRaisesRegex(ValueError, "invalid_condition_leaf_reference"):
            assemble_logic(numbered, "s", [condition], {"b": self.block()})

    def test_facts_cannot_create_ids_and_references_are_scoped(self):
        row = {"field": "age", "value": 18, "unit": "year", "polarity": "affirmed",
               "observed_at": None, "method": None, "source_refs": [self.quote()]}
        FactOutput.model_validate({"facts": [row], "issues": []})
        with self.assertRaises(ValidationError):
            FactOutput.model_validate({"facts": [{**row, "local_id": "fake"}], "issues": []})
        with self.assertRaises(ValueError):
            validate_references(["other_patient_observation"], ["this_patient_observation"])


if __name__ == "__main__":
    unittest.main()
