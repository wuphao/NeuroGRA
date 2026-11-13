"""Real-model, evidence-selection collaboration ablation on synthetic records.

These are controlled experimental adapters, not a clinical evaluation or a
performance measurement of the production PatientChat endpoint. B1 is absent:
the historical patient-agent source has no reproducible tracked snapshot.
"""
import argparse
import json
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from neurogra.clinical.generation import local_model
from neurogra.clinical.llm import ModelGateway
from neurogra.clinical.storage import RunStore
from neurogra.platform.configuration import resolve_config


DOMAINS = ['history', 'cognition', 'laboratory', 'imaging']


class Plan(BaseModel):
    domains: list[Literal['history', 'cognition', 'laboratory', 'imaging']] = Field(max_length=4)


class Selection(BaseModel):
    source_ids: list[str] = Field(max_length=8)


def cases():
    subjects = [(['history'], '读取合成病史中的记忆症状'),
                (['cognition'], '读取合成认知量表总分'),
                (['laboratory'], '读取合成检验记录'),
                (['imaging'], '读取合成影像报告文本'),
                (['history', 'cognition'], '同时读取病史和认知量表'),
                (['laboratory', 'imaging'], '同时读取检验和影像报告文本'),
                (['history', 'laboratory'], '同时读取病史和检验'),
                (['cognition', 'imaging'], '同时读取认知量表和影像文本'),
                (DOMAINS, '分别读取病史、认知、检验、影像四类资料'),
                (['history', 'imaging'], '读取病史与影像，不读取检验'),
                (['cognition', 'laboratory'], '读取认知和检验，不读取病史'),
                (['history', 'cognition', 'imaging'], '读取病史、认知与影像，不读取检验')]
    texts = ['合成患者自述记忆下降，病因不明。', '合成认知量表总分为 22，不能据此确定诊断。',
             '合成检验值为 12，未提供单位与参考范围。', '合成影像报告文本：未提供原始图像，不能视为已阅片。']
    sources = [{'id': f's{i}', 'domain': domain, 'text': texts[i], 'version': 'synthetic-v1'}
               for i, domain in enumerate(DOMAINS)]
    return [{'id': f'C{i + 1:02d}', 'question': question, 'sources': sources,
             'expected_ids': [row['id'] for row in sources if row['domain'] in domains]}
            for i, (domains, question) in enumerate(subjects)]


def run(config, root, case, method, repetition):
    store = RunStore(root / 'benchmark.sqlite')
    run_id = store.create_run({'purpose': 'synthetic_collaboration_benchmark', 'project_root': str(root),
                               'method': method, 'case': case['id'], 'repetition': repetition},
                              {'seconds': 120, 'llm': 10})
    model = ModelGateway(config.model, store, run_id)
    started = time.monotonic()
    row = {'case': case['id'], 'method': method, 'repetition': repetition, 'run_id': run_id}
    valid = {source['id'] for source in case['sources']}
    invalid_ids = []

    def select(instruction, data):
        answer = model.generate_structured(instruction, data, Selection)
        invalid_ids.extend(ref for ref in answer.source_ids if ref not in valid)
        return set(answer.source_ids) & valid

    try:
        if method == 'B0':
            selected = select('你是单智能体。只选择回答问题所需的合成资料 ID，不生成诊断或新事实。',
                              {'question': case['question'], 'sources': case['sources']})
            domains = ['general']
        else:
            if method == 'B4':
                domains = DOMAINS
            else:
                plan = model.generate_structured('按问题选择需要的领域；只可使用允许领域，不要选择明确排除的领域。',
                    {'question': case['question'], 'allowed_domains': DOMAINS,
                     'catalog': [{'id': source['id'], 'domain': source['domain']} for source in case['sources']]}, Plan)
                if len(set(plan.domains)) != len(plan.domains) or set(plan.domains) - set(DOMAINS):
                    raise ValueError('invalid_domain_plan')
                domains = plan.domains
            selected = set()
            for domain in domains:
                allowed = [source for source in case['sources'] if source['domain'] == domain]
                selection = select('你只处理当前领域。选择问题确实要求读取的资料 ID；问题未要求或明确排除时返回空列表。',
                                   {'question': case['question'], 'domain': domain, 'sources': allowed})
                if selection - {source['id'] for source in allowed}:
                    raise ValueError('worker_source_scope_violation')
                selected.update(selection)
            if method == 'B3':
                selected = select('审阅候选来源是否完整符合问题，排除被否定领域；只返回最终需要的来源 ID。'
                                  '可以针对遗漏从给定合成资料中补选，不能生成新事实。',
                    {'question': case['question'], 'candidates': sorted(selected), 'sources': case['sources']})
        expected = set(case['expected_ids'])
        row.update(status='completed', source_ids=sorted(selected), expected_ids=sorted(expected),
                   exact_match=selected == expected, missing_ids=sorted(expected - selected),
                   extra_ids=sorted(selected - expected), unknown_ids=invalid_ids, domains=domains)
        store.status(run_id, 'completed')
    except Exception as error:
        row.update(status='failed', exact_match=False, error=type(error).__name__, detail=str(error)[:300])
        store.mark_interrupted(run_id)
        store.status(run_id, 'failed')
    summary = store.summary(run_id)
    row.update(seconds=round(time.monotonic() - started, 3),
               llm_attempts=sum(item['n'] for item in summary['calls'] if item['kind'] == 'llm'))
    return row


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--limit', type=int, default=12, choices=range(1, 13))
    parser.add_argument('--repeats', type=int, default=2, choices=[1, 2])
    args = parser.parse_args()
    root = Path(args.root).resolve()
    allowed = Path(__file__).resolve().parent / 'runs'
    if not root.is_relative_to(allowed) or root == allowed:
        raise ValueError('isolated_testing_root_required')
    if (root / 'benchmark.json').exists() or (root / 'benchmark.sqlite').exists():
        raise ValueError('benchmark_requires_new_output_directory')
    root.mkdir(parents=True, exist_ok=True)
    config = resolve_config(root=root, profile='patient-only')
    if not local_model(config.model):
        raise ValueError('local_model_required')
    rows = []
    output = root / 'benchmark.json'
    for repetition in range(args.repeats):
        for case in cases()[:args.limit]:
            # Rotate order so the same adapter is not always first.
            methods = ['B0', 'B2', 'B3', 'B4']
            methods = methods[repetition:] + methods[:repetition]
            for method in methods:
                row = run(config, root, case, method, repetition)
                rows.append(row)
                output.write_text(json.dumps({'scope': __doc__, 'model': config.model.model,
                    'knowledge_version': 'synthetic-v1', 'B1': 'unavailable_historical_snapshot',
                    'expected_runs': args.limit * args.repeats * 4, 'records': rows},
                    ensure_ascii=False, indent=2), encoding='utf-8')
                print(json.dumps({key: row[key] for key in ('case', 'method', 'status', 'exact_match', 'seconds')},
                                 ensure_ascii=False), flush=True)
    return any(row['status'] == 'failed' for row in rows)


if __name__ == '__main__':
    raise SystemExit(main())
