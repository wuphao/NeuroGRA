"""Read-only RWE connector. Form schema is configuration, never patient-specific logic."""
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml
from pydantic import Field

from .schemas import Contract
from .utils import fingerprint, write_json


class RweFailure(RuntimeError):
    """Stable error codes; do not echo credentials or remote response bodies."""


class RweForm(Contract):
    key: str
    form_id: int = Field(gt=0)
    name: str
    date_field: str | None = None


class RweConfig(Contract):
    project_id: int = Field(gt=0)
    settings_file: Path = Path('.env.rwe.local')
    timeout_seconds: float = Field(default=30, gt=0, le=120)
    forms: list[RweForm] = Field(min_length=1)


def load_rwe_config(path):
    config = RweConfig.model_validate(yaml.safe_load(Path(path).read_text(encoding='utf-8-sig')))
    if len({f.key for f in config.forms}) != len(config.forms) or len({f.form_id for f in config.forms}) != len(config.forms):
        raise ValueError('duplicate_rwe_form')
    return config


def local_settings(path):
    values = {}
    if Path(path).is_file():
        for line in Path(path).read_text(encoding='utf-8-sig').splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                key, value = line.split('=', 1)
                values[key.strip()] = value.strip().strip('\"\'')
    values.update({k: v for k, v in os.environ.items() if k.startswith('RWE_')})
    return values


class RweClient:
    def __init__(self, config, settings):
        self.config, self.settings = config, settings

    def resolve_patient(self, patient_number):
        if not isinstance(patient_number, str) or not patient_number.strip():
            raise RweFailure('invalid_patient_number')
        try:
            import psycopg2
        except ImportError:
            raise RweFailure('rwe_dependency_missing:psycopg2') from None
        s = self.settings
        try:
            connection = psycopg2.connect(host=s.get('RWE_DB_HOST', 'localhost'),
                port=int(s.get('RWE_DB_PORT', '5432')), dbname=s.get('RWE_DB_NAME', 'rwe_nexus_develop'),
                user=s.get('RWE_DB_USER', 'postgres'), password=s.get('RWE_DB_PASSWORD', ''), connect_timeout=10)
            try:
                connection.set_session(readonly=True, autocommit=True)
                with connection.cursor() as cursor:
                    cursor.execute("SET statement_timeout = '10000'")
                    cursor.execute('''SELECT pp.patient_id, TRIM(pp.patient_number)
                        FROM patient_project pp JOIN patient p ON p.id=pp.patient_id
                        WHERE pp.project_id=%s AND pp.is_delete=0 AND p.is_delete=0
                        AND TRIM(pp.patient_number)=%s''', (self.config.project_id, patient_number.strip()))
                    rows = cursor.fetchall()
            finally:
                connection.close()
        except (psycopg2.Error, ValueError):
            raise RweFailure('rwe_database_unavailable') from None
        if not rows:
            raise RweFailure('patient_not_found')
        if len(rows) != 1:
            raise RweFailure('patient_number_ambiguous')
        return {'patient_id': int(rows[0][0]), 'patient_number': rows[0][1]}

    def fetch_form(self, form, patient_id):
        try:
            import requests
        except ImportError:
            raise RweFailure('rwe_dependency_missing:requests') from None
        token = self.settings.get('RWE_API_TOKEN', '').strip()
        if not token:
            raise RweFailure('rwe_token_missing')
        authorization = token if token.lower().startswith('bearer ') else 'Bearer ' + token
        url = self.settings.get('RWE_API_BASE_URL', 'http://localhost:8081').rstrip('/') + '/form/queryData'
        try:
            with requests.post(url, json={'formId': form.form_id, 'page': 0, 'pageSize': 0,
                    'criteria': [{'enName': 'patient_id', 'inputValue': patient_id, 'isFuzzy': False}]},
                    headers={'Authorization': authorization}, timeout=self.config.timeout_seconds,
                    allow_redirects=False) as response:
                if response.status_code in (401, 403):
                    raise RweFailure('rwe_authentication_or_permission_failed')
                if response.status_code != 200:
                    raise RweFailure('rwe_http_error:' + str(response.status_code))
                payload = response.json()
        except requests.RequestException:
            raise RweFailure('rwe_api_unavailable') from None
        except ValueError:
            raise RweFailure('rwe_invalid_json') from None
        if not isinstance(payload, dict) or payload.get('code') != 0:
            raise RweFailure('rwe_api_business_error')
        data = payload.get('data')
        if not isinstance(data, dict) or not isinstance(data.get('list'), list):
            raise RweFailure('rwe_invalid_form_response')
        rows = data['list']
        # A filtered request alone is insufficient to establish patient identity.
        for row in rows:
            if not isinstance(row, dict) or str(row.get('patient_id', row.get('patientId'))) != str(patient_id):
                raise RweFailure('rwe_patient_binding_failed')
        total = data.get('total', data.get('count'))
        if total is not None and int(total) != len(rows):
            raise RweFailure('rwe_incomplete_form_response')
        return rows


SYSTEM_FIELDS = {'id', 'patient_id', 'patientId', 'parent_id', 'parentId', 'parent_field_id',
                 'parentFieldId', 'is_delete', 'isDelete', 'create_time', 'createTime', 'update_time', 'updateTime'}


def adapt_export(export):
    """Lossless source is stored separately; clinical view never recalculates scores."""
    raw = {'患者ID': export['patient']['patient_number'], 'RWE资料': []}
    for form_index, form in enumerate(export['forms']):
        for index, row in enumerate(form['records']):
            fields = {k: v for k, v in row.items() if k not in SYSTEM_FIELDS and k != form['date_field']}
            raw['RWE资料'].append({'名称': form['name'], '时间': row.get(form['date_field']),
                '来源': {'type': 'rwe', 'project_id': export['project_id'], 'form_id': form['form_id'],
                         'record_id': row.get('id'), 'export_id': export['export_id'],
                         'row_pointer': f"/forms/{form_index}/records/{index}"},
                '原始记录': fields})
    return raw


def export_patient(patient_number, clinical_config, rwe_config, client=None):
    client = client or RweClient(rwe_config, local_settings(clinical_config.resolve(rwe_config.settings_file)))
    export_id = uuid.uuid4().hex
    directory = clinical_config.resolve(clinical_config.data_root) / 'rwe' / export_id
    export = {'export_id': export_id, 'project_id': rwe_config.project_id,
              'fetched_at': datetime.now(timezone.utc).isoformat(),
              'patient': client.resolve_patient(patient_number), 'forms': [], 'issues': []}
    if export['patient']['patient_number'].strip() != patient_number.strip():
        raise RweFailure('rwe_patient_binding_failed')
    for form in rwe_config.forms:
        try:
            records = client.fetch_form(form, export['patient']['patient_id'])
            status = 'available' if records else 'empty'
        except RweFailure as exc:
            if str(exc) == 'rwe_patient_binding_failed':
                raise
            records, status = [], 'failed'
            export['issues'].append({'form_id': form.form_id, 'code': str(exc)})
        export['forms'].append({**form.model_dump(mode='json'), 'records': records, 'status': status})
    path = directory / 'source.json'
    write_json(path, export)
    if not any(form['records'] for form in export['forms']):
        code = 'rwe_source_unavailable' if export['issues'] else 'patient_has_no_records'
        raise RweFailure(code + ':export_id=' + export_id)
    raw = adapt_export(export)
    write_json(directory / 'patient.json', raw)
    return raw, export, path


def analyze_rwe(patient_number, config, rwe_config, on_run_created=None):
    from .service import create_context, prepare_run
    from .workflow import continue_run
    # RWE reports always require grounding, including when a legacy clinical
    # configuration is explicitly supplied at the CLI.
    config = config.model_copy(update={'verify_narrative': True})
    raw, export, source_path = export_patient(patient_number, config, rwe_config)
    store, run_id = create_context(config)
    if on_run_created is not None:
        on_run_created(run_id)
    with store.workflow_lock(run_id):
        from neurogra.knowledge.utils import sha256_file
        store.save(run_id, 'rwe_source', 'main', {'path': str(source_path), 'sha256': sha256_file(source_path),
                   'export_id': export['export_id'], 'issues': export['issues'],
                   'forms': [{k: v for k, v in f.items() if k != 'records'} | {'record_count': len(f['records'])}
                             for f in export['forms']]})
        store.save(run_id, 'patient_input', 'main', {'raw': raw, 'use_model': True, 'query': None})
        preparation = prepare_run(raw, config, store, run_id)
    return continue_run(preparation, config)
