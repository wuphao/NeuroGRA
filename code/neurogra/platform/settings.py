"""Validated persistent settings; secrets remain in ignored local credentials."""
from pathlib import Path
from urllib.parse import urlsplit

from neurogra.clinical.utils import write_json

from .configuration import merge, public_config, resolve_config

EDITABLE = {'model', 'budget', 'main_agent', 'agents', 'max_rounds', 'max_parallel_tasks',
            'verify_narrative', 'synthesis_reserved_calls', 'llm_debug_logging', 'kb', 'kg'}


def settings_path(root):
    root = Path(root).resolve()
    path = (root / 'data/platform/settings.json').resolve()
    if not path.is_relative_to(root):
        raise ValueError('settings_outside_workspace')
    return path


def load_settings(root):
    path = settings_path(root)
    return resolve_config(path if path.is_file() else None, root=root)


def save_settings(root, current, values):
    from .locking import exclusive
    with exclusive(root, 'settings-update'):
        return _save_settings(root, current, values)


def _save_settings(root, current, values):
    if not isinstance(values, dict) or not set(values) <= EDITABLE:
        raise ValueError('设置中包含不可编辑字段。')
    # Nested root/storage paths and credentials cannot be changed through HTTP.
    for key in ('kb', 'kg'):
        if key in values:
            allowed = {'embedding'} if key == 'kb' else {'embedding', 'graph'}
            if not isinstance(values[key], dict) or not set(values[key]) <= allowed:
                raise ValueError('知识设置仅支持模型与连接参数。')
            if key == 'kg' and 'graph' in values[key]:
                graph = values[key]['graph']
                if not isinstance(graph, dict) or not set(graph) <= {'provider', 'uri', 'username', 'database', 'password_env'}:
                    raise ValueError('数据库凭据请配置到本地环境变量。')
    base = current.model_dump(mode='json')
    # Materialize legacy external SDK configurations before merging nested edits;
    # otherwise an embedding-only change would reset the existing storage paths.
    if 'kb' in values and current.kb is None:
        from .knowledge import kb_service
        base['kb'] = kb_service(current).config.model_dump(mode='json')
    if 'kg' in values and current.kg is None:
        from neurogra.clinical.knowledge_adapter import settings
        base['kg'] = settings(current).model_dump(mode='json')
    effective = merge(base, values)
    config = resolve_config(root=root, overrides=effective)
    endpoints = [config.model.base_url]
    endpoints += [service.embedding.base_url for service in (config.kb, config.kg) if service]
    if any(urlsplit(url).scheme not in {'http', 'https'} or not urlsplit(url).hostname
           or urlsplit(url).username or urlsplit(url).password for url in endpoints):
        raise ValueError('模型地址必须是无内嵌凭据的 HTTP/HTTPS 地址。')
    if config.model.context_tokens <= config.model.max_output_tokens + config.model.context.safety_tokens:
        raise ValueError('上下文容量不足以容纳输出与安全预留。')
    if config.budget.llm <= config.synthesis_reserved_calls:
        raise ValueError('总调用预算必须大于综合诊断预留额度。')
    write_json(settings_path(root), config)
    return config


def settings_view(config):
    value = public_config(config)
    return {k: v for k, v in value.items() if k in EDITABLE}
