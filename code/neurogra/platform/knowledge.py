"""Registered sources, build previews and explicit knowledge-version activation."""
import hashlib
import importlib.util
import time
from importlib.resources import files
from pathlib import Path

from neurogra_common.identity import stable_id
from neurogra_kg.utils import atomic_json

from neurogra.clinical.llm import http_json
from neurogra.clinical.utils import fingerprint, sha256_file

from .locking import exclusive


def kb_service(config):
    from neurogra_kb import KBConfig, TextKnowledgeService, load_config
    settings = config.kb or (load_config(config.resolve(config.text_knowledge_config))
                            if config.text_knowledge_config else KBConfig(root=config.project_root))
    return TextKnowledgeService(settings.model_copy(update={'root': config.project_root}))


def build_readiness(config, *, kb=True, kg=False, vector=True):
    checks = []
    endpoints = {}
    models = []
    if kg:
        from neurogra.clinical.knowledge_adapter import settings
        graph_config = settings(config)
        models.append((graph_config.llm.model, graph_config.llm.base_url))
        models.append((graph_config.embedding.model, graph_config.embedding.base_url))
        try:
            from neurogra_kg.adapters.graph import Neo4jGraphStore
            connection = graph_config.graph.model_copy(deep=True)
            if connection.password_file:
                connection.password_file = graph_config.resolve(connection.password_file)
            reachable = connection.provider == 'memory' or bool(Neo4jGraphStore(connection).probe())
        except Exception:
            reachable = False
        checks.append({'name': 'graph', 'available': reachable})
    if kb and vector:
        config_kb = kb_service(config).config
        models.append((config_kb.embedding.model, config_kb.embedding.base_url))
    for name, url in models:
        try:
            if url not in endpoints:
                endpoints[url] = http_json(url.rstrip('/') + '/api/tags', timeout=2)
            available = name in {m.get('name') for m in endpoints[url].get('models', [])}
        except Exception:
            available = False
        checks.append({'name': name, 'available': available})
    return {'ready': all(c['available'] for c in checks), 'checks': checks, 'inference_executed': False}


class KnowledgeManager:
    def __init__(self, app):
        self.app = app

    def upload(self, name, content):
        name = Path(str(name)).name
        suffix = Path(name).suffix.lower()
        if suffix not in {'.pdf', '.docx', '.json'} or not name or len(name) > 160:
            raise ValueError('支持 PDF、DOCX 或结构化 JSON 文献，名称最长 160 字符。')
        if not content or len(content) > 20_000_000:
            raise ValueError('文献文件不能为空且不能超过 20 MB。')
        checksum = hashlib.sha256(content).hexdigest()
        document_id = stable_id('document', checksum)
        # Serialize content registration so aliases with different suffixes cannot
        # create unregistered copies of the same immutable source.
        with exclusive(self.app.root, 'document-registration'):
            existing = next((d for d in self.documents() if d['document_id'] == document_id), None)
            if existing:
                if existing['status'] != 'available':
                    raise ValueError('source_document_missing_or_damaged')
                return existing
            return self._register(name, suffix, content, checksum, document_id)

    def _register(self, name, suffix, content, checksum, document_id):
        path = self.app.inside(self.app.root / 'data/platform/documents' / (document_id + suffix))
        if path.exists() and sha256_file(path) != checksum:
            raise ValueError('source_document_mutated')
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with path.open('xb') as target:
                    target.write(content)
            except FileExistsError:
                if sha256_file(path) != checksum:
                    raise ValueError('source_document_mutated') from None
        with self.app.store.connect() as db:
            db.execute('INSERT OR IGNORE INTO documents VALUES(?,?,?,?,?,?)',
                (document_id, name, path.relative_to(self.app.root).as_posix(), checksum, len(content), time.time()))
        return next(d for d in self.documents() if d['document_id'] == document_id)

    def documents(self):
        with self.app.store.connect() as db:
            rows = [dict(r) for r in db.execute('SELECT * FROM documents ORDER BY created DESC')]
        for row in rows:
            path = self.app.inside(row.pop('path'))
            row['status'] = 'missing' if not path.is_file() else 'available' if sha256_file(path) == row['checksum'] else 'damaged'
        return rows

    def inputs(self, ids):
        if not isinstance(ids, list) or not 1 <= len(ids) <= 50 or len(set(ids)) != len(ids):
            raise ValueError('请选择 1 至 50 份不重复文献。')
        selected = []
        from neurogra_kg.contracts.domain import DocumentInput
        with self.app.store.connect() as db:
            for document_id in ids:
                row = db.execute('SELECT * FROM documents WHERE document_id=?', (document_id,)).fetchone()
                if row is None:
                    raise ValueError('document_not_registered')
                path = self.app.inside(row['path'])
                if not path.is_file() or sha256_file(path) != row['checksum']:
                    raise ValueError('source_document_missing_or_damaged')
                selected.append(DocumentInput(path=path, title=row['name']))
        return selected

    def submit(self, ids, *, kb=True, kg=False, vector=True, preview=False):
        self.inputs(ids)
        if not preview and not kb and not kg:
            raise ValueError('至少启用原文 KB 或条件 KG。')
        if not preview and not build_readiness(self.app.config, kb=kb, kg=kg, vector=vector)['ready']:
            raise ValueError('knowledge_build_environment_not_ready')
        payload = {'document_ids': ids, 'kb': kb, 'kg': kg, 'vector': vector}
        kind = 'document_preview' if preview else 'knowledge_build'
        config = self.app.config.model_dump(mode='json')
        job = self.app.store.create_job(kind, None, config, payload, fingerprint([kind, payload, config]))
        return self.app.job(job['job_id'])

    def versions(self):
        result = {'kb': [], 'kg': [], 'active_kb': None, 'active_kg': None}
        kb = kb_service(self.app.config)
        try:
            result['active_kb'] = kb.active_release()
        except (OSError, ValueError, KeyError):
            pass
        directory = kb.directory / 'releases'
        if directory.is_dir():
            for path in sorted(directory.iterdir()):
                if not path.is_dir():
                    continue
                try:
                    manifest = kb.get_release(path.name)
                    result['kb'].append({'release_id': path.name, 'status': 'available',
                        'documents': manifest['document_count'], 'resources': manifest['resource_count'],
                        'vector': manifest['vector']})
                except (OSError, ValueError, KeyError):
                    result['kb'].append({'release_id': path.name, 'status': 'damaged'})
        try:
            from neurogra.clinical.knowledge_adapter import service
            with service(self.app.config) as graph:
                try:
                    result['active_kg'] = graph.registry.active_release_id()
                except ValueError:
                    pass
                for row in graph.registry.connection.execute('SELECT id FROM releases ORDER BY created_at DESC'):
                    try:
                        manifest = graph.get_release(row[0])
                        result['kg'].append({'release_id': row[0], 'status': 'available',
                                             'statements': len(manifest['statement_ids'])})
                    except (OSError, ValueError, KeyError):
                        result['kg'].append({'release_id': row[0], 'status': 'damaged'})
        except (OSError, ValueError, KeyError):
            pass
        return result

    def activate(self, module, release_id):
        with exclusive(self.app.root, 'knowledge-activation'):
            if module == 'kb':
                service = kb_service(self.app.config)
                manifest = service.get_release(release_id)
                atomic_json(service.directory / 'active.json', {'release_id': manifest['release_id']})
            elif module == 'kg':
                from neurogra.clinical.knowledge_adapter import service
                with service(self.app.config) as graph:
                    manifest = graph.get_release(release_id)
                from neurogra_kg.storage.registry import Registry

                from neurogra.clinical.knowledge_adapter import settings
                config = settings(self.app.config)
                registry = Registry(config.resolve(config.paths.data_root) / 'registry.sqlite')
                try:
                    registry.activate(release_id)
                finally:
                    registry.close()
            else:
                raise ValueError('unknown_knowledge_module')
        return self.versions()


def rwe_configuration(config):
    from neurogra.clinical.rwe import load_rwe_config
    path = config.resolve(config.rwe_config or Path('configs/rwe.project8.yaml'))
    if not path.is_file():
        path = files('neurogra.platform').joinpath('defaults/rwe.project8.yaml')
    return load_rwe_config(path)


def rwe_readiness(config):
    from neurogra.clinical.rwe import local_settings
    schema = rwe_configuration(config)
    local = local_settings(config.resolve(schema.settings_file))
    credentials = bool((local.get('RWE_LOGIN_ACCOUNT') and local.get('RWE_LOGIN_PASSWORD')) or local.get('RWE_API_TOKEN'))
    dependencies = all(importlib.util.find_spec(m) is not None for m in ['requests', 'psycopg2'])
    return {'ready': credentials and dependencies, 'credentials_configured': credentials,
            'dependencies_available': dependencies, 'connection_verified': False,
            'message': '导入时只读连接 RWE，并核对每条记录所属患者。'}
