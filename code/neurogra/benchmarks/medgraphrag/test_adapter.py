"""Scientific-input and isolation regressions. No model/network dependencies."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from neurogra.benchmarks.patient_view import prepare_patient
from neurogra.benchmarks.medgraphrag.runtime import Config, LocalModel
from neurogra.benchmarks.medgraphrag.pipeline import (
    Extraction, Prediction, Rating, load_knowledge, pack, link_graphs, run_pipeline,
    validate_prediction, analyze_chunk,
)


def temporary_directory():
    # Windows sandbox temp files can deny atomic rename; exercise the real workspace filesystem.
    root = Path(__file__).resolve().parents[4] / 'output/baselines/medgraphrag/tests'
    root.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=root)


def patient():
    return {'schema_version': '2.0', 'patient': {'patient_number': 'synthetic', 'patient_id': 9},
            'forms': {'mmse': {'form_name': 'MMSE', 'records': [
                {'record_id': 'old', 'visit_date': '2024-01-01', 'MMSE总分': 0, '分项': False},
                {'record_id': 'future', 'visit_date': '2026-01-01', 'MMSE总分': 20, '分项': 1},
                {'record_id': 'unknown', 'MMSE总分': None}]}},
            'timeline': [{'diagnosis': 'duplicate should not enter'}],
            'imaging': {'mri': {'path': 'private.nii', 'report_text': 'future report'}}}


class FakeModel:
    def __init__(self):
        self.calls = self.embedding_calls = 0
        self.usage = {}

    def generate(self, system, data, stage, schema=None):
        self.calls += 1
        if schema is Extraction:
            return Extraction(entities=[{'name': 'MMSE', 'type': 'Test', 'description': 'original finding',
                                         'refs': [data[0]['ref']]}], relationships=[])
        if schema is Rating:
            return Rating(rating=4)
        if schema is Prediction:
            return Prediction(primary_label='UNRESOLVED', cognitive_stage='UNRESOLVED',
                              etiology_label='UNRESOLVED', differential_labels=[], assessment_status='insufficient_data',
                              supporting_findings=[], opposing_findings=[], knowledge_references=[],
                              missing_information=['资料不足'], report='资料不足。')
        return 'MMSE summary'

    def embed(self, text):
        self.embedding_calls += 1
        return [1.0, 0.0]


class FakeStore:
    def __init__(self):
        self.graphs, self.links = [], []

    def save(self, run, graph):
        self.graphs.append((run, graph))

    def link(self, run, links):
        self.links.extend(links)


class AdapterTests(unittest.TestCase):
    def test_lossless_zero_false_null_and_no_duplicate_or_path(self):
        result = prepare_patient(patient())
        values = [f['value'] for f in result['sources']]
        self.assertTrue(any(type(v) is int and v == 0 for v in values))
        self.assertTrue(any(v is False for v in values))
        self.assertIn(None, values)
        self.assertNotIn('private.nii', result['text'])
        self.assertNotIn('duplicate should not enter', result['text'])

    def test_cutoff_before_trim_and_original_pointer(self):
        policy = {'reviewed': True, 'cutoff': '2025-01-01', 'trim_mode': 'short', 'exclude_pointers': {}}
        result = prepare_patient(patient(), task='adni_stage', policy=policy)
        rows = result['visible']['forms']['mmse']['records']
        self.assertEqual(len(rows), 1)
        self.assertIn('分项', rows[0])  # Latest at the cutoff, not latest in future.
        self.assertEqual(result['sources'][0]['original_pointer'], '/forms/mmse/records/0/MMSE总分')
        self.assertNotIn('future report', result['text'])
        self.assertEqual(len(patient()['forms']['mmse']['records']), 3)

    def test_shifted_rows_keep_original_source_location(self):
        raw = patient()
        raw['forms']['mmse']['records'].reverse()
        result = prepare_patient(raw, task='adni_stage', policy={
            'reviewed': True, 'cutoff': '2025-01-01', 'exclude_pointers': {}})
        self.assertEqual(result['sources'][0]['pointer'], '/forms/mmse/records/0/MMSE总分')
        self.assertEqual(result['sources'][0]['original_pointer'], '/forms/mmse/records/2/MMSE总分')

    def test_evaluation_requires_audit_and_existing_pointers(self):
        with self.assertRaisesRegex(ValueError, 'reviewed'):
            prepare_patient(patient(), task='nacc_etiology')
        with self.assertRaisesRegex(ValueError, 'unmatched'):
            prepare_patient(patient(), policy={'exclude_pointers': {'/made_up': 'remove'}})

    def test_legacy_preserves_context_but_not_path(self):
        result = prepare_patient({'患者ID': 'synth', '检验': {'时间': '2024-01-01',
                                 '单位': 'pg/mL', '结果': 0, '路径': 'private.pdf'}})
        finding = next(f for f in result['sources'] if f['pointer'].endswith('/结果'))
        self.assertEqual(finding['context']['单位'], 'pg/mL')
        self.assertNotIn('private.pdf', result['text'])

    def test_packing_never_loses_characters(self):
        items = [{'ref': 'P1', 'text': '甲乙丙丁戊'*100}]
        chunks = pack(items, 53)
        self.assertEqual(''.join(i['text'] for c in chunks for i in c), items[0]['text'])

    def test_coverage_accounts_for_every_original_leaf(self):
        from neurogra.benchmarks.patient_view import leaves
        raw = patient()
        result = prepare_patient(raw)
        covered = result['manifest']['field_coverage']
        self.assertEqual({r['pointer'] for r in covered}, {p for p, _ in leaves(raw)})

    def test_conflicting_demographics_are_preserved(self):
        raw = patient()
        raw['forms']['dicom_basic_info'] = {'records': [{'年龄': 70, '性别': '男'}]}
        raw['demographics'] = {'年龄': 71, '性别': '男'}
        result = prepare_patient(raw)
        self.assertEqual(result['visible']['demographics'], {'年龄': 71})
        self.assertIn(70, [f['value'] for f in result['sources']])
        self.assertIn(71, [f['value'] for f in result['sources']])

    def test_missing_referenced_document_fails_not_silently_skips(self):
        with temporary_directory() as directory:
            path = Path(directory)/'manifest.json'
            path.write_text(json.dumps({'version': 'test', 'documents': [{
                'id': 'missing', 'layer': 'middle', 'path': 'absent.txt',
                'source': 'test', 'license_note': 'test'}]}), encoding='utf-8')
            with self.assertRaises(FileNotFoundError):
                load_knowledge(path, 500)

    def test_no_cloud_or_default_database(self):
        with self.assertRaisesRegex(ValueError, 'local_Ollama'):
            Config(base_url='https://example.com').validate_local()
        with self.assertRaisesRegex(ValueError, 'dedicated'):
            Config(neo4j_database='neo4j').validate_local()
        with self.assertRaisesRegex(ValueError, 'qwen'):
            Config(model='gpt-4o').validate_local()

    def test_bad_references_are_not_accepted(self):
        p = FakeModel().generate('', {}, 'final', Prediction)
        p.knowledge_references = ['K999']
        with self.assertRaisesRegex(ValueError, 'unknown_knowledge'):
            validate_prediction(p, 'clinical_review', {'P1'}, set())
        p.knowledge_references = []
        p.report = 'References [K1, K999]'
        with self.assertRaisesRegex(ValueError, 'report_unknown'):
            validate_prediction(p, 'clinical_review', {'P1'}, {'K1'})

    def test_whole_pipeline_cache_and_case_isolation(self):
        with temporary_directory() as directory:
            root = Path(directory)
            (root/'middle.txt').write_text('MMSE guideline synthetic only', encoding='utf-8')
            (root/'bottom.txt').write_text('MMSE dictionary synthetic only', encoding='utf-8')
            manifest = {'version': 'synthetic-v1', 'documents': [
                {'id': name, 'layer': name, 'path': name+'.txt', 'source': 'synthetic',
                 'license_note': 'test fixture'} for name in ('middle', 'bottom')]}
            (root/'sources.json').write_text(json.dumps(manifest), encoding='utf-8')
            docs, refs, corpus_hash = load_knowledge(root/'sources.json', 500)
            config = Config(knowledge_cache=str(root/'cache'))
            store = FakeStore()
            for case in ('case-a', 'case-b'):
                output = root/case
                output.mkdir()
                model = FakeModel()
                result = run_pipeline(prepare_patient(patient()), config, model, store,
                                      output, case, docs, refs, corpus_hash)
                self.assertEqual(result['execution_status'], 'completed')
                self.assertTrue((output/'prediction.json').exists())
                if case == 'case-b':
                    self.assertTrue((output/'cache_events.jsonl').exists())
                    # Corpus graphs are cached; evidence text embeddings are a separate retrieval step.
                    self.assertGreaterEqual(model.embedding_calls, 2)
            tops = [(run, g['gid']) for run, g in store.graphs if g['layer'] == 'top']
            self.assertEqual(tops, [('case-a', 'patient-case-a'), ('case-b', 'patient-case-b')])
            self.assertFalse(any(l['to_gid'].startswith('patient-') for l in store.links))

    def test_context_rejection_happens_before_network(self):
        with temporary_directory() as directory:
            model = LocalModel(Config(max_input_chars=1000), directory)
            with self.assertRaisesRegex(ValueError, 'context_limit'):
                model.generate('test', {'value': '甲'*3000}, 'test')
            self.assertEqual(model.calls, 0)

    def test_model_checkpoint_reuses_only_matching_model_identity(self):
        with temporary_directory() as directory:
            model = LocalModel(Config(model_cache=str(Path(directory)/'cache')), directory)
            model.identity = {'digest': 'first'}
            response = {'message': {'content': '{"entities": [], "relationships": []}'}, 'done_reason': 'stop'}
            with patch.object(model, 'request', return_value=response) as request:
                model.generate('extract', {'ref': 'K1'}, 'extract:test:0', Extraction)
                model.generate('extract', {'ref': 'K1'}, 'extract:test:0', Extraction)
                self.assertEqual(request.call_count, 1)
                model.identity = {'digest': 'second'}
                model.generate('extract', {'ref': 'K1'}, 'extract:test:0', Extraction)
                self.assertEqual(request.call_count, 2)

    def test_corrupt_model_checkpoint_is_rejected(self):
        with temporary_directory() as directory:
            model = LocalModel(Config(model_cache=str(Path(directory)/'cache')), directory)
            cache = model.cache_path('test', {})
            cache.write_text('{"hash": "wrong", "value": "modified"}', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'integrity'):
                model.cached(cache)

    def test_dense_blocks_split_without_losing_text_or_offsets(self):
        class DenseModel(FakeModel):
            def __init__(self):
                super().__init__()
                self.accepted = []

            def generate(self, system, data, stage, schema=None):
                if schema is Extraction:
                    if sum(len(item['text']) for item in data) > 500:
                        raise ValueError('model_output_truncated')
                    self.accepted.extend(data)
                return super().generate(system, data, stage, schema)
        model = DenseModel()
        original = 'medical text ' * 151
        results = analyze_chunk(model, [{'ref': 'K1', 'text': original, 'offset': 23}], 'dense')
        self.assertGreater(len(results), 1)
        self.assertEqual(''.join(item['text'] for item in model.accepted), original)
        self.assertEqual(model.accepted[0]['offset'], 23)
        for left, right in zip(model.accepted, model.accepted[1:]):
            self.assertEqual(left['offset'] + len(left['text']), right['offset'])


if __name__ == '__main__':
    unittest.main()
