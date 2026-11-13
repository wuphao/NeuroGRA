"""Consistent private file/SQLite snapshots and non-overwriting isolated restore."""
import json
import os
import shutil
import sqlite3
import time
import uuid
from contextlib import ExitStack, closing
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from neurogra.clinical.config import ClinicalConfig
from neurogra.clinical.utils import dumps, fingerprint, sha256_file, write_json

from .activity import assert_chat_quiescent, maintenance
from .locking import exclusive
from .restoration import restoration_roots


def safe_relative(value):
    if not isinstance(value, str) or not value or '\\' in value or ':' in value:
        raise ValueError('invalid_backup_path')
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or str(path) != value:
        raise ValueError('invalid_backup_path')
    return Path(*path.parts)


def sqlite_file(path):
    with path.open('rb') as stream:
        return stream.read(16) == b'SQLite format 3\x00'


def relocated(value, old, new):
    """Rebase structured runtime path fields; patient prose remains untouched."""
    if value is None:
        return None
    path = Path(value)
    if path.is_absolute() and path.is_relative_to(old):
        return str(new / path.relative_to(old))
    return str(path)


def relocate_config(raw, old, new):
    config = ClinicalConfig.model_validate(raw).model_dump(mode='json')
    for key in ('project_root', 'patient_data_root', 'database', 'data_root', 'output_root', 'report_root',
                'knowledge_config', 'text_knowledge_config', 'rwe_config', 'diamond_root', 'diamond_python',
                'diamond_checkpoint', 'diamond_validation_report'):
        config[key] = relocated(config[key], old, new)
    config['project_root'] = str(new)
    config['allowed_roots'] = [relocated(path, old, new) for path in config['allowed_roots']]
    for module in ('kb', 'kg'):
        if config[module]:
            config[module]['root'] = str(new)
    if config['kb']:
        for key in ('data_root', 'output_root'):
            config['kb'][key] = relocated(config['kb'][key], old, new)
    if config['kg']:
        for key in ('data_root', 'output_root'):
            config['kg']['paths'][key] = relocated(config['kg']['paths'][key], old, new)
        config['kg']['graph']['password_file'] = relocated(config['kg']['graph']['password_file'], old, new)
    for key in ('index_root', 'index_manifest'):
        config['vector'][key] = relocated(config['vector'][key], old, new)
    return config


def relocate_run_config(raw, old, new):
    """Relocate known lightweight chat bindings without inventing a clinical config.

    Their digests remain bound to the original execution; an unfinished dialogue
    must fail its normal resume check after relocation rather than be re-signed.
    """
    if not isinstance(raw, dict):
        raise ValueError('backup_run_config_invalid')
    dialogue = (raw.get('purpose') in {'patient_dialogue', 'mixed_dialogue'} and
                set(raw) <= {'project_root', 'purpose', 'binding', 'config_digest', 'execution_digest', 'plan_digest',
                             'budget_parent_run_id', 'budget_child_run_id'})
    followup = isinstance(raw.get('followup'), dict) and set(raw) <= {'project_root', 'followup'}
    if dialogue or followup:
        if not isinstance(raw.get('project_root'), str) or Path(raw['project_root']).resolve() != old:
            raise ValueError('backup_run_root_mismatch')
        return {**raw, 'project_root': str(new)}
    return relocate_config(raw, old, new)


def embedded_config(config):
    from neurogra.clinical.knowledge_adapter import settings

    from .knowledge import kb_service
    raw = config.model_dump(mode='json')
    raw['kb'] = kb_service(config).config.model_dump(mode='json')
    raw['kg'] = settings(config).model_dump(mode='json')
    return raw


class Backups:
    def __init__(self, app):
        self.app = app

    def _sources(self, configs):
        roots = {self.app.root / 'data/platform'}
        secrets = {self.app.root / '.env', self.app.root / '.env.rwe.local'}
        for raw in configs:
            config = ClinicalConfig.model_validate(raw)
            for module in ('model', 'vector'):
                if urlsplit(raw[module]['base_url']).username or urlsplit(raw[module]['base_url']).password:
                    raise ValueError('backup_requires_credentials_outside_service_url')
            for path in (config.data_root, config.database, config.output_root, config.report_root,
                         config.patient_data_root, *config.allowed_roots):
                roots.add(config.resolve(path))
            for module in ('kb', 'kg'):
                service = config.kb if module == 'kb' else config.kg
                paths = service if module == 'kb' else service.paths
                roots.update((service.resolve(paths.data_root), service.resolve(paths.output_root)))
                for endpoint in (service.embedding.base_url, *((service.llm.base_url, service.graph.uri) if module == 'kg' else ())):
                    if urlsplit(endpoint).username or urlsplit(endpoint).password:
                        raise ValueError('backup_requires_credentials_outside_service_url')
            if config.kg.graph.password_file:
                secrets.add(config.kg.resolve(config.kg.graph.password_file))
            from .knowledge import rwe_configuration
            secrets.add(config.resolve(rwe_configuration(config).settings_file))
        for root in roots:
            if not root.is_relative_to(self.app.root) or root == self.app.root:
                raise ValueError('backup_data_root_must_be_inside_workspace')
        return roots, secrets

    def create(self, destination):
        destination = Path(destination).absolute()
        if destination.exists():
            raise ValueError('backup_destination_must_not_exist')
        if destination.resolve() != destination:
            raise ValueError('backup_destination_cannot_be_linked')
        # All platform writers that can change referenced files are fenced, while
        # BEGIN IMMEDIATE blocks queue claims and metadata writes during the copy.
        with ExitStack() as stack:
            stack.enter_context(maintenance(self.app.root))
            assert_chat_quiescent(self.app.root)
            for name in ('backup', 'model_resource', 'document-registration', 'knowledge-activation', 'settings-update'):
                stack.enter_context(exclusive(self.app.root, name))
            db = stack.enter_context(self.app.store.connect())
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running') LIMIT 1").fetchone():
                raise ValueError('请在排队和运行任务结束后创建备份。')
            jobs = {row[0]: embedded_config(ClinicalConfig.model_validate_json(row[1]))
                    for row in db.execute('SELECT job_id,config FROM jobs')}
            batches = {}
            for batch_id, raw in db.execute('SELECT batch_id,plan FROM batches'):
                plan = json.loads(raw)
                if isinstance(plan, dict) and 'config' in plan:
                    batches[batch_id] = embedded_config(ClinicalConfig.model_validate(plan['config']))
            configs = [embedded_config(self.app.config), *jobs.values(), *batches.values()]
            roots, secrets = self._sources(configs)
            for table in ('case_versions', 'documents', 'artifacts'):
                for row in db.execute(f'SELECT path FROM {table}'):
                    roots.add(self.app.inside(row[0]))
            if any(destination == root or destination.is_relative_to(root) for root in roots):
                raise ValueError('backup_destination_inside_source_data')
            source_files, excluded = {}, []
            for root in sorted(roots):
                if not root.exists():
                    continue
                candidates = [root] if root.is_file() else self._walk(root)
                for path in candidates:
                    relative = path.relative_to(self.app.root).as_posix()
                    if path.resolve() != path.absolute():
                        raise ValueError('backup_linked_data_not_supported')
                    if path in secrets or path.name == '.env' or path.name.startswith('.env.'):
                        excluded.append(relative)
                        continue
                    if relative.startswith('data/platform/locks/') or path.name.endswith(('-wal', '-shm', '-journal')):
                        continue
                    source_files[relative] = path
            # An immutable input is required; missing old report artifacts remain
            # explicitly unavailable and are never replaced with invented files.
            for table in ('case_versions', 'documents'):
                for path, checksum in db.execute(f'SELECT path,checksum FROM {table}'):
                    source = self.app.inside(path)
                    if not source.is_file() or sha256_file(source) != checksum:
                        raise ValueError('backup_input_missing_or_damaged')
            known_missing = [r[0] for r in db.execute('SELECT artifact_id,path,checksum FROM artifacts')
                if not self.app.inside(r[1]).is_file() or sha256_file(self.app.inside(r[1])) != r[2]]
            stage = destination.with_name(destination.name + '.partial-' + uuid.uuid4().hex)
            stage.mkdir(parents=True)
            entries = []
            try:
                for source in sorted(set(source_files.values())):
                    if source != self.app.store.path and sqlite_file(source):
                        guard = stack.enter_context(closing(sqlite3.connect(source, timeout=15, isolation_level=None)))
                        guard.execute('BEGIN IMMEDIATE')
                for relative, source in sorted(source_files.items()):
                    target = stage / 'files' / safe_relative(relative)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if sqlite_file(source):
                        # A separate read connection sees the last committed WAL;
                        # copying only the main SQLite file would silently lose it.
                        with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as reader, \
                             closing(sqlite3.connect(target)) as writer:
                            reader.backup(writer)
                            if writer.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                                raise ValueError('backup_database_integrity_failed')
                    else:
                        before = sha256_file(source)
                        shutil.copyfile(source, target)
                        if sha256_file(source) != before or sha256_file(target) != before:
                            raise ValueError('backup_source_changed_retry')
                    entries.append({'path': relative, 'bytes': target.stat().st_size,
                                    'checksum': sha256_file(target)})
                from .knowledge import rwe_configuration
                manifest = {'schema_version': 1, 'created': time.time(), 'source_root': str(self.app.root),
                    'files': entries, 'configuration': configs[0],
                    'job_configurations': jobs, 'batch_configurations': batches,
                    'job_rwe_configurations': {job_id: rwe_configuration(ClinicalConfig.model_validate(raw)).model_dump(mode='json')
                                               for job_id, raw in jobs.items()},
                    'rwe_configuration': rwe_configuration(self.app.config).model_dump(mode='json'),
                    'contains_private_data': True, 'credentials_included': False,
                    'excluded_credentials': sorted(set(excluded)), 'known_unavailable_artifacts': known_missing,
                    'external_services_included': False}
                write_json(stage / 'manifest.json', manifest)
                verify(stage)
                stage.rename(destination)
                return {'path': str(destination), 'files': len(entries),
                        'bytes': sum(e['bytes'] for e in entries), 'known_unavailable_artifacts': known_missing}
            except BaseException:
                # Preserve a failed snapshot for inspection; it is never accepted
                # as a completed backup without a validated manifest.
                raise

    def _walk(self, directory):
        def failed(error):
            raise error
        for base, directories, names in os.walk(directory, followlinks=False, onerror=failed):
            for name in directories:
                path = Path(base) / name
                if path.resolve() != path.absolute():
                    raise ValueError('backup_linked_data_not_supported')
            for name in names:
                yield Path(base) / name


def verify(directory):
    directory = Path(directory).absolute()
    if directory.resolve() != directory:
        raise ValueError('backup_directory_cannot_be_linked')
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('schema_version') != 1 or not isinstance(manifest.get('files'), list):
        raise ValueError('backup_manifest_invalid')
    seen = set()
    for entry in manifest['files']:
        relative = safe_relative(entry['path'])
        path = directory / 'files' / relative
        if entry['path'] in seen or path.resolve() != path.absolute() or not path.is_file():
            raise ValueError('backup_file_missing_or_duplicated')
        seen.add(entry['path'])
        if path.stat().st_size != entry['bytes'] or sha256_file(path) != entry['checksum']:
            raise ValueError('backup_file_checksum_mismatch')
    if 'data/platform/platform.sqlite' not in seen:
        raise ValueError('backup_platform_database_missing')
    old = Path(manifest['source_root'])
    if not old.is_absolute():
        raise ValueError('backup_source_root_invalid')
    for raw in [manifest['configuration'], *manifest['job_configurations'].values(),
                *manifest['batch_configurations'].values()]:
        config = ClinicalConfig.model_validate(raw)
        if config.project_root != old or config.kb is None or config.kg is None:
            raise ValueError('backup_configuration_binding_invalid')
        paths = [config.resolve(path) for path in (config.data_root, config.database, config.output_root,
                 config.report_root, config.patient_data_root, *config.allowed_roots)]
        for module in (config.kb, config.kg):
            if module.root != old:
                raise ValueError('backup_configuration_binding_invalid')
            settings = module if module is config.kb else module.paths
            paths.extend([module.resolve(settings.data_root), module.resolve(settings.output_root)])
        if any(path == old or not path.is_relative_to(old) for path in paths):
            raise ValueError('backup_configuration_data_outside_workspace')
    with closing(sqlite3.connect((directory / 'files/data/platform/platform.sqlite').as_uri() + '?mode=ro', uri=True)) as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or db.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('backup_database_integrity_failed')
        if db.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running') LIMIT 1").fetchone():
            raise ValueError('backup_contains_nonterminal_jobs')
        for table in ('case_versions', 'documents'):
            for path, checksum in db.execute(f'SELECT path,checksum FROM {table}'):
                source = directory / 'files' / safe_relative(path)
                if path not in seen or not source.is_file() or sha256_file(source) != checksum:
                    raise ValueError('backup_input_integrity_failed')
    _verify_knowledge(manifest, directory / 'files')
    return manifest


def _verify_knowledge(manifest, root):
    from neurogra_kg.storage.registry import Registry
    from neurogra_kg.storage.releases import load_release
    from neurogra_kg.utils import safe_identifier

    from .knowledge import kb_service
    checked = set()
    configs = [manifest['configuration'], *manifest['job_configurations'].values(),
               *manifest['batch_configurations'].values()]
    for raw in configs:
        config = ClinicalConfig.model_validate(relocate_config(raw, Path(manifest['source_root']), root))
        kb = kb_service(config)
        key = str(kb.directory)
        if key not in checked:
            checked.add(key)
            releases = kb.directory / 'releases'
            if releases.is_dir():
                for path in releases.iterdir():
                    if path.is_dir():
                        kb.get_release(path.name)
            kb.active_release()
        data = config.kg.resolve(config.kg.paths.data_root)
        key = str(data)
        if key not in checked and (data / 'registry.sqlite').is_file():
            checked.add(key)
            registry = Registry(data / 'registry.sqlite', read_only=True)
            try:
                for identifier, text in registry.connection.execute('SELECT id,manifest FROM releases'):
                    release = json.loads(text)
                    if release['release_id'] != identifier:
                        raise ValueError('backup_kg_release_identity_mismatch')
                    load_release(release, directory=data / 'releases' / safe_identifier(identifier))
            finally:
                registry.close()


def restore(directory, destination):
    manifest = verify(directory)
    directory, destination = Path(directory).absolute(), Path(destination).absolute()
    if destination.exists() or destination.resolve() != destination:
        raise ValueError('restore_requires_new_unlinked_directory')
    old = Path(manifest['source_root'])
    stage = destination.with_name(destination.name + '.partial-' + uuid.uuid4().hex)
    stage.mkdir(parents=True)
    for entry in manifest['files']:
        relative = safe_relative(entry['path'])
        target = stage / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(directory / 'files' / relative, target)
        if sha256_file(target) != entry['checksum']:
            raise ValueError('restore_copy_checksum_mismatch')
        if sqlite_file(target) and entry['path'] != 'data/platform/platform.sqlite':
            _relocate_engine(target, old, destination)
    # Rebase structured metadata only. Reports and immutable source snapshots are
    # byte-identical to the backup, preserving their hashes and original prose.
    database = stage / 'data/platform/platform.sqlite'
    with closing(sqlite3.connect(database)) as db:
        db.execute('BEGIN IMMEDIATE')
        for job_id, kind, version_id, payload in db.execute('SELECT job_id,kind,version_id,payload FROM jobs').fetchall():
            config = manifest['job_configurations'][job_id]
            rebased = relocate_config(config, old, destination)
            schema = manifest['job_rwe_configurations'][job_id]
            rebased['rwe_config'] = ('data/platform/rwe-restored.json' if schema == manifest['rwe_configuration']
                else f'data/platform/restored-rwe/{fingerprint(schema)}.json')
            write_json(stage / rebased['rwe_config'], schema)
            rebased = ClinicalConfig.model_validate(rebased).model_dump(mode='json')
            payload_value = json.loads(payload)
            if kind == 'analysis':
                binding = [kind, version_id, rebased]
                if payload_value.get('patient_workspace'):
                    binding.append(payload_value)
            else:
                binding = [kind, payload_value, rebased]
            db.execute('UPDATE jobs SET config=?,fingerprint=? WHERE job_id=?', (dumps(rebased), fingerprint(binding), job_id))
        for batch_id, raw in db.execute('SELECT batch_id,plan FROM batches').fetchall():
            plan = json.loads(raw)
            if isinstance(plan, dict) and 'config' in plan:
                plan['config'] = relocate_config(manifest['batch_configurations'][batch_id], old, destination)
                plan['config']['rwe_config'] = 'data/platform/rwe-restored.json'
                db.execute('UPDATE batches SET plan=? WHERE batch_id=?', (dumps(plan), batch_id))
        db.execute("UPDATE workers SET status='stopped',stop_requested=1")
        db.execute('DELETE FROM resource_leases')
        for attempt_id, raw in db.execute('SELECT attempt_id,result FROM attempts WHERE result IS NOT NULL').fetchall():
            result = json.loads(raw)
            _relocate_report_paths(result, old, destination)
            db.execute('UPDATE attempts SET result=? WHERE attempt_id=?', (dumps(result), attempt_id))
        db.commit()
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or db.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('restored_database_integrity_failed')
        for table in ('case_versions', 'documents'):
            for path, checksum in db.execute(f'SELECT path,checksum FROM {table}'):
                source = stage / safe_relative(path)
                if not source.is_file() or sha256_file(source) != checksum:
                    raise ValueError('restored_input_integrity_failed')
    config = relocate_config(manifest['configuration'], old, destination)
    config['rwe_config'] = 'data/platform/rwe-restored.json'
    write_json(stage / config['rwe_config'], manifest['rwe_configuration'])
    write_json(stage / 'data/platform/settings.json', config)
    legacy_roots = list(dict.fromkeys([str(old), *restoration_roots(stage)]))
    write_json(stage / 'data/platform/restoration.json', {'source_root': str(old), 'legacy_roots': legacy_roots,
        'restored': time.time(), 'known_unavailable_artifacts': manifest['known_unavailable_artifacts'],
        'services_verified': False, 'credentials_restored': False})
    stage.rename(destination)
    return {'path': str(destination), 'files': len(manifest['files']), 'integrity': 'passed',
            'services_verified': False, 'credentials_restored': False,
            'known_unavailable_artifacts': manifest['known_unavailable_artifacts']}


def _relocate_report_paths(result, old, new):
    if isinstance(result, dict) and isinstance(result.get('report_paths'), dict):
        result['report_paths'] = {kind: relocated(path, old, new) for kind, path in result['report_paths'].items()}


def _relocate_engine(path, old, new):
    with closing(sqlite3.connect(path)) as db:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'runs', 'objects', 'budget_ledger'} <= tables:
            return
        db.execute('BEGIN IMMEDIATE')
        for run_id, raw in db.execute('SELECT run_id,config FROM runs').fetchall():
            db.execute('UPDATE runs SET config=? WHERE run_id=?',
                       (dumps(relocate_run_config(json.loads(raw), old, new)), run_id))
        for rowid, raw in db.execute("SELECT rowid,payload FROM objects WHERE kind='run_result'").fetchall():
            result = json.loads(raw)
            _relocate_report_paths(result, old, new)
            db.execute('UPDATE objects SET payload=? WHERE rowid=?', (dumps(result), rowid))
        db.commit()
