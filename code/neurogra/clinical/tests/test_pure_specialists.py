import asyncio
import tempfile
import unittest
from pathlib import Path

from neurogra.clinical.config import ClinicalConfig
from neurogra.clinical.intake import ingest_patient
from neurogra.clinical.profiling import profile_case
from neurogra.clinical.history import HistoryAgent, HistoryDraft
from neurogra.clinical.orchestration import Executor, TaskContext, fallback_plan, MainAgent, IterationDecision
from neurogra.clinical.schemas import AgentResult
from neurogra.clinical.storage import RunStore
from neurogra.clinical.utils import identity


class PureSpecialistTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = ClinicalConfig(project_root=Path(self.temp.name))
        self.snapshot = profile_case(ingest_patient({"患者ID": "synthetic", "病史": "记忆下降", "年龄": 68},
                                                   self.config), [], self.config, None)
        self.task = fallback_plan(self.snapshot).tasks[0]
        self.store = RunStore(self.config.resolve(self.config.database))
        self.run = self.store.create_run({}, self.config.budget.model_dump())

    def test_child_returns_request_without_executing_it(self):
        ref = self.snapshot.observations[0].observation_id
        class Runtime:
            async def generate_structured(self, system, data, schema):
                return schema.model_validate({"claims": [], "uncertainties": ["缺少教育资料"],
                    "requests": [{"type": "patient_data", "question": "补取已有教育记录", "requested_fields": ["教育年限"],
                                  "source_refs": [ref], "reason": "解释限制"}]})
        result = asyncio.run(HistoryAgent().analyze(self.task, TaskContext(self.snapshot, Runtime())))
        self.assertEqual(result.requests[0].requested_fields, ["教育年限"])
        self.assertFalse(hasattr(Runtime(), "search_knowledge"))
        self.assertEqual(self.store.summary(self.run)["calls"], [])

    def test_dispatch_rejects_request_reference_outside_scoped_input(self):
        class Handler:
            async def analyze(inner, task, context):
                return AgentResult(result_id=identity("result", task.task_id), task_id=task.task_id,
                    case_version=task.case_version, status="completed",
                    requests=[{"type": "knowledge", "question": "检查", "reason": "检查",
                               "source_refs": ["other_patient_id"]}])
        executor = Executor(self.store, self.run)
        for task in fallback_plan(self.snapshot).tasks:
            executor.register(task.agent_type, Handler())
        results = asyncio.run(executor.execute_tasks(fallback_plan(self.snapshot), self.snapshot))
        self.assertTrue(all(row.status == "failed" for row in results))

    def test_child_mutation_cannot_modify_main_input(self):
        original = self.snapshot.model_dump(mode="json")
        class Handler:
            async def analyze(inner, task, context):
                context.snapshot.observations[0].quote = "子任务修改本地副本"
                return AgentResult(result_id=identity("result", task.task_id), task_id=task.task_id,
                                   case_version=task.case_version, status="completed")
        executor = Executor(self.store, self.run)
        for task in fallback_plan(self.snapshot).tasks:
            executor.register(task.agent_type, Handler())
        asyncio.run(executor.execute_tasks(fallback_plan(self.snapshot), self.snapshot))
        self.assertEqual(original, self.snapshot.model_dump(mode="json"))

    def test_main_does_not_accept_invented_pending_request_reference(self):
        class Model:
            def generate_structured(inner, system, data, schema):
                return IterationDecision(actions=[{"request_ref": "invented", "target_roles": ["history"]}])
        decision = MainAgent(Model()).decide({"pending_requests": [], "blocked_request_refs": []})
        self.assertTrue(decision.stop)
        self.assertFalse(decision.actions)

    def test_specialist_tasks_cannot_receive_external_tool_permissions(self):
        plan = fallback_plan(self.snapshot)
        plan.tasks[0].permitted_tools = ["search_knowledge"]
        with self.assertRaisesRegex(ValueError, "specialist_external_tools_forbidden"):
            asyncio.run(Executor(self.store, self.run).execute_tasks(plan, self.snapshot))
