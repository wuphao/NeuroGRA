"""Non-generative startup checks with explicit required/optional capabilities."""
import importlib.util
import os
import shutil

from neurogra.clinical.llm import http_json
from neurogra.clinical.generation import GenerationClient, GenerationError
from neurogra.platform.configuration import public_config


def inspect_environment(config, *, services=True):
    checks = []

    def add(name, required, available, message, **details):
        checks.append(dict(name=name, required=required, available=available,
                           message=message, details=details))

    root = config.project_root
    ancestor = root
    while not ancestor.exists() and ancestor != ancestor.parent:
        ancestor = ancestor.parent
    add('storage', True, os.access(ancestor, os.W_OK), '项目数据目录需要可写权限')
    add('disk', True, shutil.disk_usage(ancestor).free > 100 * 1024 ** 2,
        '至少需要 100 MiB 可用空间；实际模型与资料所需空间另计', free_bytes=shutil.disk_usage(ancestor).free)
    for module in ('pydantic', 'numpy', 'pypdf', 'yaml'):
        add('dependency:' + module, True, importlib.util.find_spec(module) is not None,
            '安装项目依赖后重试')
    endpoints = {}

    def model_available(name, url, required):
        available = None
        if services:
            try:
                if url not in endpoints:
                    endpoints[url] = http_json(url.rstrip('/') + '/api/tags', timeout=2)
                available = name in {m.get('name') for m in endpoints[url].get('models', [])}
            except Exception:
                available = False
        add('model:' + name, required, available,
            '检查 Ollama 服务及模型是否已安装' if services else '离线检查：服务尚未验证')

    try:
        generation = GenerationClient(config.model, root)
    except GenerationError as error:
        add('model:' + config.model.model, True, False, str(error), provider=config.model.provider)
    else:
        if generation.native:
            model_available(config.model.model, generation.base_url, True)
        else:
            add('model:' + config.model.model, True, True if services else None,
                '已配置云端模型。可在模型设置中运行连通性测试。', provider=config.model.provider)
    if config.knowledge_enabled('kb'):
        try:
            from neurogra_kb import KBConfig, TextKnowledgeService, load_config
            kb_config = config.kb or (load_config(config.resolve(config.text_knowledge_config))
                if config.text_knowledge_config else KBConfig(root=root))
            kb = TextKnowledgeService(kb_config.model_copy(update={'root': root}))
            release = config.text_knowledge_release_id or kb.active_release()
            manifest = kb.get_release(release) if release else None
            add('kb', True, manifest is not None, '需要可读取且完整的原文知识发布', release_id=release)
            if manifest and manifest.get('vector'):
                model_available(kb.config.embedding.model, kb.config.embedding.base_url, True)
        except Exception:
            add('kb', True, False, '原文知识发布缺失或资源校验失败')
    else:
        add('kb', False, None, '当前配置未启用 KB')
    if config.knowledge_enabled('kg'):
        try:
            from neurogra.clinical.retrieval import pin_release
            release = pin_release(config)
            add('kg_release', True, bool(release), '检查 KG 发布及其资源', release_id=release)
        except Exception:
            add('kg_release', True, False, 'KG 发布不可用；可修复发布或显式选择 kb-only 模式')
        if services:
            try:
                from neurogra.clinical.knowledge_adapter import service
                with service(config) as kg:
                    graph = kg.probe_graph()
                add('graph', True, bool(graph.get('available')), '检查配置的图数据库连接')
            except Exception:
                add('graph', True, False, '图数据库不可用，检查地址和本地凭据')
    else:
        add('kg', False, None, '当前配置未启用 KG')
    return {'ready': all(c['available'] is True for c in checks if c['required']),
            'checks': checks, 'config': public_config(config), 'inference_executed': False}
