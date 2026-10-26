"""Read the real standalone export format without live RWE, models, or imaging inference."""
import copy
import importlib.util
import io
import json
import runpy
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from neurogra.clinical.config import AgentConfig, ClinicalConfig, KnowledgeAccess
from neurogra.clinical.intake import ingest_patient
from neurogra.clinical.patient_data import PatientDataService
from neurogra.clinical.profiling import profile_case
from neurogra.clinical.schemas import CaseSnapshot, PatientInput, RequestContent
from neurogra.clinical.service import prepare_patient
from neurogra.clinical.storage import RunStore
from neurogra.clinical.utils import fingerprint


ROOT = Path(__file__).resolve().parents[4]
SPEC = importlib.util.spec_from_file_location('rwe_json_test_exporter', ROOT / 'code/tools/export_rwe_patient.py')
EXPORTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EXPORTER)


def exported_document(path, trim_mode='short'):
    rows = {
        56: [
            {'id': 1, 'patient_id': 900001, '访视日期': '2024-01-01', 'MMSE总分': 28,
             '分项': 1, '备注': 'not exported'},
            {'id': 2, 'patient_id': 900001, '访视日期': '2025-01-01', 'MMSE总分': 26,
             '分项': 0, '备注': 'not exported'},
        ],
        87: [{'id': 3, 'patient_id': 900001, '年龄': 70, '身高': 165, '体重': 60,
              '出生日期': '1954/01/01', '性别': '合成示例', '患病情况': None}],
        88: [{'id': 4, 'patient_id': 900001, '血液采集日期': '2025-01-01',
              '陌生字段/含~': '2.0', '零值': 0, '缺失值': None,
              '测定': {'结果': 2.1, '单位': 'pg/mL'}}],
    }
    args = EXPORTER.export_arguments('SYNTHETIC_JSON_PATIENT', path,
                                    {'RWE_API_TOKEN': 'synthetic-token'}, trim_mode=trim_mode)
    with patch.object(EXPORTER, 'dependencies', return_value=(object(), object())), \
         patch.object(EXPORTER, 'login_token', return_value='synthetic-token'), \
         patch.object(EXPORTER, 'resolve_patient', return_value={
             'patient_id': 900001, 'patient_number': args.patient_number}), \
         patch.object(EXPORTER, 'api_query_form', side_effect=lambda _req, _args, form, _pid: rows.get(form, [])):
        return EXPORTER.export_patient(args)


def resolve_pointer(document, pointer):
    for key in pointer.lstrip('/').split('/'):
        key = key.replace('~1', '/').replace('~0', '~')
        document = document[int(key)] if isinstance(document, list) else document[key]
    return document


class RwePatientJsonTests(unittest.TestCase):
    def setUp(self):
        (ROOT / 'tmp').mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix='rwe-json-', dir=ROOT / 'tmp')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / 'patient.json'
        self.document = exported_document(self.path)
        self.config = ClinicalConfig(project_root=self.root, text_knowledge_config=None,
            main_agent=KnowledgeAccess(use_kb=False, use_kg=False),
            agents={'cognition': AgentConfig(use_kb=False, use_kg=False)})

    def snapshot(self, document=None):
        return profile_case(ingest_patient(document or self.document, self.config), [], self.config)

    def test_export_defaults_to_generic_short_view_and_writes_manifest(self):
        import hashlib
        rows = self.document['forms']['mmse']['records']
        self.assertNotIn('分项', rows[0])
        self.assertEqual(rows[1]['分项'], 0)
        self.assertTrue(all('备注' not in row for row in rows))
        self.assertNotIn('timeline', self.document)
        manifest = json.loads(self.path.with_suffix('.manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['mode'], 'short')
        self.assertEqual(manifest['policy']['complete_detail_years'], [])
        self.assertEqual(manifest['output_sha256'], hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertEqual(manifest['source_note_columns_removed'][0]['omitted_fields'], ['备注'])

    def test_full_export_option_retains_visits_and_details_without_remarks(self):
        document = exported_document(self.root / 'full.json', trim_mode='none')
        self.assertEqual(document['forms']['mmse']['records'][0]['分项'], 1)
        self.assertNotIn('备注', document['forms']['mmse']['records'][0])
        self.assertIn('timeline', document)

    def test_main_entry_accepts_unmodified_export_and_check_mode(self):
        entry = runpy.run_path(str(ROOT / 'code/main.py'))
        entry['read_patient_json'].__globals__['PATIENT_FILE'] = self.path
        before = self.path.read_bytes()
        self.assertEqual(entry['read_patient_json'](), self.document)
        with patch('sys.argv', ['main.py', '--patient', str(self.path), '--check']), redirect_stdout(io.StringIO()) as output:
            entry['main']()
        self.assertIn('校验通过', output.getvalue())
        self.assertEqual(self.path.read_bytes(), before)
        self.assertNotIn('患者ID', self.document)

    def test_forms_supply_each_observation_once_with_dates(self):
        snapshot = self.snapshot()
        scores = [row for row in snapshot.observations if row.name == 'MMSE总分']
        self.assertEqual([row.value for row in scores], [28, 26])
        self.assertEqual([row.time.value for row in scores], ['2024-01-01', '2025-01-01'])
        self.assertTrue(all(row.context['名称'] == 'MMSE量表' for row in scores))
        self.assertTrue(all(row.observation_id in snapshot.timeline for row in scores))
        self.assertEqual(sum(row.name == '年龄' for row in snapshot.observations), 1)
        metadata = {'form_id', 'record_id', 'record_count', 'visit_date', 'schema_version', 'status', 'note'}
        self.assertFalse(any(row.name in metadata for row in snapshot.observations))
        self.assertFalse(any('/timeline/' in row.locator['json_pointer'] for row in snapshot.records))

    def test_every_source_pointer_resolves_to_unchanged_raw_value(self):
        original = copy.deepcopy(self.document)
        intake = ingest_patient(self.document, self.config)
        self.assertEqual(intake.raw_snapshot, original)
        self.assertEqual(intake.raw_input_hash, fingerprint(original))
        self.assertEqual(self.document, original)
        for row in intake.records:
            self.assertEqual(resolve_pointer(original, row.locator['json_pointer']), row.raw_value)
            if 'rwe' in row.locator:
                self.assertEqual(resolve_pointer(original, row.locator['rwe']['value_pointer']), row.raw_value)
                self.assertTrue(row.locator['rwe']['export_id'])
        field = next(row for row in intake.records if row.raw_value == '2.0')
        self.assertEqual(field.locator['json_pointer'], '/forms/plasma_biomarkers/records/0/陌生字段~1含~0')
        self.assertEqual(field.context['来源']['form_key'], 'plasma_biomarkers')
        zero = next(row for row in self.snapshot().observations if row.name == '零值')
        self.assertEqual(zero.value, 0)
        self.assertEqual(zero.status, 'observed')
        missing = next(row for row in self.snapshot().observations if row.name == '缺失值')
        self.assertIsNone(missing.value)
        self.assertEqual(missing.status, 'not_recorded')

    def test_imaging_paths_use_existing_asset_boundary_and_pending_state(self):
        document = copy.deepcopy(self.document)
        allowed = self.config.resolve(self.config.patient_data_root)
        allowed.mkdir(parents=True)
        image = allowed / 'synthetic_mri.nii.gz'
        image.write_bytes(b'synthetic placeholder; no imaging inference')
        document['imaging']['mri']['path'] = str(image)
        document['imaging']['mri']['date'] = '2025-01-01'
        outside = self.root / 'outside.nii.gz'
        outside.write_bytes(b'synthetic')
        document['imaging']['pet']['path'] = str(outside)
        intake = ingest_patient(document, self.config)
        images = {row.modality: row for row in intake.images}
        self.assertEqual(images['MRI'].path, str(image.resolve()))
        self.assertEqual(images['MRI'].status, 'available')
        self.assertEqual(images['MRI'].time.value, '2025-01-01')
        self.assertTrue(images['MRI'].source_refs)
        self.assertEqual(images['PET'].status, 'forbidden')
        self.assertFalse(intake.attachments)
        pending = ingest_patient(self.document, self.config)
        self.assertEqual(len(pending.images), 2)
        self.assertTrue(all(row.path is None and row.status == 'path_pending_manual_input' for row in pending.images))

    def test_quality_and_form_catalog_are_retained_without_becoming_clinical_facts(self):
        document = copy.deepcopy(self.document)
        document['data_quality']['warnings'].append({'form': 'CDR量表', 'message': '合成质量提示'})
        document['demographics']['教育年限'] = 12
        document['附加病史'] = '合成补充记录'
        snapshot = self.snapshot(document)
        self.assertTrue(any(row.code == 'rwe_data_quality_warning' and row.message == '合成质量提示'
                            for row in snapshot.issues))
        self.assertTrue(any(row.code == 'rwe_form_empty' for row in snapshot.issues))
        self.assertTrue(any(row.name == '教育年限' and row.value == 12 for row in snapshot.observations))
        self.assertTrue(any(row.name == '附加病史' for row in snapshot.observations))
        catalog = PatientDataService(snapshot, self.config).list_available_data()
        self.assertEqual(len(catalog['forms']), 9)
        self.assertEqual(catalog['data_quality'], document['data_quality'])
        self.assertEqual(snapshot.patient_metadata['patient'], document['patient'])

    def test_agents_fetch_local_forms_including_empty_forms_without_remote_access(self):
        service = PatientDataService(self.snapshot(), self.config)
        def request(key, fields=()):
            return RequestContent(type='patient_data', form_key=key, requested_fields=list(fields),
                                  question='读取已有表单', reason='核对原始记录')
        with patch.object(service, '_remote', side_effect=AssertionError('Complete JSON is the data source')):
            result = service.fetch_patient_data(request('mmse'), 'test-request')
            empty = service.fetch_patient_data(request('apoe_genotype', ['基因型']), 'test-empty')
            unknown = service.fetch_patient_data(request('not-a-form'), 'test-unknown')
        self.assertEqual(result.status, 'found')
        self.assertEqual([row.value for row in result.observations], [28, 26, 0])
        self.assertEqual(len(result.records), 3)
        self.assertEqual(empty.status, 'not_recorded')
        self.assertEqual(empty.missing_fields, ['基因型'])
        self.assertEqual(unknown.status, 'unsupported')
        self.assertEqual(service.read_source(result.records[0].record_id).raw_value, 28)

    def test_missing_visit_date_is_unknown_and_zero_is_not_missing(self):
        document = copy.deepcopy(self.document)
        document['forms']['mmse']['records'][0]['visit_date'] = None
        document['forms']['mmse']['records'][0]['MMSE总分'] = 0
        scores = [row for row in self.snapshot(document).observations if row.name == 'MMSE总分']
        self.assertEqual(scores[0].value, 0)
        self.assertEqual(scores[0].status, 'observed')
        self.assertIsNone(scores[0].time.value)
        self.assertEqual(scores[0].time.precision, 'unknown')

    def test_clinical_source_column_does_not_replace_provenance_or_form_binding(self):
        document = copy.deepcopy(self.document)
        document['forms']['mmse']['records'][0]['来源'] = '合成临床资料来源'
        snapshot = self.snapshot(document)
        score = next(row for row in snapshot.observations if row.name == 'MMSE总分')
        self.assertEqual(score.context['来源'], '合成临床资料来源')
        self.assertEqual(score.context['rwe_source']['form_key'], 'mmse')
        source = next(row for row in snapshot.records if row.record_id in score.source_refs)
        self.assertEqual(source.locator['rwe']['value_pointer'], '/forms/mmse/records/0/MMSE总分')
        request = RequestContent(type='patient_data', form_key='mmse', requested_fields=['MMSE总分'],
                                 question='读取量表', reason='核对原始记录')
        result = PatientDataService(snapshot, self.config).fetch_patient_data(request, 'test-source')
        self.assertEqual([row.value for row in result.observations], [28, 26])

    def test_preparation_persists_complete_source_and_restores_local_form_access(self):
        preparation = prepare_patient(self.document, self.config, use_model=False)
        store = RunStore(self.config.resolve(self.config.database))
        saved = store.objects(preparation.run_id, 'intake')[-1]['payload']
        self.assertEqual(saved['raw_snapshot'], self.document)
        restored = CaseSnapshot.model_validate(store.objects(preparation.run_id, 'snapshot')[-1]['payload'])
        self.assertEqual(restored.patient_id, self.document['patient']['patient_number'])
        self.assertEqual(len(PatientDataService(restored, self.config).list_available_data()['forms']), 9)

    def test_invalid_identity_or_shape_is_rejected(self):
        documents = []
        for number in (None, 123, '', '   '):
            document = copy.deepcopy(self.document)
            document['patient']['patient_number'] = number
            documents.append(document)
        document = copy.deepcopy(self.document)
        document['患者ID'] = 'OTHER_PATIENT'
        documents.append(document)
        document = copy.deepcopy(self.document)
        document['forms']['mmse']['records'][0]['patient_id'] = 999
        documents.append(document)
        for records in ({}, ['not-a-record']):
            document = copy.deepcopy(self.document)
            document['forms']['mmse']['records'] = records
            documents.append(document)
        for key in ('project', 'imaging', 'data_quality'):
            document = copy.deepcopy(self.document)
            document[key] = []
            documents.append(document)
        for index, document in enumerate(documents):
            with self.subTest(index=index), self.assertRaises(ValueError):
                PatientInput.model_validate(document)
        document = copy.deepcopy(self.document)
        document['forms']['mmse']['records'][0]['MMSE总分'] = float('nan')
        with self.assertRaises(ValueError):
            ingest_patient(document, self.config)


if __name__ == '__main__':
    unittest.main()
