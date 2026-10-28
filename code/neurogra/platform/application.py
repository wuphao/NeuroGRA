"""Shared case, job and report services used by every application transport."""
import hashlib
import json
import time
import uuid
from collections import Counter
from pathlib import Path

from neurogra_common.identity import stable_id

from neurogra.clinical.config import ClinicalConfig
from neurogra.clinical.intake import ingest_patient
from neurogra.clinical.schemas import PatientInput
from neurogra.clinical.utils import dumps, fingerprint, sha256_file, write_json

from .configuration import configuration_for_profile, public_config
from .preflight import inspect_environment
from .store import PlatformStore


class Application:
    def __init__(self, root, config=None):
        self.root = Path(root).resolve()
        from .settings import load_settings
        self.config = config or load_settings(self.root)
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

    def submit(self, version_id, config=None, *, batch_id=None):
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
                                    fingerprint(['analysis', version_id, effective]), batch_id=batch_id)
        return self.job(job['job_id'])

    def submit_batch(self, version_ids, config=None, name='病例批量分析'):
        if not isinstance(version_ids, list) or not 1 <= len(version_ids) <= 200 or len(set(version_ids)) != len(version_ids):
            raise ValueError('请选择 1 至 200 个不重复的病例版本。')
        if not isinstance(name, str) or not name.strip() or len(name) > 120:
            raise ValueError('批次名称不能为空且最长 120 字符。')
        for version_id in version_ids:
            self.patient(version_id)
        config = config or self.config
        ready = inspect_environment(config)
        if not ready['ready']:
            raise ValueError('environment_not_ready')
        updates = {}
        for source, field in [('kb', 'text_knowledge_release_id'), ('kg_release', 'knowledge_release_id')]:
            for check in ready.get('checks', []):
                if check['name'] == source and check.get('details', {}).get('release_id'):
                    updates[field] = check['details']['release_id']
        config = config.model_copy(update=updates)
        batch_id = uuid.uuid4().hex
        plan = {'versions': version_ids, 'config': config.model_dump(mode='json'), 'submission_errors': {}}
        with self.store.connect() as db:
            db.execute('INSERT INTO batches VALUES(?,?,?,?)', (batch_id, name.strip(), time.time(), dumps(plan)))
        for version_id in version_ids:
            try:
                self.submit(version_id, config, batch_id=batch_id)
            except (ValueError, KeyError) as exc:
                # The plan remains persisted; failures are visible, never an apparently complete batch.
                plan['submission_errors'][version_id] = type(exc).__name__
        with self.store.connect() as db:
            db.execute('UPDATE batches SET plan=? WHERE batch_id=?', (dumps(plan), batch_id))
        return self.batch(batch_id)

    def batch(self, batch_id):
        with self.store.connect() as db:
            row = db.execute('SELECT * FROM batches WHERE batch_id=?', (batch_id,)).fetchone()
            if row is None:
                raise KeyError('batch_not_found')
            ids = [r[0] for r in db.execute('SELECT job_id FROM batch_jobs WHERE batch_id=?', (batch_id,))]
        jobs = [self.job(i) for i in ids]
        plan = json.loads(row['plan'])
        if isinstance(plan, list):
            plan = {'versions': plan, 'submission_errors': {}}
        submitted = {j['version_id'] for j in jobs}
        return {'batch_id': batch_id, 'name': row['name'], 'created': row['created'], 'planned': len(plan['versions']),
                'jobs': jobs, 'not_submitted': [v for v in plan['versions'] if v not in submitted],
                'submission_errors': plan.get('submission_errors', {}),
                'counts': dict(Counter(j['status'] for j in jobs))}

    def batches(self):
        with self.store.connect() as db:
            ids = [r[0] for r in db.execute('SELECT batch_id FROM batches ORDER BY created DESC LIMIT 100')]
        return [self.batch(i) for i in ids]

    def configuration(self, profile, overrides=None):
        return configuration_for_profile(self.config, profile, overrides)

    def submit_rwe(self, patient_number):
        from .knowledge import rwe_readiness
        if not isinstance(patient_number, str) or not patient_number.strip() or len(patient_number) > 128 or any(ord(c) < 32 for c in patient_number):
            raise ValueError('请输入有效的 RWE 患者编号。')
        if not rwe_readiness(self.config)['ready']:
            raise ValueError('RWE 凭据或依赖未配置，请检查本地设置。')
        payload = {'patient_number': patient_number.strip()}
        config = self.config.model_dump(mode='json')
        job = self.store.create_job('rwe_import', None, config, payload,
                                    fingerprint(['rwe_import', payload, config]))
        return self.job(job['job_id'])

    def retry_job(self, job_id, *, resume=False):
        from .locking import exclusive
        with exclusive(self.root, 'job_' + job_id):
            job = self.store.job(job_id)
            previous = job['attempts'][-1]
            config = ClinicalConfig.model_validate_json(job['config'])
            if job['kind'] == 'analysis':
                readiness = inspect_environment(config)
            elif job['kind'] == 'rwe_import':
                from .knowledge import rwe_readiness
                readiness = rwe_readiness(config)
            elif job['kind'] == 'knowledge_build':
                from .knowledge import build_readiness
                payload = json.loads(job['payload'])
                readiness = build_readiness(config, kb=payload['kb'], kg=payload['kg'], vector=payload['vector'])
            else:
                readiness = {'ready': True}
            if not readiness['ready']:
                raise ValueError('environment_not_ready')
            run_id = None
            if resume:
                if job['kind'] != 'analysis':
                    raise ValueError('此任务请使用重新执行，不支持临床检查点恢复。')
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
