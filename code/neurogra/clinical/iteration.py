"""Main agent's bounded loop. Its private context is never exposed to specialists."""

import asyncio
import json
import time

from .schemas import (CaseSnapshot, AgentResult, AgentTask, TaskPlan, EvidenceItem,
                      EvidenceBundle, RequestContent, InventoryItem, ROLES)
from .orchestration import MainAgent, Executor, IterationDecision, ActionProposal, eligible_roles
from .llm import ModelGateway
from .main_tools import MainToolExecutor
from .utils import identity, fingerprint


def request_signature(request):
    if request.type == "patient_data":
        key = [request.type, request.form_key, sorted(request.requested_fields), request.cursor, request.page_size,
               str(request.date_from), str(request.date_to)]
    else:
        key = [request.type, "".join(request.question.split()).casefold(), request.purpose,
               sorted(request.source_refs), request.cursor, request.page_size]
    return identity("request_signature", key)


def update_evidence(existing, incoming):
    """Replace an earlier applicability assessment for the same statement/source."""
    def key(row):
        return row['version'], row['document_id'], row['span_id'], row.get('statement_id')
    merged = {key(row): row for row in existing}
    changed = False
    for row in incoming:
        previous = merged.get(key(row))
        if previous != row:
            changed = True
            merged[key(row)] = row
    return list(merged.values()), changed


def _last(store, run_id, kind, object_id):
    rows = [row for row in store.objects(run_id, kind) if row["object_id"] == object_id]
    return max(rows, key=lambda row: row["version"])["payload"] if rows else None


def add_pending(state, request, role, origin, *, allow_supplement=False):
    signature = request_signature(request)
    if signature in state["seen"]:
        return
    request_ref = identity("request", [origin, role, request.model_dump(mode="json")])
    if not any(row["request_ref"] == request_ref for row in state["pending"]):
        state["pending"].append({"request_ref": request_ref, "role": role,
            "request": request.model_dump(mode="json"), "signature": signature,
            "allow_supplement": allow_supplement})


def queue_knowledge_gaps(state, data, role, action, request, snapshot):
    aliases = {"age": "年龄", "sex": "性别", "mmse": "MMSE", "moca": "MoCA", "cdr": "CDR"}
    names = {row.name for row in snapshot.observations}
    for demand in data.get("data_requests", []):
        fields = [name if name in names else aliases.get(name, name) for name in demand["fields"]]
        if fields:
            followup = RequestContent(type="patient_data", question="补取图谱适用条件所需的已有患者记录：" + "、".join(fields),
                requested_fields=fields, source_refs=request.source_refs,
                reason=demand.get("reason") or "知识条件缺少患者事实", purpose=request.purpose)
            add_pending(state, followup, role, [action["action_id"], demand["gap_id"]])
    for gap in data.get("gaps", []):
        if gap["kind"] in {"knowledge_condition", "source_evidence", "graph_connection"}:
            followup = RequestContent(type="knowledge", question=(request.question + "；补查知识缺口：" +
                "、".join(gap["missing_items"]))[:1800], source_refs=request.source_refs,
                reason=gap.get("reason") or "核对原文知识与条件", purpose=request.purpose)
            add_pending(state, followup, role, [action["action_id"], gap["gap_id"]], allow_supplement=True)


def _material_key(row):
    return fingerprint({key: row.model_dump(mode="json")[key] for key in
                        ("name", "quote", "value", "unit", "status", "time", "domains", "kind")})


def merge_patient_data(snapshot, data):
    if data.get("patient_id") != snapshot.patient_id:
        raise ValueError("iteration_patient_binding_failed")
    from .schemas import Observation, SourceRecord
    known = {_material_key(row) for row in snapshot.observations}
    novel = []
    for value in data.get("observations", []):
        row = Observation.model_validate(value)
        if _material_key(row) not in known:
            novel.append(row)
            known.add(_material_key(row))
    if not novel:
        return snapshot, False
    records = {row.record_id: row for row in snapshot.records}
    records.update({row.record_id: row for row in map(SourceRecord.model_validate, data.get("records", []))})
    for row in novel:
        if not row.source_refs or any(ref not in records or row.quote not in records[ref].text for ref in row.source_refs):
            raise ValueError("new_patient_fact_without_source")
    updated = snapshot.model_copy(deep=True)
    updated.observations.extend(novel)
    updated.records = list(records.values())
    updated.version += 1
    updated.inventory = []
    for role in (*ROLES, "background", "unknown"):
        values = [row for row in updated.observations if role in row.domains]
        usable = [row for row in values if row.status != "not_recorded"]
        availability = "available" if usable else "absent"
        if values and len(usable) != len(values) or role == "imaging" and updated.images:
            availability = "partial"
        updated.inventory.append(InventoryItem(domain=role, availability=availability,
            observation_ids=[row.observation_id for row in values],
            record_ids=list(dict.fromkeys(ref for row in values for ref in row.source_refs))))
    updated.timeline = [row.observation_id for row in sorted(updated.observations,
        key=lambda row: row.time.value or "~") if row.time.value]
    from collections import defaultdict
    groups = defaultdict(list)
    for row in updated.observations:
        if row.time.precision == "day":
            groups[(row.name, row.time.value, row.unit, str(row.context.get("名称")),
                    str(row.context.get("方法")))].append(row)
    updated.conflict_groups = [list(dict.fromkeys(row.observation_id for row in group))
                              for group in groups.values() if len({fingerprint(row.value) for row in group}) > 1]
    return updated, True


def _persist_snapshot(store, run_id, snapshot):
    rows = [row for row in store.objects(run_id, "snapshot") if row["object_id"] == snapshot.case_id]
    same = next((row for row in rows if row["version"] == snapshot.version), None)
    if same:
        if fingerprint(same["payload"]) != fingerprint(snapshot.model_dump(mode="json")):
            raise ValueError("restored_snapshot_changed")
        return
    last = max((row["version"] for row in rows), default=0)
    store.save(run_id, "snapshot", snapshot.case_id, snapshot, snapshot.version, last)


def run_main_loop(preparation, config, store, *, handlers, tools=None, main_factory=None):
    from .agent_policy import scoped_tool_data
    enabled = set(config.enabled_roles())
    run_id = preparation.run_id
    sealed = _last(store, run_id, "main_loop_outcome", "main")
    if sealed is not None:
        return sealed
    snapshot = preparation.snapshot.model_copy(deep=True)
    tools = tools or MainToolExecutor(snapshot, config, store, run_id,
                                     graph_release_id=preparation.knowledge_release_id)
    completed = sorted(store.objects(run_id, "main_round_complete"), key=lambda row: row["payload"]["round"])
    state = {"snapshot": snapshot.model_dump(mode="json"), "results": {}, "tasks": {},
             "evidence": {}, "context": {}, "outcomes": {}, "seen": [], "pending": [],
             "unresolved": [], "stop_reason": "", "round": -1}
    if completed:
        state = completed[-1]["payload"]
        snapshot = CaseSnapshot.model_validate(state["snapshot"])
    for round_no in range(state["round"] + 1, config.max_rounds):
        if state["stop_reason"]:
            break
        round_id = identity("main_round", [run_id, round_no])
        tools.snapshot = snapshot
        tools.patient.snapshot = snapshot
        tools.allowed_refs.update(row.observation_id for row in snapshot.observations)
        tools.allowed_refs.update(row.record_id for row in snapshot.records)
        limits = json.loads(store.run(run_id)["limits"])
        used = sum(row["n"] for row in store.summary(run_id)["calls"] if row["kind"] == "llm")
        if time.time() >= store.run(run_id)["deadline"]:
            state["stop_reason"] = "run_deadline"
            break
        if limits["llm"] - used <= config.synthesis_reserved_calls:
            state["stop_reason"] = "reserved_synthesis_budget"
            break
        saved = _last(store, run_id, "main_round_plan", round_id)
        if saved:
            decision = IterationDecision.model_validate(saved["decision"])
        elif round_no == 0:
            from .orchestration import TaskProposal
            purposes = {"history": "diagnostic_assessment", "cognition": "cognitive_assessment",
                        "laboratory": "biomarker_interpretation", "imaging": "imaging_interpretation"}
            decision = IterationDecision(tasks=[TaskProposal(agent_type=task.agent_type, input_refs=task.input_refs,
                questions=task.questions, why=task.why) for task in preparation.plan.tasks],
                actions=[ActionProposal(target_roles=[task.agent_type], request=RequestContent(type="knowledge",
                    question=(task.agent_type + " " + " ".join(task.questions) + " " + " ".join(dict.fromkeys(
                        row.name for row in snapshot.observations if row.observation_id in task.input_refs)))[:1800],
                    source_refs=list(dict.fromkeys(task.input_refs + task.background_refs)),
                    purpose=purposes[task.agent_type], reason="主Agent准备任务证据"))
                    for task in preparation.plan.tasks if task.agent_type in enabled and
                    (config.knowledge_access([task.agent_type]).use_kb or config.knowledge_access([task.agent_type]).use_kg)])
        else:
            pending = [row for row in state["pending"] if row["signature"] not in state["seen"]]
            catalog = tools.patient.list_available_data()
            catalog.pop("records", None)
            catalog['total_observations'] = len(catalog.get('observations', []))
            catalog["observations"] = catalog.get("observations", [])[:48]
            visible_refs = {row["observation_id"] for row in catalog["observations"]}
            visible_refs.update(ref for row in pending for ref in row["request"]["source_refs"][:8])
            projected = [{**row, "request": {**row["request"], "source_refs": row["request"]["source_refs"][:8]},
                          "omitted_source_ref_count": max(0, len(row["request"]["source_refs"]) - 8)} for row in pending]
            context = {"round": round_no, "eligible_roles": [r for r in eligible_roles(snapshot) if r in enabled],
                       "knowledge_permissions": {'main': config.main_agent.model_dump(),
                           'specialists': {r: config.agent(r).model_dump() for r in enabled}},
                       "pending_requests": projected, "blocked_request_refs": [],
                       "catalog": catalog, "allowed_source_refs": sorted(visible_refs),
                       "scope_notice": "目录为有界视图；省略引用由程序在执行request_ref时恢复，不能据省略判断资料不存在。",
                       "role_input_refs": {role: [row.observation_id for row in snapshot.observations if role in row.domains][:64]
                                           + ([row.asset_id for row in snapshot.images] if role == "imaging" else []) for role in ROLES},
                       "results": [{"role": role, "result_id": row["result_id"],
                                    "claims": [{**claim, "text": claim["text"][:600],
                                                "excerpt_truncated": len(claim["text"]) > 600} for claim in row["claims"][:8]],
                                    "omitted_claim_count": max(0, len(row["claims"]) - 8),
                                    "uncertainties": row["uncertainties"][:4],
                                    "omitted_uncertainty_count": max(0, len(row['uncertainties']) - 4),
                                    "coverage": {k: len(v) if isinstance(v, list) else v
                                                 for k, v in row.get('coverage', {}).items()}}
                                   for role, row in state["results"].items()],
                       "previous_outcomes": [{"role": role, "status": row["status"],
                                              "request": {k: v for k, v in row["request"].items()
                                                          if k not in {'source_refs'}}, "error": row.get("error")}
                                             for role, rows in state["outcomes"].items() for row in rows[-3:]],
                       "remaining_calls": limits["llm"] - used, "remaining_seconds": store.run(run_id)["deadline"] - time.time()}
            main = main_factory(round_no) if main_factory else MainAgent(ModelGateway(config.model, store, run_id,
                scope="main_decision_" + str(round_no), scope_limit=2, reserved_calls=config.synthesis_reserved_calls))
            decision = main.decide(context)
        if saved is None:
            store.save(run_id, "main_round_plan", round_id, {"decision": decision.model_dump(mode="json"),
                       "round": round_no, "input_fingerprint": fingerprint(state)})
        state["unresolved"].extend(decision.unresolved)
        if decision.stop:
            state["stop_reason"] = decision.stop_reason or "main_stopped"
            break
        affected, new_material = set(), False
        pending_by_id = {row["request_ref"]: row for row in state["pending"]}
        for index, proposal in enumerate(decision.actions):
            if not set(proposal.target_roles) <= set(ROLES):
                raise ValueError("invalid_action_target_role")
            if proposal.request_ref:
                if proposal.request_ref not in pending_by_id:
                    raise ValueError("unknown_pending_request_reference")
                request = RequestContent.model_validate(pending_by_id[proposal.request_ref]["request"])
            else:
                request = proposal.request
            targets = [r for r in proposal.target_roles if r in enabled]
            if not targets:
                continue
            access = config.knowledge_access(targets)
            if request.type == 'knowledge' and not (access.use_kb or access.use_kg):
                continue
            signature = request_signature(request)
            if signature in state["seen"]:
                state["unresolved"].append("重复请求未再次执行：" + request.question)
                continue
            kwargs = {} if access == config.main_agent else {'knowledge_access': access}
            action = tools.execute(request, scope=round_id + "_" + str(index), allow_supplement=proposal.allow_supplement, **kwargs)
            state["seen"].append(signature)
            if action.get("error") == "BudgetExceeded":
                state["stop_reason"] = "tool_budget_exhausted"
            data = action.get("data", {})
            novel = False
            if request.type == "patient_data" and data.get("observations"):
                snapshot, novel = merge_patient_data(snapshot, data)
                if novel:
                    _persist_snapshot(store, run_id, snapshot)
                    tools.snapshot = snapshot
                    tools.patient.snapshot = snapshot
                    tools.allowed_refs.update(row.observation_id for row in snapshot.observations)
                    tools.allowed_refs.update(row.record_id for row in snapshot.records)
            full_data = data
            if request.type in {'patient_data', 'source_context'} and data.get('next_cursor'):
                for target in targets:
                    page_request = request.model_copy(update={'cursor': data['next_cursor']})
                    add_pending(state, page_request, target, [action['action_id'], 'next_page'])
            for role in targets:
                data = scoped_tool_data(full_data, config.agent(role))
                # Knowledge source expansions must be restricted per recipient too.
                if request.type == 'source_context' and 'records' not in data and not (
                    config.agent(role).use_kb and config.agent(role).use_kg):
                    allowed_evidence = state['evidence'].get(role, [])
                    allowed_refs = {ref for e in allowed_evidence for ref in (e['evidence_id'], e['span_id'])}
                    if not set(request.source_refs) <= allowed_refs:
                        continue
                scoped_action = {**action, 'data': data}
                state["outcomes"].setdefault(role, []).append(scoped_action)
                evidence = state["evidence"].setdefault(role, [])
                evidence, evidence_changed = update_evidence(evidence, data.get("evidence", []))
                state['evidence'][role] = evidence
                role_novel = evidence_changed or novel
                if request.type == "source_context" and action["status"] == "found":
                    contexts = state["context"].setdefault(role, [])
                    if fingerprint(data) not in {fingerprint(row) for row in contexts}:
                        contexts.append(data)
                        role_novel = True
                if request.type == "imaging_tool" and data:
                    state.setdefault("images", {})[role] = data
                    role_novel = True
                if request.type == "knowledge":
                    queue_knowledge_gaps(state, data, role, scoped_action, request, snapshot)
                if request.type == "patient_data" and novel:
                    # Conditions must be re-evaluated with newly acquired facts, not stale UNKNOWN evidence.
                    previous_queries = [row for row in state["outcomes"][role] if row["request"]["type"] == "knowledge"
                        and row.get("data", {}).get("kg_release_id") and (row["data"].get("data_requests") or
                            any(item.get("statement_id") for item in row["data"].get("evidence", [])))]
                    if previous_queries:
                        old_query = previous_queries[-1]["request"]
                        refs = [row.observation_id for row in snapshot.observations
                                if role in row.domains or "background" in row.domains]
                        followup = RequestContent.model_validate({**old_query, "source_refs": refs,
                                                                  "reason": "补取患者资料后重新判断知识适用条件"})
                        add_pending(state, followup, role, [action["action_id"], "refresh_applicability"])
                if role_novel:
                    affected.add(role)
                    new_material = True
                if action["status"] not in {"found", "completed"}:
                    state["unresolved"].append(request.question + "：" + action["status"] +
                                               (" / " + action["error"] if action.get("error") else ""))
            data = full_data
            if data.get("evidence"):
                bundle_id = identity("main_bundle", action["action_id"])
                if _last(store, run_id, "retrieval", bundle_id) is None:
                    store.save(run_id, "retrieval", bundle_id, EvidenceBundle(bundle_id=bundle_id,
                        request_id=action["action_id"], status="ok", backend_status=data.get("backend_status", {}),
                        items=[EvidenceItem.model_validate(row) for row in data["evidence"]]))
        proposals = {task.agent_type: task for task in decision.tasks}
        selected = set(proposals) | affected
        if round_no > 0 and not new_material and not proposals:
            state["stop_reason"] = "no_new_information"
            break
        tasks, inputs = [], {}
        background = [row.observation_id for row in snapshot.observations if "background" in row.domains]
        for role in sorted(selected):
            if role not in enabled or role not in eligible_roles(snapshot):
                state["unresolved"].append(role + "：无可用专科资料，未创建任务")
                continue
            proposal = proposals.get(role)
            previous = state["results"].get(role)
            if round_no > 0 and role not in affected:
                # A changed concrete question is useful even without new data; identical questions are not.
                old = state["tasks"].get(role)
                if old and (proposal is None or proposal.questions == old["questions"]):
                    continue
            refs = proposal.input_refs if proposal else [row.observation_id for row in snapshot.observations if role in row.domains]
            refs = list(dict.fromkeys(refs))
            if role == "imaging":
                refs = list(dict.fromkeys(refs + [row.asset_id for row in snapshot.images]))
            previous_task = state['tasks'].get(role, {})
            questions = proposal.questions if proposal else previous_task.get('questions', [
                "依据主Agent新增资料和工具结果更新分析，保留未解决条件"])
            task_id = identity("task", [run_id, role, round_no, snapshot.version, refs, questions,
                                        state["evidence"].get(role, [])])
            task = AgentTask(task_id=task_id, agent_type=role, case_version=snapshot.version,
                input_refs=refs, background_refs=background, questions=questions,
                why=proposal.why if proposal else "主Agent补查后更新相关分析", permitted_tools=[],
                budget=(config.agent(role).task_budget or config.task_budget).model_copy(deep=True))
            from .agent_state import material_hash
            binding = material_hash(task, snapshot, state['evidence'].get(role, []), config.agent(role))
            binding = fingerprint([binding, config.model.model_dump(mode='json'),
                                   state['context'].get(role, []), state.get('images', {}).get(role)])
            if previous and previous.get('material_hash') == binding:
                store.event(run_id, 'task_material_unchanged', {'role': role, 'material_hash': binding})
                continue
            task_id = task.task_id
            tasks.append(task)
            inputs[task_id] = {"evidence": state["evidence"].get(role, []), "previous_result": previous,
                              "additional_context": state["context"].get(role, []),
                              "request_outcomes": state["outcomes"].get(role, []),
                              "image_result": state.get("images", {}).get(role)}
        executor = Executor(store, run_id, max_parallel=config.max_parallel_tasks, model_config=config.model,
                            reserved_calls=config.synthesis_reserved_calls, agent_configs=config.agents)
        for role, handler in handlers.items():
            executor.register(role, handler)
        if tasks:
            plan = TaskPlan(tasks=tasks, mode="model")
            results = asyncio.run(executor.execute_tasks(plan, snapshot, inputs))
            for task, result in zip(tasks, results):
                result.material_hash = fingerprint([
                    material_hash(task, snapshot, state['evidence'].get(task.agent_type, []), config.agent(task.agent_type)),
                    config.model.model_dump(mode='json'), state['context'].get(task.agent_type, []),
                    state.get('images', {}).get(task.agent_type)])
                old = state["results"].get(task.agent_type)
                if old:
                    store.event(run_id, "task_result_superseded", {"role": task.agent_type,
                        "previous_result_id": old["result_id"], "new_result_id": result.result_id})
                state["results"][task.agent_type] = result.model_dump(mode="json")
                state["tasks"][task.agent_type] = task.model_dump(mode="json")
                for index, request in enumerate(result.requests):
                    if request.type == 'knowledge' and not (config.agent(task.agent_type).use_kb or config.agent(task.agent_type).use_kg):
                        continue
                    signature = request_signature(request)
                    request_ref = identity("request", [run_id, task.task_id, index, request.model_dump(mode="json")])
                    row = {"request_ref": request_ref, "role": task.agent_type,
                           "request": request.model_dump(mode="json"), "signature": signature}
                    if signature not in state["seen"]:
                        state["pending"].append(row)
                    else:
                        state["unresolved"].append("已有请求未解决，不重复执行：" + request.question)
        elif round_no == 0:
            state["stop_reason"] = "no_usable_patient_data"
        state["snapshot"] = snapshot.model_dump(mode="json")
        state["round"] = round_no
        if round_no + 1 == config.max_rounds and any(row["signature"] not in state["seen"] for row in state["pending"]):
            state["stop_reason"] = "max_rounds"
        store.save(run_id, "main_round_complete", round_id, state)
    if not state["stop_reason"]:
        state["stop_reason"] = "max_rounds" if state["round"] + 1 >= config.max_rounds else "no_actionable_requests"
    for row in state["pending"]:
        if row["signature"] not in state["seen"]:
            state["unresolved"].append("未执行补查需求：" + row["request"]["question"])
    if not state["results"]:
        from .schemas import Claim
        from .orchestration import scoped_snapshot
        for task in preparation.plan.tasks:
            if task.agent_type not in enabled:
                continue
            subset = scoped_snapshot(snapshot, task)
            result = AgentResult(result_id=identity("result", task.task_id), task_id=task.task_id,
                case_version=task.case_version, status="partial", used_refs=[row.observation_id for row in subset.observations],
                claims=[Claim(claim_id=identity("source_fact", [task.task_id, row.observation_id]),
                    text=row.quote, kind="description", level=task.agent_type, observation_ids=[row.observation_id],
                    strength="descriptive") for row in subset.observations if row.quote.strip()],
                uncertainties=["预算或时限不足，未完成专科模型分析，仅保留来源事实"])
            if _last(store, run_id, "task", task.task_id) is None:
                store.save(run_id, "task", task.task_id, task)
            if _last(store, run_id, "result", result.result_id) is None:
                store.save(run_id, "result", result.result_id, result)
            state["tasks"][task.agent_type] = task.model_dump(mode="json")
            state["results"][task.agent_type] = result.model_dump(mode="json")
    state["snapshot"] = snapshot.model_dump(mode="json")
    store.save(run_id, "main_loop_outcome", "main", state)
    return state
