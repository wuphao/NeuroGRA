"""Deterministic S14-S16 audit, explicitly separate from clinical correctness."""
import json
from .schemas import AgentResult, CaseSnapshot, FinalReport
from .storage import RunStore
from .utils import write_json


def audit_run(config, run_id, require_all_agents=False):
    store = RunStore(config.resolve(config.database))
    def last(kind):
        rows = store.objects(run_id, kind)
        if not rows:
            raise ValueError('missing_' + kind)
        return rows[-1]['payload']
    outcome = last('run_result')
    report = FinalReport.model_validate(outcome['report'])
    snapshot = CaseSnapshot.model_validate(last('snapshot'))
    tasks = last('plan')['tasks']
    results = {r.task_id: r for r in report.agent_results}
    all_versions = {(r['object_id'], r['version']): AgentResult.model_validate(r['payload'])
                    for r in store.objects(run_id, 'result')}
    latest_versions = {}
    for result_id, version in all_versions:
        latest_versions[result_id] = max(latest_versions.get(result_id, 0), version)
    observations = {o.observation_id: o for o in snapshot.observations}
    evidence = {e['evidence_id']: e for r in store.objects(run_id, 'retrieval') for e in r['payload']['items']}
    tools = {r['object_id']: r['payload'] for r in store.objects(run_id, 'diamond_result')}
    checks, errors = [], []
    def check(name, ok):
        checks.append({'check': name, 'passed': bool(ok)})
        if not ok: errors.append(name)
    expected = {t['task_id'] for t in tasks}
    check('all_planned_tasks_have_results', set(results) == expected)
    roles = {t['agent_type'] for t in tasks}
    if require_all_agents:
        check('all_four_roles_executed', roles == {'history', 'cognition', 'laboratory', 'imaging'})
    check('report_identity', report.run_id == run_id and report.patient_id == snapshot.patient_id)
    for task in tasks:
        result = results.get(task['task_id'])
        if result is None: continue
        allowed = set(task['input_refs'] + task['background_refs'])
        check('result_binding:' + task['agent_type'], result.case_version == snapshot.version and
              latest_versions.get(result.result_id) == result.version)
        check('agent_observation_scope:' + task['agent_type'], all(set(c.observation_ids) <= allowed for c in result.claims))
        check('model_results_real:' + task['agent_type'], all(i in tools and tools[i]['status'] == 'completed' for i in result.model_results))
    for claim in report.claims:
        check('claim_observations:' + claim.claim_id, bool(claim.observation_ids) and set(claim.observation_ids) <= observations.keys())
        check('claim_evidence:' + claim.claim_id, set(claim.evidence_ids) <= evidence.keys())
        deps = report.claim_dependencies.get(claim.claim_id, [])
        valid_deps = bool(deps)
        for dep in deps:
            result = results.get(dep.task_id)
            valid_deps &= bool(result and result.result_id == dep.result_id and result.version == dep.result_version
                and any(c.claim_id == dep.claim_id and c.observation_ids == claim.observation_ids for c in result.claims))
        check('latest_claim_dependencies:' + claim.claim_id, valid_deps)
        if claim.kind == 'description' and set(claim.observation_ids) <= observations.keys():
            check('description_is_source_text:' + claim.claim_id,
                  claim.text == '；'.join(dict.fromkeys(observations[o].quote for o in claim.observation_ids)))
        if claim.kind == 'interpretation':
            check('bounded_interpretation:' + claim.claim_id,
                bool(claim.evidence_ids) and claim.strength in {'tentative', 'conditional'} and bool(claim.limitations))
    check('citations_exact', set(report.citations) == {eid for c in report.claims for eid in c.evidence_ids})
    for tool_id, tool in tools.items():
        if tool['status'] != 'completed':
            check('failed_tool_has_no_prediction:' + tool_id, tool['prediction'] is None and not tool['scores'])
        check('tool_patient_binding:' + tool_id, tool['patient_id'] == snapshot.patient_id and tool['case_version'] == snapshot.version)
    requests = {r['object_id']: r['payload'] for r in store.objects(run_id, 'review_request')}
    for row in store.objects(run_id, 'review_response'):
        response = row['payload']; request = requests.get(response['request_id'])
        check('review_request_binding:' + row['object_id'], request is not None)
        revised = response.get('revised_result')
        if request and revised:
            check('review_version:' + row['object_id'], revised['result_id'] == request['target_result_id']
                and revised['version'] == request['target_result_version'] + 1)
    summary = store.summary(run_id)
    limits = json.loads(store.run(run_id)['limits'])
    check('global_budget_preserved', all(sum(c['n'] for c in summary['calls'] if c['kind'] == k) <= v for k,v in limits.items()))
    from pathlib import Path
    check('reports_exist', all(Path(p).is_file() for p in outcome['report_paths'].values()))
    result = {'run_id': run_id, 'protocol_passed': not errors, 'roles': sorted(roles),
        'checks': checks, 'errors': errors, 'calls': summary['calls'],
        'not_validated': ['临床诊断准确率和知识适用性不由本协议验收证明',
                          'DiaMond真实分类一致性仍受S12验证门控，不以协议替身替代验收']}
    path = config.resolve(config.output_root) / run_id / 'acceptance.json'
    write_json(path, result)
    return path, result
