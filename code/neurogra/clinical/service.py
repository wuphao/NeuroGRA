"""S01-S08 foundation entry, intentionally stops at a validated task plan."""
from .config import ClinicalConfig
from .schemas import Contract, CaseSnapshot, TaskPlan, EvidenceBundle, Issue, IntakeResult, ParseResult
from .storage import RunStore
from .llm import ModelGateway
from .intake import ingest_patient
from .parsing import parse_attachment, inspect_image
from .profiling import profile_case
from .orchestration import MainAgent
from .retrieval import KnowledgeService, pin_release
from .utils import write_json


class PreparationResult(Contract):
    run_id: str
    status: str
    snapshot: CaseSnapshot
    plan: TaskPlan
    knowledge_release_id: str | None
    evidence: EvidenceBundle | None = None
    issues: list[Issue]
    artifact_path: str


def create_context(config, *, store=None, on_created=None):
    store = store if store is not None else RunStore(config.resolve(config.database))
    run_id = store.create_run(config.model_dump(mode="json"), config.budget.model_dump())
    if on_created is not None:
        on_created(run_id)
    from .execution import execution_binding
    store.save(run_id, 'execution_binding', 'main', execution_binding())
    return store, run_id


def prepare_patient(raw: dict, config: ClinicalConfig, use_model=True, query=None) -> PreparationResult:
    store, run_id = create_context(config)
    with store.workflow_lock(run_id):
        if config.diagnosis_task_mode == 'blinded_classification':
            from .label_masking import mask_labels
            raw, masking = mask_labels(raw)
            store.save(run_id, 'label_masking', 'main', masking)
        store.save(run_id, 'patient_input', 'main', {'raw': raw, 'use_model': use_model, 'query': query})
        return prepare_run(raw, config, store, run_id, use_model, query)


def prepare_run(raw, config, store, run_id, use_model=True, query=None):
    """Resume committed preparation phases without renewing the run budget."""
    if config.diagnosis_task_mode == 'blinded_classification':
        from .label_masking import mask_labels
        raw, masking = mask_labels(raw)
        previous = store.objects(run_id, 'label_masking')
        if not previous:
            store.save(run_id, 'label_masking', 'main', masking)
            inputs = store.objects(run_id, 'patient_input')
            if inputs:
                latest_input = inputs[-1]
                version = latest_input['version']
                store.save(run_id, 'patient_input', latest_input['object_id'],
                    {**latest_input['payload'], 'raw': raw}, version + 1, version)
    def saved(kind):
        rows = store.objects(run_id, kind)
        return rows[-1]['payload'] if rows else None
    gateway = ModelGateway(config.model, store, run_id, reserved_calls=config.synthesis_reserved_calls) if use_model else None
    try:
        issues = []
        source = saved('rwe_source')
        if source:
            issues.extend(Issue(code='rwe_form_failed', stage='intake',
                message=f"RWE表单 {item['form_id']} 读取失败：{item['code']}，不能视为该患者未检查。")
                for item in source['issues'])
        release_id, evidence = None, None
        binding = saved('knowledge_binding')
        if binding is None:
            try:
                release_id = pin_release(config) if config.knowledge_enabled('kg') else None
                binding = {'release_id': release_id, 'error': None}
            except Exception as exc:
                binding = {'release_id': None, 'error': type(exc).__name__}
            store.save(run_id, 'knowledge_binding', 'main', binding)
        release_id = binding['release_id']
        if release_id:
            store.event(run_id, 'knowledge_pinned', {'release_id': release_id})
            KnowledgeService(config, release_id, store, run_id)  # pin vector manifest before the first task
        elif config.knowledge_enabled('kg'):
            issues.append(Issue(code='knowledge_unavailable', stage='preparation', message=binding['error']))
        store.status(run_id, 'profiling')
        cached = saved('intake')
        if cached:
            intake = IntakeResult.model_validate(cached)
        else:
            intake = ingest_patient(raw, config)
            write_json(config.resolve(config.data_root) / 'cases' / intake.raw_input_hash / 'input.json', intake.raw_snapshot)
            store.save(run_id, 'intake', intake.raw_input_hash, intake)
        cached = saved('snapshot')
        if cached:
            snapshot = CaseSnapshot.model_validate(cached)
        else:
            parsed = []
            parse_cache = {r['object_id']: r['payload'] for r in store.objects(run_id, 'parse')}
            for attachment in intake.attachments:
                if attachment.attachment_id in parse_cache:
                    result = ParseResult.model_validate(parse_cache[attachment.attachment_id])
                else:
                    result = parse_attachment(attachment, config)
                    store.save(run_id, 'parse', result.attachment_id, result)
                parsed.append(result)
            intake.images = [inspect_image(i, config) for i in intake.images]
            # Structured RWE fields already have exact values and locators. Avoid
            # re-extracting hundreds of atomic values with an LLM; agents analyze them.
            snapshot = profile_case(intake, parsed, config,
                                    None if source or intake.patient_metadata.get('format') == 'rwe_export' else gateway)
            store.save(run_id, 'snapshot', snapshot.case_id, snapshot)
        write_json(config.resolve(config.data_root) / 'cases' / intake.raw_input_hash / run_id / 'snapshot.json', snapshot)
        store.status(run_id, 'planning')
        cached = saved('plan')
        plan = TaskPlan.model_validate(cached) if cached else MainAgent(gateway, config.task_budget, config.enabled_roles()).plan(snapshot)
        if cached is None:
            store.save(run_id, 'plan', 'main', plan)
        issues.extend(snapshot.issues + plan.issues)
        if query:
            from .schemas import RequestContent, EvidenceItem
            from .main_tools import MainToolExecutor
            from .utils import identity
            previous = [r['payload'] for r in store.objects(run_id, 'retrieval') if r['payload']['request_id'] == 'foundation_query']
            if previous:
                evidence = EvidenceBundle.model_validate(previous[-1])
            else:
                tools = MainToolExecutor(snapshot, config, store, run_id, graph_release_id=release_id)
                action = tools.execute(RequestContent(type='knowledge', question=query,
                    reason='主Agent准备阶段的用户查询', source_refs=[row.observation_id for row in snapshot.observations]),
                    scope='foundation_query')
                data = action.get('data', {})
                evidence = EvidenceBundle(bundle_id=identity('bundle', [run_id, action['action_id']]),
                    request_id='foundation_query', status='ok' if data.get('evidence') else 'empty',
                    items=[EvidenceItem.model_validate(row) for row in data.get('evidence', [])],
                    backend_status=data.get('backend_status', {}), retrieval_metadata=data)
                store.save(run_id, 'retrieval', evidence.bundle_id, evidence)
            if evidence.status in {"degraded", "failed"}:
                issues.append(Issue(code="retrieval_" + evidence.status, stage="preparation",
                                    message=",".join(k + ":" + v for k, v in evidence.backend_status.items())))
        path = config.resolve(config.output_root) / run_id / "preparation.json"
        result = PreparationResult(run_id=run_id, status="prepared" if not issues else "prepared_with_limitations",
                                   snapshot=snapshot, plan=plan, knowledge_release_id=release_id,
                                   evidence=evidence, issues=issues, artifact_path=str(path))
        write_json(path, result)
        store.status(run_id, result.status)
        return result
    except Exception as exc:
        store.status(run_id, "failed")
        store.event(run_id, "failed", {"error_type": type(exc).__name__})
        raise
