"""Main-agent planning and bounded dispatch, with no fake production subagents."""
import asyncio
import time
from dataclasses import dataclass
from typing import Protocol
from .schemas import AgentTask, AgentResult, CaseSnapshot, TaskPlan, Issue, ROLES, Role, Contract
from .utils import identity
from .llm import ModelFailure, ModelGateway
from .storage import BudgetExceeded
from .retrieval import read_case_record

class TaskProposal(Contract):
    agent_type: Role
    input_refs: list[str]
    questions: list[str]
    why: str


class PlanProposal(Contract):
    tasks: list[TaskProposal]


PLANNER_PROMPT = """你是NeuroGRA主Agent，只规划子Agent任务，不诊断。
只为eligible_roles中列出的角色建立任务，每个角色恰好一个，不要建立其他角色。
input_refs从给出的observation_ids选择；影像角色也可选image asset_id，不能创造ID。
每项至少一个输入、一个具体问题，why说明资料依据。不要把未知检查当已存在。
根据患者实际资料制定该角色要回答的问题，不输出临床判断。
"""


def eligible_roles(snapshot):
    return [i.domain for i in snapshot.inventory if i.domain in ROLES
            and (i.observation_ids or (i.domain == "imaging" and snapshot.images))
            and i.availability in {"available", "partial"}]


def validate_plan(plan: TaskPlan, snapshot: CaseSnapshot) -> list[str]:
    errors, seen = [], set()
    observations = {o.observation_id: o for o in snapshot.observations}
    images = {i.asset_id for i in snapshot.images}
    expected = set(eligible_roles(snapshot))
    for task in plan.tasks:
        if task.agent_type in seen:
            errors.append("duplicate_role")
        seen.add(task.agent_type)
        if task.agent_type not in expected:
            errors.append("role_without_data")
        if task.case_version != snapshot.version:
            errors.append("stale_case_version")
        if not task.input_refs or not task.questions or not all(q.strip() for q in task.questions) or not task.why.strip():
            errors.append("empty_task")
        for ref in task.input_refs:
            if task.agent_type == "imaging" and ref in images:
                continue
            if ref not in observations or task.agent_type not in observations[ref].domains:
                errors.append("invalid_input_ref")
        for ref in task.background_refs:
            if ref not in observations or "background" not in observations[ref].domains:
                errors.append("invalid_background_ref")
        if task.agent_type == 'imaging' and not images <= set(task.input_refs):
            errors.append('unassigned_image_assets')
        if not set(task.permitted_tools) <= ({"read_case_record", "search_knowledge", "diamond_predict"} if task.agent_type == "imaging" else {"read_case_record", "search_knowledge"}):
            errors.append("tool_not_enabled")
        if task.budget.max_llm_calls > 64 or task.budget.max_retrieval_calls > 2 or task.budget.timeout_seconds > 3600:
            errors.append("task_budget_exceeded")
    if seen != expected:
        errors.append("missing_eligible_role")
    return sorted(set(errors))


def fallback_plan(snapshot, issues=None):
    tasks = []
    background = [o.observation_id for o in snapshot.observations if "background" in o.domains]
    for role in eligible_roles(snapshot):
        refs = [o.observation_id for o in snapshot.observations if role in o.domains]
        if role == "imaging":
            refs += [i.asset_id for i in snapshot.images]
        tasks.append(AgentTask(task_id=identity("task", [snapshot.case_id, role, snapshot.version]),
                               agent_type=role, case_version=snapshot.version, input_refs=refs,
                               background_refs=background, questions=["整理实际提供的资料并指出解释限制"],
                               why="依据资料盘点采用确定性路由",
                               permitted_tools=["read_case_record", "search_knowledge"] + (["diamond_predict"] if role == "imaging" else [])))
    return TaskPlan(tasks=tasks, skipped={r: "未提供可用相关资料" for r in ROLES if r not in eligible_roles(snapshot)},
                    mode="deterministic_fallback" if tasks else "empty", issues=issues or [])


class MainAgent:
    def __init__(self, gateway, task_budget=None):
        self.gateway = gateway
        self.task_budget = task_budget

    def plan(self, snapshot: CaseSnapshot) -> TaskPlan:
        if not eligible_roles(snapshot):
            return fallback_plan(snapshot)
        data = {"case_version": snapshot.version, "eligible_roles": eligible_roles(snapshot),
                "inventory": [i.model_dump(mode="json") for i in snapshot.inventory if i.domain in eligible_roles(snapshot)],
                "observations": [{"observation_id": o.observation_id, "name": o.name, "domains": o.domains,
                                  "status": o.status, "quote": o.quote[:300]} for o in snapshot.observations],
                "images": [{"asset_id": i.asset_id, "modality": i.modality, "status": i.status} for i in snapshot.images]}
        background = [o.observation_id for o in snapshot.observations if "background" in o.domains]
        grouped = len(snapshot.observations) > 40
        if grouped:
            # Planning is about task coverage. All observations remain assigned;
            # the model sees the available forms/fields rather than repeated IDs.
            data['observations'] = [{'input_ref': role,
                'forms': sorted({str(o.context.get('名称', '未命名资料')) for o in snapshot.observations if role in o.domains}),
                'fields': sorted({o.name for o in snapshot.observations if role in o.domains}),
                'observation_count': sum(role in o.domains for o in snapshot.observations)} for role in eligible_roles(snapshot)]
            data['inventory'] = [{'domain': i.domain, 'availability': i.availability,
                                  'limitations': i.limitations} for i in snapshot.inventory]
            data['input_ref_instruction'] = 'input_refs使用对应角色名；程序会将该角色全部原始观察分配给任务，不遗漏字段。'
        issues = []
        if self.gateway:
            for attempt in range(2):
                try:
                    proposal = self.gateway.generate_structured(PLANNER_PROMPT, data, PlanProposal)
                    plan = TaskPlan(tasks=[AgentTask(
                        task_id=identity("task", [snapshot.case_id, item.agent_type, snapshot.version]),
                        agent_type=item.agent_type, case_version=snapshot.version,
                        input_refs=([o.observation_id for o in snapshot.observations if item.agent_type in o.domains]
                                    if grouped and item.input_refs == [item.agent_type] else item.input_refs), background_refs=background,
                        questions=item.questions, why=item.why,
                        permitted_tools=["read_case_record", "search_knowledge"]) for item in proposal.tasks])
                    if self.task_budget:
                        for task in plan.tasks:
                            task.budget = self.task_budget.model_copy(deep=True)
                    for task in plan.tasks:
                        if task.agent_type == 'imaging':
                            task.input_refs = list(dict.fromkeys(task.input_refs + [a.asset_id for a in snapshot.images]))
                    errors = validate_plan(plan, snapshot)
                    if not errors:
                        plan.mode = "model"
                        for task in plan.tasks:
                            task.task_id = identity("task", [snapshot.case_id, task.agent_type, snapshot.version])
                            if task.agent_type == 'imaging':
                                task.permitted_tools.append('diamond_predict')
                        plan.skipped = {r: "未提供可用相关资料" for r in ROLES if r not in eligible_roles(snapshot)}
                        return plan
                    data["validation_errors"] = errors
                    data["rejected_plan"] = proposal.model_dump(mode="json")
                    issues.append(Issue(code="invalid_model_plan", stage="planning", message=",".join(errors)))
                except (ModelFailure, BudgetExceeded) as exc:
                    issues.append(Issue(code="planner_unavailable", stage="planning", message=type(exc).__name__))
                    break
        else:
            issues.append(Issue(code="planner_not_called", stage="planning", message="明确选择了离线规则模式"))
        plan = fallback_plan(snapshot, issues)
        if self.task_budget:
            for task in plan.tasks:
                task.budget = self.task_budget.model_copy(deep=True)
        return plan


class AgentHandler(Protocol):
    async def analyze(self, task: AgentTask, context: "TaskContext") -> AgentResult: ...


@dataclass
class TaskContext:
    snapshot: CaseSnapshot
    tools: "ToolProxy"


class ToolProxy:
    def __init__(self, task, snapshot, knowledge, store, run_id, model_config=None, diamond=None):
        self.task, self.snapshot, self.knowledge = task, snapshot, knowledge
        self.store, self.run_id = store, run_id
        self.diamond = diamond
        self.diamond_result_ids = set()
        self.retrieval_calls = sum(1 for r in store.objects(run_id, "task_retrieval_request")
                                   if r["payload"]["task_id"] == task.task_id)
        self.evidence_ids = set()
        self.lock = asyncio.Lock()
        self.deadline = min(store.run(run_id)["deadline"], time.time() + task.budget.timeout_seconds)
        self.gateway = (ModelGateway(model_config, store, run_id, scope=task.task_id,
                        scope_limit=task.budget.max_llm_calls, deadline=self.deadline)
                        if model_config else None)

    def check_deadline(self):
        if time.time() >= self.deadline:
            raise TimeoutError("task_deadline")

    async def generate_structured(self, system, data, schema):
        self.check_deadline()
        if self.gateway is None:
            raise ModelFailure("task_model_unconfigured")
        return await asyncio.to_thread(self.gateway.generate_structured, system, data, schema)

    def record_analysis_batch(self, batch_index, observation_ids, status):
        self.store.event(self.run_id, 'analysis_batch', {'task_id': self.task.task_id,
            'batch_index': batch_index, 'observation_ids': observation_ids, 'status': status})

    async def read_case_record(self, record_id, start=0, end=None):
        self.check_deadline()
        if "read_case_record" not in self.task.permitted_tools:
            raise ValueError("tool_not_permitted")
        result = read_case_record(self.snapshot, record_id, self.task.case_version, start, end)
        self.store.event(self.run_id, "record_read", {"task_id": self.task.task_id, "record_id": record_id})
        return result

    async def search_knowledge(self, request):
        self.check_deadline()
        async with self.lock:
            if "search_knowledge" not in self.task.permitted_tools:
                raise ValueError("tool_not_permitted")
            if self.retrieval_calls >= self.task.budget.max_retrieval_calls:
                raise BudgetExceeded("task_retrieval")
            allowed = {o.observation_id for o in self.snapshot.observations}
            if not set(request.patient_fact_refs) <= allowed:
                raise ValueError("invalid_patient_fact_ref")
            self.retrieval_calls += 1
            self.store.save(self.run_id, "task_retrieval_request",
                            self.task.task_id + ":" + str(self.retrieval_calls),
                            {"task_id": self.task.task_id, "request": request.model_dump(mode="json")})
        if self.knowledge is None:
            raise ValueError("knowledge_unavailable")
        result = await asyncio.to_thread(self.knowledge.search_knowledge, request, deadline=self.deadline)
        self.check_deadline()
        self.evidence_ids.update(item.evidence_id for item in result.items)
        return result

    async def diamond_predict(self):
        self.check_deadline()
        if self.task.agent_type != 'imaging' or 'diamond_predict' not in self.task.permitted_tools:
            raise ValueError('tool_not_permitted')
        if self.diamond is None:
            raise ValueError('diamond_unconfigured')
        result = await asyncio.to_thread(self.diamond.predict, self.snapshot, self.deadline)
        if result.status == 'completed':
            self.diamond_result_ids.add(result.result_id)
        return result


class Executor:
    def __init__(self, store, run_id, knowledge=None, max_parallel=4, model_config=None, diamond=None):
        self.store, self.run_id, self.knowledge = store, run_id, knowledge
        self.handlers = {}
        self.model_config = model_config
        self.diamond = diamond
        self.semaphore = asyncio.Semaphore(max_parallel)

    def register(self, role, handler: AgentHandler):
        if role not in ROLES:
            raise ValueError("unknown_agent_role")
        self.handlers[role] = handler

    async def execute_tasks(self, plan: TaskPlan, snapshot: CaseSnapshot) -> list[AgentResult]:
        errors = validate_plan(plan, snapshot)
        if errors:
            raise ValueError(",".join(errors))

        async def execute(task):
            async with self.semaphore:
                saved = [r for r in self.store.objects(self.run_id, "result") if r["payload"]["task_id"] == task.task_id]
                if saved:
                    previous = AgentResult.model_validate(max(saved, key=lambda r: r["version"])["payload"])
                    if previous.model_results:
                        from .diamond import DiamondResult
                        tools = {r['object_id']: r['payload'] for r in self.store.objects(self.run_id, 'diamond_result')}
                        for tool_id in previous.model_results:
                            if not self.diamond or tool_id not in tools:
                                raise ValueError('diamond_reuse_blocked:missing_tool_binding')
                            self.diamond.assert_reusable(DiamondResult.model_validate(tools[tool_id]), snapshot)
                    return previous
                if not any(r["object_id"] == task.task_id for r in self.store.objects(self.run_id, "task")):
                    self.store.save(self.run_id, "task", task.task_id, task)
                self.store.event(self.run_id, "task_started", {"task_id": task.task_id, "agent": task.agent_type})
                try:
                    if task.agent_type not in self.handlers:
                        raise LookupError("agent_not_implemented")
                    obs_refs = set(task.input_refs + task.background_refs)
                    observations = [o for o in snapshot.observations if o.observation_id in obs_refs]
                    images = [i for i in snapshot.images if i.asset_id in task.input_refs]
                    source_refs = {r for o in observations for r in o.source_refs} | {r for i in images for r in i.source_refs}
                    subset = snapshot.model_copy(update={
                        "observations": observations, "images": images,
                        "records": [r for r in snapshot.records if r.record_id in source_refs],
                        "inventory": [], "processing": {}, "timeline": [], "conflict_groups": [], "issues": []})
                    context = TaskContext(subset, ToolProxy(task, subset, self.knowledge, self.store, self.run_id, self.model_config, self.diamond))
                    import time
                    remaining = self.store.run(self.run_id)["deadline"] - time.time()
                    if remaining <= 0:
                        raise TimeoutError("run_deadline")
                    result = await asyncio.wait_for(self.handlers[task.agent_type].analyze(task, context),
                                                    min(remaining, task.budget.timeout_seconds))
                    result = AgentResult.model_validate(result)
                    if result.task_id != task.task_id or result.case_version != snapshot.version:
                        raise ValueError("invalid_result_binding")
                    if not set(result.used_refs) <= (obs_refs | source_refs):
                        raise ValueError("invalid_result_references")
                    if not set(result.model_results) <= context.tools.diamond_result_ids:
                        raise ValueError('unexecuted_image_tool_result')
                    for claim in result.claims:
                        if not claim.observation_ids or not set(claim.observation_ids) <= obs_refs:
                            raise ValueError("invalid_claim_observations")
                        if not set(claim.evidence_ids) <= context.tools.evidence_ids:
                            raise ValueError("invalid_claim_evidence")
                        if claim.kind == "interpretation" and not claim.evidence_ids:
                            raise ValueError("interpretation_without_evidence")
                        if claim.kind == "model_classification":
                            raise ValueError("image_tool_not_enabled_in_foundation")
                except Exception as exc:
                    result = AgentResult(result_id=identity("result", task.task_id), task_id=task.task_id,
                                         case_version=snapshot.version, status="failed",
                                         issues=[Issue(code="agent_unavailable" if isinstance(exc, LookupError) else "task_failed",
                                                       stage="executor", message=type(exc).__name__)])
                self.store.save(self.run_id, "result", result.result_id, result)
                self.store.event(self.run_id, "task_finished", {"task_id": task.task_id, "status": result.status})
                return result
        return await asyncio.gather(*(execute(t) for t in plan.tasks))
