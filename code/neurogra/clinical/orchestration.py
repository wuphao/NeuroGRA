"""Main-agent planning and bounded dispatch, with no fake production subagents."""
import asyncio
import time
from dataclasses import dataclass
from typing import Protocol
from .schemas import AgentTask, AgentResult, CaseSnapshot, TaskPlan, Issue, ROLES, Role, Contract
from .utils import identity
from .llm import ModelFailure, ModelGateway
from .storage import BudgetExceeded
from .schemas import RequestContent
from pydantic import Field, model_validator

class TaskProposal(Contract):
    agent_type: Role
    input_refs: list[str]
    questions: list[str]
    why: str


class PlanProposal(Contract):
    tasks: list[TaskProposal]


class ActionProposal(Contract):
    """Reference an existing code-owned request or propose new content, never an ID."""
    request_ref: str | None = None
    request: RequestContent | None = None
    target_roles: list[Role] = Field(min_length=1, max_length=4)
    allow_supplement: bool = False

    @model_validator(mode="after")
    def exactly_one_request(self):
        if (self.request_ref is None) == (self.request is None):
            raise ValueError("exactly_one_request_source_required")
        if len(set(self.target_roles)) != len(self.target_roles):
            raise ValueError("duplicate_target_role")
        return self


class IterationDecision(Contract):
    actions: list[ActionProposal] = Field(default_factory=list, max_length=8)
    tasks: list[TaskProposal] = Field(default_factory=list, max_length=4)
    stop: bool = False
    stop_reason: str = ""
    unresolved: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def terminal_has_no_actions(self):
        if self.stop and (self.actions or self.tasks):
            raise ValueError("stop_with_pending_actions")
        return self


ITERATION_PROMPT = """你是NeuroGRA主Agent，所有外部工具和专科任务均由你调度。
knowledge_permissions是程序配置：只能向eligible_roles派任务。主Agent自己的知识开关和专科开关独立；
可以代专科查询该专科启用的知识来源，关闭全部知识来源的专科不得要求知识补查。
患者事实只能来自输入患者记录，医学知识不能补写患者事实。判断哪些待处理请求能影响当前问题。
actions引用pending_requests提供的request_ref，或给出新的request内容，二者选其一。
新对象ID由程序创建，你不能生成或修改ID；source_refs和input_refs只能选输入允许的引用。
指定target_roles以便主Agent将工具结果交给相关专科。不要对无新增资料的角色重复派任务。
患者取数只读已有资料，不能下检查医嘱；不得输出SQL/Cypher、URL或凭据。
知识缺口可用knowledge请求，只有需要从原文抽取验证新条件时允许allow_supplement。
已经失败、无新内容或重复的请求不要再次执行；预算不足或资料不可获得时保留不确定性。
返回actions、tasks、stop、stop_reason、unresolved。停止时不返回动作或任务，不按专科人数投票。
tasks每个角色至多一个；questions为具体问题，input_refs仅引用该角色现有患者资料。
"""


PLANNER_PROMPT = """你是NeuroGRA主Agent，只规划子Agent任务，不诊断。
只为eligible_roles中列出的角色建立任务，根据当前问题选择需要的角色，每个角色至多一个任务，不要建立其他角色。
input_refs从给出的observation_ids选择；影像角色也可选image asset_id，不能创造ID。
每项至少一个输入、一个具体问题，why说明资料依据。不要把未知检查当已存在。
根据患者实际资料制定该角色要回答的问题，不输出临床判断。
"""


def eligible_roles(snapshot):
    return [i.domain for i in snapshot.inventory if i.domain in ROLES
            and (i.observation_ids or (i.domain == "imaging" and snapshot.images))
            and i.availability in {"available", "partial"}]


def validate_plan(plan: TaskPlan, snapshot: CaseSnapshot, require_all_roles=False) -> list[str]:
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
        if task.permitted_tools:
            errors.append("specialist_external_tools_forbidden")
        if task.budget.max_llm_calls > 64 or task.budget.max_retrieval_calls > 2 or task.budget.timeout_seconds > 3600:
            errors.append("task_budget_exceeded")
    if require_all_roles and seen != expected:
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
                               permitted_tools=[]))
    return TaskPlan(tasks=tasks, skipped={r: "未提供可用相关资料" for r in ROLES if r not in eligible_roles(snapshot)},
                    mode="deterministic_fallback" if tasks else "empty", issues=issues or [])


class MainAgent:
    def __init__(self, gateway, task_budget=None, enabled_roles=None):
        self.gateway = gateway
        self.task_budget = task_budget
        self.enabled_roles = set(ROLES if enabled_roles is None else enabled_roles)

    def plan(self, snapshot: CaseSnapshot) -> TaskPlan:
        # Keep the original snapshot private; restrict only the planning inventory.
        snapshot = snapshot.model_copy(deep=True, update={'inventory': [i for i in snapshot.inventory
            if i.domain in self.enabled_roles]})
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
                        permitted_tools=[]) for item in proposal.tasks])
                    if self.task_budget:
                        for task in plan.tasks:
                            task.budget = self.task_budget.model_copy(deep=True)
                    for task in plan.tasks:
                        if task.agent_type == 'imaging':
                            task.input_refs = list(dict.fromkeys(task.input_refs + [a.asset_id for a in snapshot.images]))
                    errors = validate_plan(plan, snapshot, require_all_roles=True)
                    if not errors:
                        plan.mode = "model"
                        for task in plan.tasks:
                            task.task_id = identity("task", [snapshot.case_id, task.agent_type, snapshot.version])
                            if task.agent_type == 'imaging':
                                task.permitted_tools = []
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

    def decide(self, context):
        """Main owns iteration choices; execution/identity remain deterministic code."""
        if self.gateway is not None:
            try:
                from .context import ContextBuilder
                from .config import ModelConfig
                context = ContextBuilder(getattr(self.gateway, 'config', None) or ModelConfig()).decision_context(
                    context, ITERATION_PROMPT, IterationDecision)
                decision = self.gateway.generate_structured(ITERATION_PROMPT, context, IterationDecision)
                allowed = {row["request_ref"] for row in context.get("pending_requests", [])}
                if any(action.request_ref is not None and action.request_ref not in allowed for action in decision.actions):
                    raise ValueError("unknown_pending_request_reference")
                refs = set(context.get("allowed_source_refs", []))
                if any(action.request is not None and not set(action.request.source_refs) <= refs for action in decision.actions):
                    raise ValueError("main_action_reference_out_of_scope")
                if any(task.agent_type not in context.get("eligible_roles", []) or
                       not set(task.input_refs) <= set(context.get("role_input_refs", {}).get(task.agent_type, []))
                       for task in decision.tasks):
                    raise ValueError("main_task_reference_out_of_scope")
                return decision
            except (ModelFailure, BudgetExceeded, ValueError):
                pass
        pending = context.get("pending_requests", [])
        blocked = set(context.get("blocked_request_refs", []))
        actions = [ActionProposal(request_ref=row["request_ref"], target_roles=[row["role"]],
                                  allow_supplement=row.get("allow_supplement", False))
                   for row in pending if row["request_ref"] not in blocked][:8]
        return IterationDecision(actions=actions, stop=not actions,
            stop_reason="no_actionable_requests" if not actions else "deterministic_main_fallback",
            unresolved=["主Agent模型决策不可用，按待处理需求执行确定性调度"] if self.gateway else [])


class AgentHandler(Protocol):
    async def analyze(self, task: AgentTask, context: "TaskContext") -> AgentResult: ...


@dataclass(frozen=True)
class TaskContext:
    snapshot: CaseSnapshot
    runtime: "AnalysisRuntime"
    evidence: list = None
    image_result: dict | None = None
    previous_result: AgentResult | None = None
    additional_context: list = None
    request_outcomes: list = None

    def __post_init__(self):
        for key in ("evidence", "additional_context", "request_outcomes"):
            if getattr(self, key) is None:
                object.__setattr__(self, key, [])


class AnalysisRuntime:
    """Generation and audit only. No patient, knowledge or imaging capability."""
    def __init__(self, task, store, run_id, model_config=None, reserved_calls=6, knowledge_access=None):
        self.task, self.store, self.run_id = task, store, run_id
        self.knowledge_access = knowledge_access
        self.deadline = min(store.run(run_id)["deadline"], time.time() + task.budget.timeout_seconds)
        self.gateway = (ModelGateway(model_config, store, run_id, scope=task.task_id,
                        scope_limit=task.budget.max_llm_calls, deadline=self.deadline, reserved_calls=reserved_calls)
                        if model_config else None)

    def prepare_system(self, system):
        if self.knowledge_access is not None:
            system += ('\n运行权限由代码决定：KB=' + str(self.knowledge_access.use_kb) +
                '，KG=' + str(self.knowledge_access.use_kg) +
                '。只能引用主Agent提供且已获准的知识证据。关闭的知识来源不可请求或引用；'
                '两者均关闭时不得返回knowledge请求。患者资料补查仍可返回patient_data请求。')
        return system

    async def generate_structured(self, system, data, schema):
        system = self.prepare_system(system)
        if time.time() >= self.deadline:
            raise TimeoutError("task_deadline")
        if self.gateway is None:
            raise ModelFailure("task_model_unconfigured")
        cache_key = self._cache_key(system, data, schema)
        rows = [r for r in self.store.objects(self.run_id, 'validated_batch') if r['object_id'] == cache_key]
        if rows:
            self.store.event(self.run_id, 'context_cache_hit', {'task_id': self.task.task_id, 'key': cache_key})
            return schema.model_validate(rows[-1]['payload'])
        output = await asyncio.to_thread(self.gateway.generate_structured, system, data, schema)
        return output

    def _cache_key(self, system, data, schema):
        from .utils import fingerprint
        # Scheduling metadata and previous drafts do not change the clinical inputs.
        material = {k: v for k, v in data.items() if k not in {
            'previous_result', 'request_outcomes', 'availability', 'continuation_ref', 'batch'}}
        material['unresolved_outcomes'] = [r for r in data.get('request_outcomes', [])
                                           if r.get('status') not in {'found', 'completed'}]
        return fingerprint({'role': self.task.agent_type, 'case_version': self.task.case_version,
            'questions': self.task.questions, 'system': system, 'data': material,
            'schema': schema.model_json_schema(), 'model': self.gateway.config.model_dump(mode='json'),
            'access': self.knowledge_access.model_dump() if self.knowledge_access else None})

    def commit_structured(self, system, data, schema, output):
        """Only called after the consumer has validated the claims and references."""
        key = self._cache_key(self.prepare_system(system), data, schema)
        if not any(r['object_id'] == key for r in self.store.objects(self.run_id, 'validated_batch')):
            self.store.save(self.run_id, 'validated_batch', key, output)

    def record_analysis_batch(self, batch_index, observation_ids, status):
        self.store.event(self.run_id, "analysis_batch", {"task_id": self.task.task_id,
            "batch_index": batch_index, "observation_ids": observation_ids, "status": status})


def scoped_snapshot(snapshot, task):
    refs = set(task.input_refs + task.background_refs)
    observations = [o for o in snapshot.observations if o.observation_id in refs]
    images = [i for i in snapshot.images if i.asset_id in task.input_refs]
    source_refs = {r for o in observations for r in o.source_refs} | {r for i in images for r in i.source_refs}
    return snapshot.model_copy(deep=True, update={
        "observations": [o.model_copy(deep=True) for o in observations],
        "images": [i.model_copy(deep=True) for i in images],
        "records": [r.model_copy(deep=True) for r in snapshot.records if r.record_id in source_refs],
        "inventory": [], "processing": {}, "timeline": [], "conflict_groups": [], "issues": [],
        "patient_metadata": {}})


class Executor:
    """Dispatch only the explicit tasks and scoped materials supplied by main."""
    def __init__(self, store, run_id, max_parallel=4, model_config=None, reserved_calls=6, agent_configs=None):
        self.store, self.run_id = store, run_id
        self.handlers = {}
        self.model_config = model_config
        self.reserved_calls = reserved_calls
        self.agent_configs = agent_configs or {}
        self.semaphore = asyncio.Semaphore(max_parallel)

    def register(self, role, handler: AgentHandler):
        if role not in ROLES:
            raise ValueError("unknown_agent_role")
        self.handlers[role] = handler

    async def execute_tasks(self, plan: TaskPlan, snapshot: CaseSnapshot, task_inputs=None) -> list[AgentResult]:
        from .schemas import EvidenceItem
        from .history import check_claims
        from neurogra_common import validate_references
        task_inputs = task_inputs or {}
        errors = validate_plan(plan, snapshot)
        if errors:
            raise ValueError(",".join(errors))

        async def execute(task):
            async with self.semaphore:
                subset = scoped_snapshot(snapshot, task)
                supplied = task_inputs.get(task.task_id, {})
                from copy import deepcopy
                supplied = deepcopy(supplied)
                input_payload = {"task": task.model_dump(mode="json"),
                                 "snapshot": subset.model_dump(mode="json"), **supplied}
                from .utils import fingerprint
                bound = fingerprint(input_payload)
                bindings = [r for r in self.store.objects(self.run_id, "task_input") if r["object_id"] == task.task_id]
                if bindings and bindings[0]["payload"]["fingerprint"] != bound:
                    raise ValueError("task_input_changed")
                if not bindings:
                    self.store.save(self.run_id, "task_input", task.task_id,
                                    {"fingerprint": bound, "input": input_payload})
                saved = [r for r in self.store.objects(self.run_id, "result") if r["payload"]["task_id"] == task.task_id]
                if saved:
                    return AgentResult.model_validate(max(saved, key=lambda r: r["version"])["payload"])
                if not any(r["object_id"] == task.task_id for r in self.store.objects(self.run_id, "task")):
                    self.store.save(self.run_id, "task", task.task_id, task)
                self.store.event(self.run_id, "task_started", {"task_id": task.task_id, "agent": task.agent_type})
                try:
                    if task.agent_type not in self.handlers:
                        raise LookupError("agent_not_implemented")
                    evidence = [EvidenceItem.model_validate(row).model_copy(deep=True) for row in supplied.get("evidence", [])]
                    previous = AgentResult.model_validate(supplied["previous_result"]) if supplied.get("previous_result") else None
                    context = TaskContext(subset, AnalysisRuntime(task, self.store, self.run_id, self.model_config, self.reserved_calls,
                        self.agent_configs.get(task.agent_type)),
                        evidence=evidence, previous_result=previous, image_result=supplied.get("image_result"),
                        additional_context=supplied.get("additional_context", []),
                        request_outcomes=supplied.get("request_outcomes", []))
                    remaining = self.store.run(self.run_id)["deadline"] - time.time()
                    if remaining <= 0:
                        raise TimeoutError("run_deadline")
                    result = await asyncio.wait_for(self.handlers[task.agent_type].analyze(task, context),
                                                    min(remaining, task.budget.timeout_seconds))
                    result = AgentResult.model_validate(result)
                    if result.task_id != task.task_id or result.case_version != snapshot.version:
                        raise ValueError("invalid_result_binding")
                    refs = {o.observation_id for o in subset.observations} | {r.record_id for r in subset.records}
                    validate_references(result.used_refs, refs | {i.asset_id for i in subset.images})
                    check_claims(result.claims, subset, evidence)
                    allowed = refs | {i.asset_id for i in subset.images} | {e.evidence_id for e in evidence} | {e.span_id for e in evidence}
                    for request in result.requests:
                        validate_references(request.source_refs, allowed)
                    image_data = supplied.get("image_result") or {}
                    image_ids = {image_data["result_id"]} if image_data.get("status") == "completed" else set()
                    validate_references(result.model_results, image_ids)
                except Exception as exc:
                    result = AgentResult(result_id=identity("result", task.task_id), task_id=task.task_id,
                                         case_version=snapshot.version, status="failed",
                                         issues=[Issue(code="agent_unavailable" if isinstance(exc, LookupError) else "task_failed",
                                                       stage="executor", message=type(exc).__name__)])
                self.store.save(self.run_id, "result", result.result_id, result)
                self.store.save(self.run_id, 'agent_working_state', task.task_id, {
                    'patient_id': snapshot.patient_id, 'case_version': snapshot.version,
                    'role': task.agent_type, 'questions': task.questions,
                    'result_id': result.result_id, 'status': result.status,
                    'claim_ids': [c.claim_id for c in result.claims],
                    'dependencies': {c.claim_id: c.observation_ids + c.evidence_ids for c in result.claims},
                    'coverage': result.coverage, 'pending_requests': [r.model_dump() for r in result.requests]})
                self.store.event(self.run_id, "task_finished", {"task_id": task.task_id, "status": result.status})
                return result
        return await asyncio.gather(*(execute(t) for t in plan.tasks))
