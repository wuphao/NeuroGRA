import unittest

from neurogra_kg.construction.alignment import CandidatePair, align_candidate_pairs, select_bindings
from neurogra_kg.contracts.tasks import AlignmentDecision, AlignmentOutput, QuoteRef
from neurogra_kg.contracts.domain import Block


class AlignmentTests(unittest.TestCase):
    def test_batched_alignment_preserves_all_pairs_and_full_context(self):
        pairs = [CandidatePair("s" + str(i), "c", .9, .5) for i in range(19)]
        calls = []
        test = self
        class LLM:
            def execute(self, task, payload, schema, budget):
                calls.append(payload)
                return AlignmentOutput(decisions=[test.decision(p["statement_id"], p["condition_id"])
                    for p in payload["candidate_pairs"]])
        block = Block(block_id="b", document_id="d", kind="paragraph", page=1, order=1, text="x")
        output = align_candidate_pairs(LLM(), pairs, {"statements": ["full context"], "structure_edges": []},
                                       {"b": block}, None)
        self.assertEqual([len(p["candidate_pairs"]) for p in calls], [8, 8, 3])
        self.assertTrue(all(p["statements"] == ["full context"] for p in calls))
        selected, _ = select_bindings(pairs, output.decisions,
            {"structure": .5, "entity": .2, "semantic": .3}, .55)
        self.assertEqual(len(selected), 19)

    def test_batch_rejects_missing_or_duplicate_decisions(self):
        test = self
        class LLM:
            def execute(self, task, payload, schema, budget):
                return AlignmentOutput(decisions=[test.decision("s", "c"), test.decision("s", "c")])
        with self.assertRaisesRegex(ValueError, "alignment_decision_pair_mismatch"):
            align_candidate_pairs(LLM(), [CandidatePair("s", "c", 1, 1)], {}, {}, None)

    def decision(self, statement_id, condition_id, relation="shared"):
        return AlignmentDecision(statement_id=statement_id, condition_id=condition_id, relation=relation,
            scope="table", evidence_refs=[QuoteRef(block_id="b", quote="x", occurrence=0)], reason="scope")

    def test_shared_conditions_are_many_to_many(self):
        pairs = [CandidatePair("s1", "c", .9, .5), CandidatePair("s2", "c", .9, .5)]
        selected, _ = select_bindings(pairs, [self.decision("s1", "c"), self.decision("s2", "c")],
                                     {"structure": .5, "entity": .2, "semantic": .3}, .55)
        self.assertEqual(len(selected), 2)

    def test_unresolved_cannot_be_forced_by_reference(self):
        pair = CandidatePair("s", "c", 1, 1, fixed_reference=True)
        selected, _ = select_bindings([pair], [self.decision("s", "c", "unresolved")],
                                     {"structure": .5, "entity": .2, "semantic": .3}, .55)
        self.assertFalse(selected)

    def test_exclusive_group_maximizes_gain(self):
        pairs = [CandidatePair("s1", "c", .8, 0, exclusive_group="c"),
                 CandidatePair("s2", "c", 1, 1, exclusive_group="c")]
        selected, trace = select_bindings(pairs, [self.decision("s1", "c"), self.decision("s2", "c")],
                                         {"structure": .5, "entity": .2, "semantic": .3}, .55)
        self.assertEqual([pair.statement_id for pair in selected], ["s2"])
        self.assertFalse(trace[0]["selected"])


if __name__ == "__main__":
    unittest.main()
