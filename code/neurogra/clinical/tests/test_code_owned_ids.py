import unittest
from pydantic import ValidationError

from neurogra.clinical.history import HistoryDraft, GroundedDraft, number_claims
from neurogra.clinical.schemas import ClaimContent
from neurogra_common.schema import grammar_schema


class ClinicalIdentityTests(unittest.TestCase):
    def test_decoding_grammar_keeps_identity_and_extra_field_contract(self):
        original = HistoryDraft.model_json_schema()
        transport = grammar_schema(original)
        self.assertFalse(transport['additionalProperties'])
        self.assertNotIn('claim_id', transport['$defs']['ClaimContent']['properties'])
        self.assertIn('maxItems', original['$defs']['RequestContent']['properties']['source_refs'])
        self.assertNotIn('maxItems', transport['$defs']['RequestContent']['properties']['source_refs'])
        self.assertEqual(grammar_schema({'properties': {'maxItems': {'type': 'string'}}})['properties'],
                         {'maxItems': {'type': 'string'}})
    def content(self):
        return dict(text="未提供记录", kind="description", level="history",
                    observation_ids=["obs_allowed"], strength="descriptive")

    def test_all_specialist_drafts_reject_new_claim_id(self):
        for schema in (HistoryDraft, GroundedDraft):
            payload = {"claims": [{**self.content(), "claim_id": "model_invented"}],
                       "uncertainties": []}
            with self.subTest(schema=schema.__name__), self.assertRaises(ValidationError):
                schema.model_validate(payload)

    def test_program_numbers_and_keeps_unchanged_revision_identity(self):
        row = ClaimContent.model_validate(self.content())
        first = number_claims([row], "task1")
        self.assertEqual(first, number_claims([row], "task1"))
        self.assertNotEqual(first[0].claim_id, number_claims([row], "task2")[0].claim_id)
        self.assertEqual(first[0].claim_id,
                         number_claims([row], ["task1", 2], first)[0].claim_id)
        changed = row.model_copy(update={"text": "原文修订"})
        self.assertNotEqual(first[0].claim_id,
                            number_claims([changed], ["task1", 2], first)[0].claim_id)
