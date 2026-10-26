"""One validated configuration resolver shared by scripts, CLI and workbench."""
import os
from pathlib import Path

import yaml
from neurogra_kb.config import KBConfig
from neurogra_kg.config import KGConfig

from neurogra.clinical.config import ClinicalConfig

PROFILES = ('standard', 'quick', 'kb-only', 'patient-only')


def merge(base, overlay):
    result = dict(base)
    for key, value in overlay.items():
        result[key] = merge(result[key], value) if isinstance(value, dict) and isinstance(result.get(key), dict) else value
    return result


def resolve_config(path=None, *, profile='standard', root=None, overrides=None):
    if profile not in PROFILES:
        raise ValueError('unknown_configuration_profile')
    root = Path(root or os.environ.get('NEUROGRA_HOME') or Path.cwd()).resolve()
    base = ClinicalConfig(project_root=root, kb=KBConfig(root=root), kg=KGConfig(root=root),
        text_knowledge_config=None, max_records=5000, verify_narrative=True,
        synthesis_reserved_calls=16, max_parallel_tasks=2).model_dump(mode='json')
    base = merge(base, {'model': {'timeout_seconds': 300, 'context': {'max_batch_items': 80}},
        'budget': {'seconds': 1200}, 'agents': {
            role: {'task_budget': {'max_llm_calls': calls, 'timeout_seconds': 600}}
            for role, calls in [('history', 4), ('cognition', 32), ('laboratory', 4), ('imaging', 2)]}})
    if profile == 'quick':
        base = merge(base, {'budget': {'seconds': 600, 'llm': 32}, 'max_rounds': 1,
                            'model': {'context': {'max_batch_items': 35}}})
    if profile in {'kb-only', 'patient-only'}:
        access = {'use_kg': False, **({'use_kb': False} if profile == 'patient-only' else {})}
        base = merge(base, {'main_agent': access, 'agents': {r: access for r in base['agents']}})
    if path:
        path = Path(path).resolve()
        raw = yaml.safe_load(path.read_text(encoding='utf-8-sig')) or {}
        if not isinstance(raw, dict):
            raise ValueError('clinical_configuration_must_be_mapping')
        base = merge(base, raw)
        if 'text_knowledge_config' in raw and 'kb' not in raw:
            base['kb'] = None
        if 'knowledge_config' in raw and 'kg' not in raw:
            base['kg'] = None
    base = merge(base, overrides or {})
    base['project_root'] = str(root)
    for key in ('kb', 'kg'):
        if base.get(key) is not None:
            base[key]['root'] = str(root)
    return ClinicalConfig.model_validate(base)


def public_config(config):
    """Never expose configured secret values in diagnostics or the browser."""
    def redact(value):
        if isinstance(value, dict):
            return {k: ('[configured]' if v else None) if k.lower() in {
                'password', 'token', 'api_key', 'db_password', 'secret'} else redact(v)
                for k, v in value.items()}
        if isinstance(value, list):
            return [redact(v) for v in value]
        return value
    return redact(config.model_dump(mode='json'))
