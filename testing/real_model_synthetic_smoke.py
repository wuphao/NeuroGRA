"""Exercise real local model orchestration with synthetic RWE and knowledge only.

This is an engineering smoke test, not a medical or retrieval-quality evaluation.
It deliberately refuses non-local model configuration and stores only synthetic data.
"""

import argparse
import json
import time
import traceback
from contextlib import ExitStack, closing
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from neurogra_kb import TextKnowledgeService
from neurogra_kg.adapters.graph import MemoryGraphStore
from neurogra_kg.adapters.ollama_embedding import OllamaEmbeddingAdapter
from neurogra_kg.config import GraphConfig
from neurogra_kg.construction.parsing import DocumentParser
from neurogra_kg.contracts.domain import DocumentInput, Entity, KnowledgeStatement, SourceRef
from neurogra_kg.runtime import Budget
from neurogra_kg.storage.registry import Registry
from neurogra_kg.storage.releases import create_release
from neurogra.clinical.generation import local_model
from neurogra.clinical.schemas import AgentResult, Claim, EvidenceItem
from neurogra.clinical.storage import RunStore
from neurogra.platform.application import Application
from neurogra.platform.configuration import resolve_config
from neurogra.platform.evochat_store import EvoChatStore
from neurogra.platform.patient_analysis import PatientAnalysis
from neurogra.platform.patient_chat import PatientChat
from neurogra.platform.patient_workspace import PatientWorkspace

from neurogra_ui_fixture import memory, rwe_query


class SyntheticReader:
    def query(self, path, body):
        data = rwe_query(None, path, body)
        if path == '/form/getFormDetailsBatch' and '55' in data:
            data['55']['fields'].append({'enName': 'memory', 'cnName': '记忆症状'})
        if path == '/form/queryDataBatch' and '55' in data:
            data['55']['list'] = [{**row, 'memory': '合成记录：近一年自述记忆下降'}
                                  for row in data['55']['list']]
        return data


def publish_synthetic_graph(config, root):
    text = '合成图谱规则：MoCA 认知量表与记忆症状应结合病史进一步核查，不能据此确定诊断。'
    source = root / 'synthetic-graph.json'
    source.write_text(json.dumps({'blocks': [{'kind': 'paragraph', 'page': 1, 'text': text}]},
                                 ensure_ascii=False), encoding='utf-8')
    kg = config.kg
    data_root = kg.resolve(kg.paths.data_root)
    document = DocumentParser(data_root).parse(DocumentInput(path=source, title='合成图谱文档', language='zh'))
    block = next(row for row in document.blocks if row.text == text)
    ref = SourceRef(block_id=block.block_id, start=0, end=len(text), quote=text)
    statement = KnowledgeStatement(statement_id='synthetic-graph-statement',
        subject=Entity(entity_id='synthetic-memory', name='记忆症状', entity_type='phenotype'),
        predicate='associated_with', object=Entity(entity_id='synthetic-cognition', name='认知评估',
                                                    entity_type='examination'),
        assertion_text=text, assertion_language='zh', condition_status='explicit_unrestricted',
        status='approved', purposes=['diagnostic_assessment', 'cognitive_assessment'], source_refs=[ref])
    embedding = OllamaEmbeddingAdapter(kg.embedding, kg.resolve(kg.paths.output_root))
    with closing(Registry(data_root / 'registry.sqlite')) as registry:
        return create_release(data_root, registry, 'synthetic-graph-build', [statement], [document],
                              embedding, MemoryGraphStore(), Budget(120))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--seeded-interpretations', action='store_true',
                      help='Use deterministic synthetic specialist claims to exercise the real reviewer')
    mode.add_argument('--conditional-fixture', action='store_true',
                      help='Give real specialists a synthetic rule with synthetic applicability')
    mode.add_argument('--real-local-kb', action='store_true',
                      help='Publish and retrieve an isolated synthetic local knowledge base')
    parser.add_argument('--kb-vector', action='store_true',
                        help='Build the synthetic local KB with the configured local embedding model')
    parser.add_argument('--real-local-kg', action='store_true',
                        help='Add a synthetic memory-graph release and real local KG retrieval')
    args = parser.parse_args()
    if args.kb_vector and not args.real_local_kb:
        raise ValueError('--kb-vector requires --real-local-kb')
    if args.real_local_kg and not args.real_local_kb:
        raise ValueError('--real-local-kg requires --real-local-kb')
    root = Path(args.root).resolve()
    allowed = (Path(__file__).resolve().parent / 'runs').resolve()
    if root == allowed or not root.is_relative_to(allowed):
        raise ValueError('root must be a dedicated directory under testing/runs')
    root.mkdir(parents=True, exist_ok=True)
    config = resolve_config(root=root, profile='standard' if args.real_local_kg else 'kb-only')
    if not local_model(config.model):
        raise ValueError('this synthetic smoke test requires a local model')
    if args.real_local_kb:
        embedding = config.kb.embedding
        if (urlsplit(embedding.base_url).hostname not in {'127.0.0.1', 'localhost', '::1'} or
                embedding.model.lower().endswith(('-cloud', ':cloud'))):
            raise ValueError('the synthetic KB requires a local embedding model')
    if args.real_local_kg:
        for model in (config.kg.embedding, config.kg.llm):
            if (urlsplit(model.base_url).hostname not in {'127.0.0.1', 'localhost', '::1'} or
                    model.model.lower().endswith(('-cloud', ':cloud'))):
                raise ValueError('the synthetic KG requires local models')
        config = config.model_copy(update={'kg': config.kg.model_copy(update={'graph': GraphConfig(provider='memory')})})
    app = Application(root, config)
    workspace = PatientWorkspace(config, reader_factory=lambda *_: SyntheticReader())
    with patch('neurogra.platform.patient_workspace.obtain_token', return_value='synthetic-token'):
        cookie = workspace.login('synthetic-doctor', 'synthetic-only')
    session = workspace.session(cookie)
    chat = PatientChat(app, workspace, PatientAnalysis(app, workspace, starter=lambda _: None))
    sid = chat.create(session, '11')['id']
    body = {'request_id': 'real-model-synthetic-1',
            'message': '请结合患者病史和 MoCA 认知结果分别分析，并说明资料不足的地方。',
            'capabilities': ['async_turns_v1', 'collaboration_progress_v1']}
    if args.conditional_fixture:
        body['message'] = '请结合患者病史与 MoCA 认知结果，根据给定规则说明记忆症状可能提示什么和资料局限。'
    evidence = {'evidence_id': 'synthetic-evidence-1', 'document_id': 'synthetic-document-1',
                'span_id': 'synthetic-span-1',
                'title': '合成知识文本，仅供工程测试', 'version': 'synthetic-v1',
                'quote': '合成规则：记录中的记忆症状可提示进一步核查，不能单凭记录确定原因。',
                'applicability': 'unknown', 'allowed_use': 'background', 'matched_by': ['kb_lexical']}
    if args.conditional_fixture:
        evidence.update(applicability='applicable', allowed_use='conditional_support',
                        limitations=['仅为合成规则及合成适用条件，不能用于实际患者。'])
    evidence = EvidenceItem.model_validate(evidence).model_dump(mode='json')
    report = {'model': config.model.model, 'model_endpoint': config.model.base_url,
              'question': body['message'], 'data': 'synthetic RWE and synthetic knowledge',
              'actual_components': 'local model, planner, scheduler and chat',
              'substituted_components': 'RWE identity/data, knowledge retrieval and chat memory',
              'clinical_evaluation': False}
    if args.seeded_interpretations:
        report['substituted_components'] += ', specialist outputs'
    else:
        report['actual_components'] += ', specialist handlers'
    if args.conditional_fixture:
        report['substituted_components'] += ', synthetic knowledge applicability'
    if args.real_local_kb:
        source = root / 'synthetic-knowledge.json'
        source.write_text(json.dumps({'blocks': [{'kind': 'paragraph', 'page': 1,
            'text': '合成规则：MoCA 认知量表和记忆症状应结合病史进一步核查，此内容仅供工程测试。'}]},
            ensure_ascii=False), encoding='utf-8')
        release = TextKnowledgeService(config.kb).publish([source], vector=args.kb_vector)
        report['synthetic_kb'] = {'release_id': release['release_id'], 'vector': release['vector']}
        report['actual_components'] += ', local KB publication and retrieval'
        report['substituted_components'] = 'RWE identity/data, knowledge content and chat memory'
    if args.real_local_kg:
        release = publish_synthetic_graph(config, root)
        report['synthetic_kg'] = {'release_id': release['release_id'], 'graph_provider': 'memory',
                                  'statement_construction': 'manually seeded source-linked test statement'}
        report['actual_components'] += ', KG release and local retrieval'

    async def seeded_specialists(_executor, task_plan, snapshot, **_kwargs):
        results = []
        for task in task_plan.tasks:
            rows = [item for item in snapshot.observations if item.observation_id in task.input_refs]
            row = next((item for item in rows if item.name == '记忆症状'), rows[0])
            claim = Claim(claim_id='synthetic-claim-' + task.task_id, kind='interpretation',
                          level=task.agent_type, observation_ids=[row.observation_id],
                          evidence_ids=[evidence['evidence_id']], strength='conditional',
                          text='当前记录中的记忆症状需要结合其他资料进一步核查，不能据此确定原因。',
                          rationale='测试用的患者记录及合成规则均提到记忆症状。',
                          applicability='仅为合成条件，适用性待核实。', limitations=['合成资料仅用于工程测试。'])
            results.append(AgentResult(result_id='synthetic-result-' + task.task_id, task_id=task.task_id,
                                       case_version=snapshot.version, status='completed', claims=[claim]))
        return results

    started = time.monotonic()
    try:
        with ExitStack() as stack:
            stack.enter_context(patch.object(EvoChatStore, 'memory', memory))
            tools = None
            if not args.real_local_kb:
                stack.enter_context(patch('neurogra.platform.patient_agents.pin_release', return_value=None))
                tools = stack.enter_context(patch('neurogra.platform.patient_agents.MainToolExecutor'))
                tools.return_value.execute.return_value = {'status': 'found', 'data': {
                    'evidence': [evidence], 'backend_status': {'synthetic_kb': 'ok'}}}
            if args.seeded_interpretations:
                stack.enter_context(patch('neurogra.platform.patient_agents.Executor.execute_tasks', seeded_specialists))
            reply = chat.send(session, '11', sid, body, lambda: None)['reply']
        binding = chat.store.database().collaboration_binding(sid, body['request_id'])
        clinical_config = app.configuration('standard')
        runs = RunStore(clinical_config.resolve(clinical_config.database))
        report.update(reply=reply, run_id=binding['run_id'] if binding else None,
                      budget=runs.summary(binding['run_id']) if binding else None)
        report['knowledge_queries'] = (tools.return_value.execute.call_count if tools else
                                       sum(row['n'] for row in report['budget']['calls']
                                           if row['kind'] == 'retrieval'))
        report['observed_roles'] = [item.get('role') for item in reply.get('collaboration', [])]
        record_texts = {record.get('text') for source in reply.get('sources', [])
                        for record in source.get('records', [])}
        report['has_synthetic_patient_fact'] = bool({'22', '合成记录：近一年自述记忆下降'} & record_texts)
        report['review_invoked'] = any(event['type'] == 'llm_completed' and
                                       event['payload'].get('schema') == 'ClaimReview'
                                       for event in report['budget']['events'])
        report['synthetic_kb_source_found'] = any('此内容仅供工程测试' in source.get('text', '') and
                                                  source.get('version') for source in reply.get('sources', []))
        if args.real_local_kb:
            checkpoints = runs.objects(binding['run_id'], 'dialogue_checkpoint')
            backend = checkpoints[-1]['payload'].get('backend', {}) if checkpoints else {}
            report['knowledge_backends'] = backend
            for name in ('lexical', 'vector'):
                states = [value for key, value in backend.items() if key.endswith('kb_' + name)]
                report['kb_' + name + '_ok'] = bool(states) and all(value == 'ok' for value in states)
        if args.real_local_kg:
            report['synthetic_kg_source_found'] = any('合成图谱规则' in source.get('text', '') and
                                                      source.get('version') for source in reply.get('sources', []))
            for name in ('lexical', 'vector', 'graph'):
                states = [value for key, value in backend.items() if key.endswith('kg_' + name)]
                report['kg_' + name + '_ok'] = bool(states) and all(value == 'ok' for value in states)
        role_states = {item.get('role'): item.get('status') for item in reply.get('collaboration', [])}
        report['engineering_pass'] = (report['has_synthetic_patient_fact'] and
                                      all(role_states.get(role) in {'completed', 'partial'}
                                          for role in ('history', 'cognition')) and
                                      (not args.real_local_kb or (report['synthetic_kb_source_found'] and
                                                                  report['kb_lexical_ok'])) and
                                      (not args.kb_vector or report.get('kb_vector_ok')) and
                                      (not args.real_local_kg or
                                       (report['synthetic_kg_source_found'] and all(
                                           report['kg_' + name + '_ok'] for name in ('lexical', 'vector', 'graph')))) and
                                      (not (args.seeded_interpretations or args.conditional_fixture) or
                                       report['review_invoked']))
    except Exception as error:
        report.update(error_type=type(error).__name__, error=str(error), traceback=traceback.format_exc())
    report['elapsed_seconds'] = round(time.monotonic() - started, 2)
    path = root / 'real-model-smoke.json'
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'report': str(path), 'elapsed_seconds': report['elapsed_seconds'],
                      'error_type': report.get('error_type'), 'roles': report.get('observed_roles'),
                      'has_synthetic_patient_fact': report.get('has_synthetic_patient_fact'),
                      'review_invoked': report.get('review_invoked'),
                      'synthetic_kb_source_found': report.get('synthetic_kb_source_found'),
                      'kb_lexical_ok': report.get('kb_lexical_ok'),
                      'kb_vector_ok': report.get('kb_vector_ok'),
                      'synthetic_kg_source_found': report.get('synthetic_kg_source_found'),
                      'kg_lexical_ok': report.get('kg_lexical_ok'), 'kg_vector_ok': report.get('kg_vector_ok'),
                      'kg_graph_ok': report.get('kg_graph_ok'),
                      'result_status': report.get('reply', {}).get('result_status'),
                      'engineering_pass': report.get('engineering_pass', False)}, ensure_ascii=False), flush=True)
    return 0 if report.get('engineering_pass') else 1


if __name__ == '__main__':
    raise SystemExit(main())
