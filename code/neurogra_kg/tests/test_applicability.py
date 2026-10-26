import unittest
from datetime import datetime, timezone

from neurogra_kg.contracts.domain import AtomCondition, NotCondition, AllCondition, PatientFact, SourceRef
from neurogra_kg.retrieval.applicability import aggregate, evaluate_condition


REF = SourceRef(block_id="b", start=0, end=1, quote="x")
TIME = datetime(2026, 10, 10, tzinfo=timezone.utc)


class ApplicabilityTests(unittest.TestCase):
    def atom(self, **overrides):
        return AtomCondition.model_validate({"condition_id": "c", "field": "x", "operator": "gt",
                                           "value": 10, "source_refs": [REF], **overrides})

    def fact(self, **overrides):
        return PatientFact.model_validate({"fact_id": "f", "field": "x", "value": 12,
                                         "source_ref": REF, **overrides})

    def test_unknown_is_not_negated_to_true(self):
        state, _ = evaluate_condition(NotCondition(child=self.atom()), [], TIME)
        self.assertEqual(state, "UNKNOWN")

    def test_method_mismatch_stays_unknown(self):
        state, _ = evaluate_condition(self.atom(method="M"), [self.fact(method="N")], TIME)
        self.assertEqual(state, "UNKNOWN")

    def test_unit_conversion_and_incompatibility(self):
        state, _ = evaluate_condition(self.atom(value=10, unit="mg/l"), [self.fact(value=1.2, unit="mg/dl")], TIME)
        self.assertEqual(state, "SATISFIED")
        state, _ = evaluate_condition(self.atom(unit="mg/l"), [self.fact(unit="mmol/l")], TIME)
        self.assertEqual(state, "UNKNOWN")

    def test_conflicting_records_remain_unknown(self):
        state, details = evaluate_condition(self.atom(), [self.fact(), self.fact(fact_id="f2", value=9)], TIME)
        self.assertEqual(state, "UNKNOWN")
        self.assertEqual(details[0].conflicts, ["x"])

    def test_future_record_cannot_be_used(self):
        state, _ = evaluate_condition(self.atom(), [self.fact(observed_at="2026-10-11T00:00:00Z")], TIME)
        self.assertEqual(state, "UNKNOWN")

    def test_all_and_any_three_values(self):
        self.assertEqual(aggregate("ALL", ["UNKNOWN", "VIOLATED"]), "VIOLATED")
        self.assertEqual(aggregate("ANY", ["UNKNOWN", "SATISFIED"]), "SATISFIED")
        self.assertEqual(aggregate("ANY", ["UNKNOWN", "VIOLATED"]), "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
