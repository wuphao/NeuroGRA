import unittest

from neurogra.clinical.config import ClinicalConfig
from neurogra.clinical.intake import ingest_patient
from neurogra.clinical.orchestration import MainAgent, PlanProposal
from neurogra.clinical.profiling import profile_case


class PlanningCoverageTests(unittest.TestCase):
    def test_empty_model_plan_cannot_discard_available_patient_data(self):
        snapshot = profile_case(ingest_patient({'患者ID': 'synthetic', '病史': '记忆下降'}, ClinicalConfig()), [], ClinicalConfig())

        class EmptyPlanner:
            def generate_structured(self, *args):
                return PlanProposal(tasks=[])

        plan = MainAgent(EmptyPlanner()).plan(snapshot)
        self.assertTrue(plan.tasks)
        self.assertEqual(plan.mode, 'deterministic_fallback')
        self.assertTrue(any(i.code == 'invalid_model_plan' for i in plan.issues))
