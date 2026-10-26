"""Explicit execution compatibility for historical runs."""

from pathlib import Path
from .utils import fingerprint, sha256_file


def execution_binding():
    root = Path(__file__).resolve().parent
    names = ('iteration.py', 'orchestration.py', 'history.py', 'specialists.py', 'imaging.py',
             'main_tools.py', 'patient_data.py', 'schemas.py', 'llm.py', 'workflow.py',
             'config.py', 'storage.py', 'agent_policy.py', 'service.py', 'knowledge_adapter.py',
             'batching.py', 'context.py', 'context_models.py', 'context_budget.py',
             'context_views.py', 'agent_state.py', 'narrative.py', 'profiling.py',
             'reporting.py', 'report_template.md', 'diagnostic_synthesis.py',
             'diagnostic_outcomes.py', 'label_masking.py', 'triple_knowledge.py', 'diagnosis_validation.py')
    return {'contract': 'main_owned_v2',
            'implementation': fingerprint({name: sha256_file(root / name) for name in names})}


def verify_execution_binding(store, run_id):
    rows = store.objects(run_id, 'execution_binding')
    if not rows:
        raise RuntimeError('legacy_run_read_only:create_new_run')
    if rows[0]['payload'] != execution_binding():
        raise RuntimeError('run_implementation_changed:inspect_old_run_or_create_new_run')
