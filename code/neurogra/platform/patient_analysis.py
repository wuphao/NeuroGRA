"""Bind authenticated RWE patients to durable NeuroGRA analysis and report artifacts."""
import hashlib
import json
import time

from neurogra.clinical.rwe import SYSTEM_FIELDS
from neurogra.clinical.utils import dumps, fingerprint

from .activity import tracked
from .locking import exclusive
from .patient_workspace import WorkspaceError, read_rows


def ensure_worker(root):
    import os
    import subprocess
    import sys

    from .store import PlatformStore
    store = PlatformStore(root)
    with exclusive(root, 'worker-launch'):
        if any(row['online'] and not row['stop_requested'] for row in store.workers()):
            return
        directory = root / 'data/platform/logs'
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / 'worker.log').open('ab') as log:
            child = subprocess.Popen([sys.executable, '-m', 'neurogra.platform', '--root', str(root), 'worker'],
                cwd=root, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                start_new_session=os.name != 'nt')
        deadline = time.monotonic() + 10
        while not any(row['online'] and not row['stop_requested'] for row in store.workers()):
            if child.poll() is not None or time.monotonic() > deadline:
                raise WorkspaceError('任务已保存，但后台进程未就绪。请再次点击智能分析重试启动。', 503)
            time.sleep(.1)


class PatientAnalysis:
    def __init__(self, app, workspace, *, starter=ensure_worker):
        self.app, self.workspace, self.starter = app, workspace, starter

    def context(self, patient, profile='kb-only', knowledge_note=None):
        context = {'source_id': self.workspace.source_id, 'project_id': self.workspace.schema.project_id,
                   'patient_id': patient['patientKey'], 'patient_number': patient['id'], 'profile': profile}
        if knowledge_note:
            context['knowledge_note'] = knowledge_note
        return context

    def export(self, session, patient, profile='kb-only'):
        reader = self.workspace.reader(session)
        forms = self.workspace.schema.forms
        batch = reader.query('/form/queryDataBatch', {'forms': [{'formId': form.form_id} for form in forms],
            'patientId': int(patient['patientKey']), 'page': 1, 'pageSize': 0})
        metadata = reader.query('/form/getFormDetailsBatch', {'formIds': [form.form_id for form in forms]})
        if not isinstance(batch, dict) or not isinstance(metadata, dict):
            raise WorkspaceError('患者资料读取不完整，未启动分析。')
        raw = {'schema_version': '2.0', 'project': {'id': self.workspace.schema.project_id},
               'patient': {'patient_id': int(patient['patientKey']), 'patient_number': patient['id'], 'name': patient['name']},
               'demographics': {'性别': patient['sex'], **({'年龄': patient['age']} if patient['age'] is not None else {})},
               'forms': {}, 'data_quality': {'warnings': [], 'missing_forms': []}}
        source_forms, total = [], 0
        for form in forms:
            records = read_rows(batch.get(str(form.form_id)))
            definition = metadata.get(str(form.form_id))
            fields = definition.get('fields') if isinstance(definition, dict) else None
            if not isinstance(fields, list) or not all(isinstance(field, dict) for field in fields):
                raise WorkspaceError('表单结构读取失败，未启动分析。')
            labels = {field.get('enName', field.get('en_name')):
                      field.get('name') or field.get('cnName') or field.get('enName') for field in fields}
            output = []
            for record in records:
                identities = [record[key] for key in ('patient_id', 'patientId') if key in record]
                if not identities or any(str(value) != patient['patientKey'] for value in identities):
                    raise WorkspaceError('表单与患者不匹配，未启动分析。')
                row = {'record_id': record.get('id'), 'patient_id': int(patient['patientKey']),
                       'visit_date': record.get(form.date_field) if form.date_field else None}
                for key, value in record.items():
                    if key in SYSTEM_FIELDS or key == form.date_field:
                        continue
                    label = labels.get(key) or key
                    if label in row:
                        label = key
                    if label in row:
                        raise WorkspaceError('表单字段名称存在冲突，未启动分析。')
                    row[label] = value
                output.append(row)
            output.sort(key=dumps)
            raw['forms'][form.key] = {'form_id': form.form_id, 'form_name': form.name, 'records': output,
                                      'definition_fingerprint': fingerprint(sorted(fields, key=dumps))}
            source_forms.append({'form_id': form.form_id, 'name': form.name, 'record_count': len(output)})
            total += len(output)
            if not output:
                raw['data_quality']['missing_forms'].append(form.name)
        if not total:
            raise WorkspaceError('该患者尚无表单记录，无法启动分析。', 400)
        return raw, {'kind': 'rwe_workspace', **self.context(patient, profile), 'forms': source_forms, 'issues': []}

    def history(self, session, patient_id):
        patient = self.workspace.patient(session, patient_id, refresh=True)
        with self.app.store.connect() as db:
            rows = db.execute('''SELECT job_id FROM jobs WHERE kind='analysis'
                AND json_extract(payload,'$.patient_workspace.source_id')=?
                AND json_extract(payload,'$.patient_workspace.project_id')=?
                AND json_extract(payload,'$.patient_workspace.patient_id')=?
                ORDER BY created DESC LIMIT 20''', (self.workspace.source_id, self.workspace.schema.project_id, patient['patientKey'])).fetchall()
        return {'jobs': [self.app.job(row[0]) for row in rows], 'patient_number': patient['id'], 'profile': 'per-job'}

    @tracked(lambda self: self.app.root)
    def submit(self, session, patient_id, *, request_key=None, expected_fingerprint=None):
        patient = self.workspace.patient(session, patient_id, refresh=True)
        lock_name = f'patient-analysis-{self.workspace.source_id}-{self.workspace.schema.project_id}-{int(patient_id)}'
        with exclusive(self.app.root, lock_name):
            previous = self.app.store.dispatched_job(request_key) if request_key else None
            if previous:
                job = self.job(session, patient_id, previous)
                if expected_fingerprint is not None and self.input_fingerprint(self.app.patient(job['version_id'])) != expected_fingerprint:
                    raise WorkspaceError('原请求对应的患者资料版本不一致。', 409)
                self.starter(self.app.root)
                return job
            config, profile = self.app.patient_configuration()
            raw, source = self.export(session, patient, profile)
            knowledge_note = None
            if self.app.config.knowledge_enabled('kg') and not config.knowledge_enabled('kg'):
                knowledge_note = ('知识图谱未就绪，本次仅使用已启用的本地文本知识。' if config.knowledge_enabled('kb')
                                  else '知识图谱未就绪，本次没有启用其他本地知识来源。')
                source['knowledge_note'] = knowledge_note
            if expected_fingerprint is not None and self.input_fingerprint(raw) != expected_fingerprint:
                raise WorkspaceError('患者资料再次发生变化，请重新确认。', 409)
            try:
                case = self.app.import_case(raw, source=source)
                job = self.app.submit(case['version_id'], config,
                                      context=self.context(patient, profile, knowledge_note), request_key=request_key)
            except ValueError as exc:
                if 'environment_not_ready' in str(exc):
                    raise WorkspaceError('分析环境未就绪，请检查模型与原文知识库。', 503) from None
                if 'max_records_exceeded' in str(exc):
                    raise WorkspaceError('患者资料超过当前分析容量，未截断资料，请调整输入容量后重试。', 400) from None
                raise
            self.starter(self.app.root)
            return job

    @staticmethod
    def input_fingerprint(raw):
        return hashlib.sha256(dumps(raw).encode('utf-8')).hexdigest()

    def report_status(self, session, patient_id):
        """Compare a completed report with current input without assuming a consistent upstream snapshot."""
        history = self.history(session, patient_id)
        for job in history['jobs']:
            if job['status'] != 'succeeded':
                continue
            for attempt in reversed(job['attempts']):
                report = next((item for item in attempt['artifacts']
                               if item['kind'] == 'json' and item['status'] == 'available'), None)
                if report is None:
                    continue
                try:
                    _, report_path = self.artifact(session, patient_id, job['job_id'], report['artifact_id'])
                    report_data = json.loads(report_path.read_text(encoding='utf-8'))
                    old_raw = self.app.patient(job['version_id'])
                    patient = self.workspace.patient(session, patient_id, refresh=True)
                    current_raw, _ = self.export(session, patient)
                except WorkspaceError as error:
                    if error.status in {401, 403, 404}:
                        raise
                    return {'status': 'unknown', 'job': job, 'input_fingerprint': None}
                except (ValueError, OSError, KeyError):
                    return {'status': 'unknown', 'job': job, 'input_fingerprint': None}
                current = self.input_fingerprint(current_raw)
                execution_status = (attempt.get('result') or {}).get('execution_status')
                if execution_status in {'partial', 'failed'} or report_data.get('status') in {'partial', 'failed'}:
                    status = 'incomplete'
                elif current != self.input_fingerprint(old_raw):
                    status = 'changed'
                elif self.workspace.schema.consistent_patient_snapshot:
                    status = 'unchanged'
                else:
                    status = 'unknown'
                return {'status': status, 'job': job, 'input_fingerprint': current}
        return {'status': 'missing', 'job': None, 'input_fingerprint': None}

    def job(self, session, patient_id, job_id):
        patient = self.workspace.patient(session, patient_id, refresh=True)
        job = self.app.job(job_id)
        binding = job['payload'].get('patient_workspace') or {}
        if (binding.get('source_id') != self.workspace.source_id or
                binding.get('project_id') != self.workspace.schema.project_id or binding.get('patient_id') != patient['patientKey']):
            raise WorkspaceError('任务不属于当前患者。', 404)
        if job['attempts'] and job['attempts'][-1].get('run_id'):
            progress = self.collaboration_progress(session, patient_id, job, binding)
            if progress is not None:
                job['collaboration'] = progress
        return job

    def collaboration_progress(self, session, patient_id, job, binding):
        """Project only task metadata after rechecking the report's patient scope."""
        import sqlite3
        from contextlib import closing

        from neurogra.clinical.collaboration_contracts import SubjectScope

        run_id = job['attempts'][-1]['run_id']
        path = self.app.inside(job['config']['database'])
        if not path.is_file():
            return None
        try:
            with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=5)) as db:
                db.row_factory = sqlite3.Row
                row = db.execute('SELECT entry_kind,scope,state,graph_version FROM collaboration_runs WHERE run_id=?',
                                 (run_id,)).fetchone()
                if row is None:
                    return None
                scope = SubjectScope.model_validate_json(row['scope'])
                if (row['entry_kind'] != 'report' or scope.source_id != binding['source_id'] or
                        scope.project_id != str(binding['project_id']) or scope.primary_patient != patient_id or
                        scope.comparison_patients or scope.cohort_binding):
                    raise WorkspaceError('报告协作记录的患者范围不一致。', 403)
                self.workspace.authorize_forms(session, patient_id)
                tasks = [dict(item) for item in db.execute('''SELECT n.task_id,n.task_version,n.kind,
                    n.agent_type,n.state FROM collaboration_nodes n JOIN collaboration_graph_nodes g
                    ON n.run_id=g.run_id AND n.task_id=g.task_id AND n.task_version=g.task_version
                    WHERE g.run_id=? AND g.graph_version=? ORDER BY g.ordinal''',
                    (run_id, row['graph_version']))]
                status = row['state']
                if (job['attempts'][-1]['status'] in {'interrupted', 'cancelled', 'failed'} and
                        status not in {'completed', 'partial', 'failed', 'cancelled'}):
                    # An expired platform worker cannot safely write its own
                    # clinical interruption. Show the effective state until a
                    # new owner can reconcile the durable graph.
                    status = 'interrupted'
                    for task in tasks:
                        if task['state'] == 'running':
                            task['state'] = 'interrupted'
                return {'status': status, 'graph_version': row['graph_version'], 'tasks': tasks}
        except sqlite3.OperationalError as error:
            if 'no such table' in str(error):
                return None
            raise WorkspaceError('报告协作进度暂时不可用。', 503) from None

    def artifact(self, session, patient_id, job_id, artifact_id):
        job = self.job(session, patient_id, job_id)
        source = json.loads(self.app.store.version(job['version_id'])['source'])
        used_forms = {item.get('form_id') for item in source.get('forms', []) if isinstance(item, dict)}
        current_forms = {item.form_id for item in self.workspace.schema.forms}
        if not used_forms <= current_forms:
            raise WorkspaceError('报告使用的表单已不在当前授权配置中。', 403)
        self.workspace.authorize_forms(session, patient_id)
        attempt = next((attempt for attempt in job['attempts'] if any(item['artifact_id'] == artifact_id for item in attempt['artifacts'])), None)
        if attempt is None:
            raise WorkspaceError('报告不属于当前分析任务。', 404)
        report_file = next((item for item in attempt['artifacts'] if item['kind'] == 'json'), None)
        if report_file is None:
            raise WorkspaceError('报告尚未生成。', 409)
        _, path = self.app.artifact(report_file['artifact_id'])
        report = json.loads(path.read_text(encoding='utf-8'))
        if (report.get('patient_id') != job['payload']['patient_workspace']['patient_number'] or
                report.get('run_id') != attempt['run_id']):
            raise WorkspaceError('报告与患者或执行记录不匹配。', 409)
        return self.app.artifact(artifact_id)
