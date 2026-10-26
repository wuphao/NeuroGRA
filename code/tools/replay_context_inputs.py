"""Replay persisted task inputs through the context builder without model calls."""
import argparse
import json
from pathlib import Path
import runpy
import sqlite3
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'code'))
from neurogra.clinical.context import ContextBuilder
from neurogra.clinical.context_budget import measure, messages_for
from neurogra.clinical.history import GroundedDraft, HistoryAgent
from neurogra.clinical.specialists import CognitionAgent, LaboratoryAgent
from neurogra.clinical.imaging import ImagingAgent
from neurogra.clinical.orchestration import AnalysisRuntime
from neurogra.clinical.schemas import AgentTask, CaseSnapshot, EvidenceItem, AgentResult
from neurogra.clinical.orchestration import TaskContext
from neurogra.clinical.utils import dumps


def replay(run_id, output):
    config = runpy.run_path(str(ROOT / 'code/main.py'))['build_config']()
    database = config.resolve(config.database)
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as connection:
        rows = connection.execute('SELECT payload FROM objects WHERE run_id=? AND kind=?',
                                  (run_id, 'task_input')).fetchall()
    if not rows:
        raise ValueError('task_inputs_not_found')
    results = []
    for (payload,) in rows:
        data = json.loads(payload)['input']
        task = AgentTask.model_validate(data['task'])
        context = TaskContext(CaseSnapshot.model_validate(data['snapshot']), SimpleNamespace(),
            evidence=[EvidenceItem.model_validate(e) for e in data.get('evidence', [])],
            request_outcomes=data.get('request_outcomes', []),
            additional_context=data.get('additional_context', []),
            previous_result=AgentResult.model_validate(data['previous_result']) if data.get('previous_result') else None)
        handler = {'history': HistoryAgent, 'cognition': CognitionAgent,
                   'laboratory': LaboratoryAgent, 'imaging': ImagingAgent}[task.agent_type]()
        runtime_view = SimpleNamespace(knowledge_access=config.agent(task.agent_type))
        system = AnalysisRuntime.prepare_system(runtime_view, handler.batch_prompt())
        batches = list(ContextBuilder(config.model).iter_batches(task, context, system, GroundedDraft))
        metrics, failures, covered = [], [], []
        for batch, prepared in batches:
            if isinstance(prepared, Exception):
                failures.append(str(prepared))
            else:
                view = prepared[0]
                metrics.append(measure(messages_for(system, view), GroundedDraft, config.model))
                covered.extend(o.observation_id for o in batch)
        results.append({'role': task.agent_type, 'task_id': task.task_id,
            'original_outcome_chars': len(dumps(context.request_outcomes)),
            'observations': len(context.snapshot.observations), 'selected_across_batches': len(set(covered)),
            'batches': len(batches), 'max_business_chars': max((m['business_chars'] for m in metrics), default=0),
            'max_estimated_input_tokens': max((m['estimated_input_tokens'] for m in metrics), default=0),
            'all_fit': all(m['fits'] for m in metrics) and not failures, 'failures': failures})
    result = {'source_run_id': run_id, 'model_calls': 0, 'tasks': results}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--output', type=Path, default=ROOT / 'output/clinical/context_replay.json')
    args = parser.parse_args()
    replay(args.run_id, args.output)
