"""Shared case, job and report services used by every application transport."""
import hashlib
import json
import time
from pathlib import Path

from neurogra_common.identity import stable_id

from neurogra.clinical.config import ClinicalConfig
from neurogra.clinical.intake import ingest_patient
from neurogra.clinical.schemas import PatientInput
from neurogra.clinical.utils import dumps, fingerprint, sha256_file, write_json

from .configuration import PROFILES, merge, public_config, resolve_config
from .preflight import inspect_environment
from .store import PlatformStore


class Application:
    def __init__(self, root, config=None):
        self.root = Path(root).resolve()
        self.config = config or resolve_config(root=self.root)
        self.store = PlatformStore(self.root)

    def import_case(self, raw, *, source='json'):
        serialized = dumps(raw)
        if len(serialized.encode('utf-8')) > 50_000_000:
            raise ValueError('patient_input_too_large')
        patient = PatientInput.model_validate(raw)
        intake = ingest_patient(raw, self.config)
        checksum = hashlib.sha256(serialized.encode('utf-8')).hexdigest()
        case_id = stable_id('case', patient.patient_id.strip())
        version_id = stable_id('input', [case_id, checksum])
        path = self.root / 'data/platform/cases' / case_id / (version_id + '.json')
        path = self.inside(path)
        if path.exists():
            if sha256_file(path) != hashlib.sha256(serialized.encode('utf-8')).hexdigest():
                raise ValueError('existing_input_snapshot_mutated')
        else:
            write_json(path, raw)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT OR IGNORE INTO cases VALUES(?,?,?)', (case_id, patient.patient_id.strip(), time.time()))
            db.execute('INSERT OR IGNORE INTO case_versions VALUES(?,?,?,?,?,?,?)',
                (version_id, case_id, checksum, path.relative_to(self.root).as_posix(),
                 dumps(source), dumps([i.model_dump(mode='json') for i in intake.issues]), time.time()))
            db.execute('COMMIT')
        return self.case_version(version_id)

    def inside(self, path):
        path = Path(path)
        path = (path if path.is_absolute() else self.root / path).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError('resource_outside_workspace')
        return path

    def patient(self, version_id):
        version = self.store.version(version_id)
        path = self.inside(version['path'])
        if not path.is_file():
            raise ValueError('patient_snapshot_missing')
        if sha256_file(path) != version['checksum']:
            raise ValueError('patient_snapshot_checksum_mismatch')
        raw = json.loads(path.read_text(encoding='utf-8'))
        PatientInput.model_validate(raw)
        return raw

    def case_version(self, version_id):
        version = self.store.version(version_id)
        raw = self.patient(version_id)
        intake = ingest_patient(raw, self.config)
        return {k: v for k, v in version.items() if k != 'path'} | {
            'patient_number': PatientInput.model_validate(raw).patient_id,
            'issues': json.loads(version['issues']), 'source': json.loads(version['source']),
            'record_count': len(intake.records),
            'preview': [r.model_dump(mode='json') for r in intake.records[:80]]}

    def submit(self, version_id, config=None):
        config = config or self.config
        if config.project_root.resolve() != self.root:
            raise ValueError('job_root_mismatch')
        self.patient(version_id)
        readiness = inspect_environment(config)
        if not readiness['ready']:
            missing = [c['name'] for c in readiness['checks'] if c['required'] and c['available'] is not True]
            raise ValueError('environment_not_ready:' + ','.join(missing))
        # Freeze knowledge releases at submission, not after waiting in the queue.
        updates = {}
        if config.knowledge_enabled('kb'):
            updates['text_knowledge_release_id'] = next(c['details']['release_id'] for c in readiness['checks'] if c['name'] == 'kb')
        if config.knowledge_enabled('kg'):
            updates['knowledge_release_id'] = next(c['details']['release_id'] for c in readiness['checks'] if c['name'] == 'kg_release')
        config = config.model_copy(update=updates)
        effective = config.model_dump(mode='json')
        job = self.store.create_job('analysis', version_id, effective, {},
                                    fingerprint(['analysis', version_id, effective]))
        return self.job(job['job_id'])

    def configuration(self, profile, overrides=None):
        if profile not in PROFILES:
            raise ValueError('unknown_configuration_profile')
        access = {'use_kb': profile != 'patient-only', 'use_kg': profile in {'standard', 'quick'}}
        patch = {'main_agent': access, 'agents': {r: access for r in self.config.agents}}
        if profile == 'quick':
            patch = merge(patch, {'max_rounds': 1, 'budget': {'llm': 32, 'seconds': 600}})
        values = merge(self.config.model_dump(mode='json'), merge(patch, overrides or {}))
        return resolve_config(root=self.root, overrides=values)

    def retry_job(self, job_id, *, resume=False):
        from .locking import exclusive
        with exclusive(self.root, 'job_' + job_id):
            job = self.store.job(job_id)
            previous = job['attempts'][-1]
            config = ClinicalConfig.model_validate_json(job['config'])
            readiness = inspect_environment(config)
            if not readiness['ready']:
                raise ValueError('environment_not_ready')
            run_id = None
            if resume:
                from neurogra.clinical.execution import verify_execution_binding
                from neurogra.clinical.storage import RunStore
                run_id = previous['run_id']
                if not run_id:
                    raise ValueError('run_not_started_create_new_attempt')
                engine = RunStore(config.resolve(config.database))
                if engine.run(run_id)['deadline'] <= time.time():
                    raise ValueError('run_deadline_expired_create_new_attempt')
                if engine.objects(run_id, 'run_result'):
                    raise ValueError('run_already_finished_create_new_attempt')
                verify_execution_binding(engine, run_id)
            self.store.retry(job_id, resume_run_id=run_id)
        return self.job(job_id)

    def job(self, job_id):
        job = self.store.job(job_id)
        job['config'] = public_config(ClinicalConfig.model_validate_json(job['config']))
        job['payload'] = json.loads(job['payload'])
        for attempt in job['attempts']:
            attempt['result'] = json.loads(attempt['result']) if attempt['result'] else None
            attempt['events'] = self.store.events(attempt['attempt_id'])
            for event in attempt['events']:
                event['payload'] = json.loads(event['payload'])
            attempt['artifacts'] = self.artifacts(attempt['attempt_id'])
        return job

    def register_artifacts(self, attempt_id, paths):
        for kind, path in paths.items():
            path = self.inside(path)
            if not path.is_file():
                raise ValueError('generated_artifact_missing')
            artifact_id = stable_id('artifact', [attempt_id, kind])
            with self.store.connect() as db:
                db.execute('INSERT OR REPLACE INTO artifacts VALUES(?,?,?,?,?,?,?)',
                    (artifact_id, attempt_id, kind, path.relative_to(self.root).as_posix(),
                     sha256_file(path), path.stat().st_size, 'available'))

    def artifacts(self, attempt_id):
        with self.store.connect() as db:
            rows = [dict(r) for r in db.execute('SELECT * FROM artifacts WHERE attempt_id=?', (attempt_id,))]
        for row in rows:
            try:
                path = self.inside(row['path'])
                status = 'missing' if not path.is_file() else 'available' if sha256_file(path) == row['checksum'] else 'damaged'
            except ValueError:
                status = 'outside_workspace'
            row['status'] = status if row['status'] == 'available' else row['status']
            row.pop('path')
        return rows

    def artifact(self, artifact_id):
        with self.store.connect() as db:
            row = db.execute('SELECT * FROM artifacts WHERE artifact_id=?', (artifact_id,)).fetchone()
        if row is None:
            raise KeyError('artifact_not_found')
        row = dict(row)
        path = self.inside(row['path'])
        if row['status'] != 'available' or not path.is_file():
            raise ValueError('artifact_unavailable:文件已清理或归档')
        if sha256_file(path) != row['checksum']:
            raise ValueError('artifact_damaged:文件校验失败')
        return row, path
