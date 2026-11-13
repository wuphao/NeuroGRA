import asyncio
import tempfile
import unittest
from pathlib import Path

from neurogra.clinical.collaboration_contracts import (
    ClaimEvidenceLink,
    CollaborationClaim,
    CollaborationGraph,
    CollaborationTask,
    SubjectScope,
    TaskDependency,
    validate_task_scope,
)
from neurogra.clinical.config import ClinicalConfig
from neurogra.clinical.intake import ingest_patient
from neurogra.clinical.orchestration import Executor, fallback_plan, validate_plan
from neurogra.clinical.profiling import profile_case
from neurogra.clinical.schemas import AgentResult, Claim, TaskPlan
from neurogra.clinical.storage import RunStore
from neurogra.clinical.utils import identity


class CollaborationContractTests(unittest.TestCase):
    def task(self, name, *, role="cognition", dependencies=(), patient="11"):
        return CollaborationTask(
            task_id=name, kind="specialist.analyze", agent_type=role, objective="核对资料",
            patient_keys=[patient], depends_on=list(dependencies),
        )

    def test_graph_accepts_distinct_tasks_for_the_same_role_and_prior_version(self):
        first = self.task("cognition_first")
        second = self.task("cognition_second")
        review = CollaborationTask(task_id="review", kind="evidence.review", objective="核对依据",
            depends_on=[TaskDependency(task_id=first.task_id), TaskDependency(task_id=second.task_id)])
        graph = CollaborationGraph(tasks=[first, second, review])
        self.assertEqual([task.agent_type for task in graph.tasks[:2]], ["cognition", "cognition"])
        self.assertEqual(CollaborationGraph.model_validate_json(graph.model_dump_json()), graph)

    def test_graph_rejects_unknown_duplicate_and_cyclic_dependencies(self):
        with self.assertRaisesRegex(ValueError, "duplicate_task_version"):
            CollaborationGraph(tasks=[self.task("same"), self.task("same")])
        with self.assertRaisesRegex(ValueError, "cyclic_or_unknown_task_dependency"):
            CollaborationGraph(tasks=[self.task("a", dependencies=[TaskDependency(task_id="missing")])])
        with self.assertRaisesRegex(ValueError, "cyclic_or_unknown_task_dependency"):
            CollaborationGraph(tasks=[
                self.task("a", dependencies=[TaskDependency(task_id="b")]),
                self.task("b", dependencies=[TaskDependency(task_id="a")]),
            ])
        with self.assertRaises(ValueError):
            CollaborationTask(task_id="evil", kind="arbitrary_sql", objective="执行")

    def test_scope_is_explicit_and_does_not_authorize_extra_patients_or_tools(self):
        scope = SubjectScope(source_id="source", project_id="p", primary_patient="11",
                             comparison_patients=["12"])
        graph = CollaborationGraph(tasks=[
            self.task("outside", patient="13"),
            self.task("unknown_role", role="unregistered"),
            CollaborationTask(task_id="forms", kind="patient.read", objective="读取",
                              patient_keys=["11"], form_keys=["foreign"]),
            CollaborationTask(task_id="group", kind="cohort.aggregate", objective="统计"),
            CollaborationTask(task_id="compare", kind="comparison.run", objective="比较",
                              patient_keys=["11"]),
            CollaborationTask(task_id="report", kind="report.request", objective="报告",
                              patient_keys=["12"]),
        ])
        self.assertEqual(set(validate_task_scope(graph, scope, form_keys={"moca"}, agent_types={"cognition"})), {
            "patient_outside_scope", "unregistered_agent", "form_outside_scope", "cohort_scope_required",
            "comparison_requires_selected_patients", "report_requires_primary_patient",
        })
        with self.assertRaisesRegex(ValueError, "invalid_comparison_scope"):
            SubjectScope(source_id="source", project_id="p", primary_patient="11", comparison_patients=["11"])

    def test_claim_links_cannot_invent_source_or_treat_inapplicable_rule_as_support(self):
        claim = Claim(claim_id="c", text="有条件的解释", kind="interpretation", level="cognition",
                      observation_ids=["o"], evidence_ids=["e"], strength="conditional")
        with self.assertRaisesRegex(ValueError, "inapplicable_evidence_cannot_support_claim"):
            ClaimEvidenceLink(kind="knowledge", ref_id="e", relation="supports", applicability="violated")
        with self.assertRaisesRegex(ValueError, "claim_patient_link_not_referenced"):
            CollaborationClaim(task_id="t", patient_keys=["11"], claim=claim, links=[
                ClaimEvidenceLink(kind="patient", ref_id="invented", relation="supports", patient_key="11")])
        with self.assertRaisesRegex(ValueError, "claim_link_outside_patient_scope"):
            CollaborationClaim(task_id="t", patient_keys=["11"], claim=claim, links=[
                ClaimEvidenceLink(kind="patient", ref_id="o", relation="supports", patient_key="12")])


class RepeatedSpecialistExecutionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        config = ClinicalConfig(project_root=Path(self.directory.name))
        self.snapshot = profile_case(ingest_patient({"患者ID": "p", "MMSE总分": 23}, config), [], config, None)
        self.store = RunStore(Path(self.directory.name) / "clinical.sqlite")
        self.run_id = self.store.create_run({}, {"llm": 0, "retrieval": 0, "seconds": 30})

    def test_executor_keeps_two_tasks_for_same_specialist_separate(self):
        original = next(task for task in fallback_plan(self.snapshot).tasks if task.agent_type == "cognition")
        first = original.model_copy(deep=True, update={"task_id": "cognition_1", "questions": ["第一次"]})
        second = original.model_copy(deep=True, update={"task_id": "cognition_2", "questions": ["第二次"]})
        plan = TaskPlan(tasks=[first, second])
        self.assertEqual(validate_plan(plan, self.snapshot), [])

        class Handler:
            async def analyze(self, task, context):
                return AgentResult(result_id=identity("result", task.task_id), task_id=task.task_id,
                                   case_version=context.snapshot.version, status="completed",
                                   used_refs=task.input_refs)

        executor = Executor(self.store, self.run_id)
        executor.register("cognition", Handler())
        results = asyncio.run(executor.execute_tasks(plan, self.snapshot))
        self.assertEqual([result.task_id for result in results], ["cognition_1", "cognition_2"])
        self.assertEqual(len({result.result_id for result in results}), 2)
        self.assertEqual(len(self.store.objects(self.run_id, "result")), 2)
        with self.assertRaisesRegex(ValueError, "duplicate_task_id"):
            asyncio.run(executor.execute_tasks(TaskPlan(tasks=[first, first]), self.snapshot))
