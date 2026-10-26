import unittest

from neurogra_kg.construction.logic import assemble_logic
from neurogra_kg.construction.verification import apply_verification, REQUIRED_FIELDS
from neurogra_kg.contracts.domain import Block, Entity, KnowledgeStatement, SourceRef
from neurogra_kg.contracts.tasks import ExtractedCondition, FieldCheck, LogicOutput, QuoteRef, VerificationOutput


class LogicVerificationTests(unittest.TestCase):
    def setup_condition(self):
        block = Block(block_id="b", document_id="d", page=1, order=0, kind="footnote", text="必须明确排除因素 E")
        quote = {"block_id": "b", "quote": block.text, "occurrence": 0}
        condition = ExtractedCondition(local_id="c", condition_text=block.text, category="exclusion",
                                       scope_hint="local", source_refs=[quote])
        atom = {"node_id": "a", "kind": "ATOM", "condition_id": "c", "field": "exclusion_e",
                "operator": "eq", "value": False, "unit": None, "method": None, "time_scope": None,
                "population": None, "source_refs": [quote]}
        return block, condition, atom

    def test_double_negated_exclusion_rejected(self):
        block, condition, atom = self.setup_condition()
        output = LogicOutput(statement_id="s", root_id="not", nodes=[atom,
            {"node_id": "not", "kind": "NOT", "child_ids": ["a"]}], atom_mappings={}, unresolved_items=[], logic_evidence=[])
        with self.assertRaisesRegex(ValueError, "double_negation"):
            assemble_logic(output, "s", [condition], {"b": block})

    def test_direct_exclusion_absence_accepted(self):
        block, condition, atom = self.setup_condition()
        output = LogicOutput(statement_id="s", root_id="a", nodes=[atom], atom_mappings={}, unresolved_items=[], logic_evidence=[])
        tree = assemble_logic(output, "s", [condition], {"b": block})
        self.assertIs(tree.value, False)

    def test_verification_cannot_publish_unsupported_logic_as_applicable(self):
        block, condition, atom = self.setup_condition()
        tree = assemble_logic(LogicOutput(statement_id="s", root_id="a", nodes=[atom], atom_mappings={},
                              unresolved_items=[], logic_evidence=[]), "s", [condition], {"b": block})
        statement = KnowledgeStatement(statement_id="s", subject=Entity(entity_id="h", name="X", entity_type="other"),
            predicate="supports", object=Entity(entity_id="t", name="D", entity_type="other"),
            assertion_text="X supports D", condition=tree, condition_status="constrained",
            source_refs=[SourceRef(block_id="b", start=0, end=len(block.text), quote=block.text)])
        quote = QuoteRef(block_id="b", quote=block.text, occurrence=0)
        checks = [FieldCheck(field=field, state="unsupported" if field == "logic" else "supported",
                             evidence_refs=[quote], reason="check") for field in sorted(REQUIRED_FIELDS)]
        verified = apply_verification(statement, VerificationOutput(verdict="partial", checks=checks, issues=[]), {"b": block})
        self.assertEqual(verified.condition_status, "unresolved")
        self.assertIsNone(verified.condition)

    def test_population_qualifier_cannot_disappear_inside_an_age_quote(self):
        text = "年龄不小于18的示例人群"
        block = Block(block_id="b", document_id="d", page=1, order=0, kind="paragraph", text=text)
        quote = {"block_id": "b", "quote": text, "occurrence": 0}
        condition = ExtractedCondition(local_id="c", condition_text=text, category="population", scope_hint="local", source_refs=[quote])
        atom = {"node_id": "a", "kind": "ATOM", "condition_id": "c", "field": "age", "operator": "gte",
            "value": 18, "unit": "year", "method": None, "population": None, "time_scope": None, "source_refs": [quote]}
        output = LogicOutput(statement_id="s", root_id="a", nodes=[atom], atom_mappings={}, unresolved_items=[], logic_evidence=[])
        with self.assertRaisesRegex(ValueError, "population_qualifier_omitted"):
            assemble_logic(output, "s", [condition], {"b": block})
        atom["population"] = "示例人群"
        output = LogicOutput(statement_id="s", root_id="a", nodes=[atom], atom_mappings={}, unresolved_items=[], logic_evidence=[])
        self.assertEqual(assemble_logic(output, "s", [condition], {"b": block}).population, "示例人群")

    def test_threshold_must_bind_its_selected_method(self):
        text = "方法M下，指标X大于10"
        block = Block(block_id="b", document_id="d", page=1, order=0, kind="paragraph", text=text)
        quote = {"block_id": "b", "quote": text, "occurrence": 0}
        conditions = [ExtractedCondition(local_id=id, condition_text=part, category=category, scope_hint="local", source_refs=[quote])
            for id, part, category in [("m", "方法M", "method"), ("v", "指标X大于10", "threshold")]]
        atom = {"node_id": "a", "kind": "ATOM", "condition_id": "v", "field": "indicator_x", "operator": "gt",
            "value": 10, "unit": None, "method": None, "population": None, "time_scope": None, "source_refs": [quote]}
        nodes = [atom, {"node_id": "m", "kind": "TEXT", "condition_id": "m", "text": "方法M", "source_refs": [quote]},
            {"node_id": "root", "kind": "ALL", "child_ids": ["a", "m"]}]
        output = LogicOutput(statement_id="s", root_id="root", nodes=nodes, atom_mappings={}, unresolved_items=[], logic_evidence=[])
        with self.assertRaisesRegex(ValueError, "method_binding_omitted"):
            assemble_logic(output, "s", conditions, {"b": block})


if __name__ == "__main__":
    unittest.main()
