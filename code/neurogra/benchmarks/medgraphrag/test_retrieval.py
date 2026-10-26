"""Regression cases for real-corpus context limits and evidence provenance."""
import unittest
from pydantic import ValidationError

from .retrieval import compact_patient, summary_preview, retrieve, prompt_graph, score_trends
from .runtime import Config, request_fits
from .pipeline import Prediction, validate_prediction, comparison_labels
from .test_adapter import patient, FakeModel
from neurogra.benchmarks.patient_view import prepare_patient


class RetrievalTests(unittest.TestCase):
    def test_stage_and_etiology_cannot_be_silently_defaulted(self):
        fields = FakeModel().generate('', {}, '', Prediction).model_dump()
        for field in ('cognitive_stage', 'etiology_label'):
            incomplete = dict(fields)
            incomplete.pop(field)
            with self.assertRaises(ValidationError):
                Prediction.model_validate(incomplete)

    def test_compaction_preserves_context_and_every_typed_fact(self):
        prepared = prepare_patient(patient())
        compact, items = compact_patient(prepared)
        restored = {}
        for group in compact['groups']:
            for ref, field, value in group['facts']:
                restored[ref] = (group['path'] + '/' + field, group['context'], value, type(value))
        self.assertEqual(len(restored), len(items))
        for fact in prepared['sources']:
            self.assertEqual(restored[fact['id']], (fact['pointer'], fact['context'],
                                                   fact['value'], type(fact['value'])))

    def test_large_summary_preview_is_bounded_and_verbatim(self):
        text = 'start' + 'x' * 50000 + 'end'
        preview = summary_preview(text, 1800)
        self.assertLessEqual(sum(len(e['text']) for e in preview), 1800)
        self.assertEqual(preview[-1]['end'], len(text))
        for e in preview:
            self.assertEqual(text[e['start']:e['end']], e['text'])

    def test_trends_use_dated_numeric_values_without_boolean_or_diagnosis(self):
        facts = [{'id': 'P2', 'pointer': '/form/总分', 'context': {'form': 'test', 'date': '2019-01-01'}, 'value': 20},
                 {'id': 'P1', 'pointer': '/form/总分', 'context': {'form': 'test', 'date': '2017-01-01'}, 'value': 15},
                 {'id': 'P3', 'pointer': '/form/总分', 'context': {'form': 'test', 'date': None}, 'value': 100},
                 {'id': 'P4', 'pointer': '/form/总分', 'context': {'form': 'test', 'date': '2020-01-01'}, 'value': False}]
        summary = score_trends(facts)
        trend = dict(zip(summary['columns'], summary['rows'][0]))
        self.assertEqual(trend['last_minus_first'], 5)
        self.assertEqual((trend['observed_min'], trend['observed_max']), (15, 20))
        self.assertEqual((trend['first_ref'], trend['last_ref']), ('P1', 'P2'))

    def test_retrieval_is_bounded_preserves_offsets_and_has_no_orphan_edges(self):
        graph = {'gid': 'g', 'entities': [
            {'id': n, 'name': n, 'type': 'test', 'refs': ['K1'], 'embedding': [1, 0]}
            for n in ('a', 'b')], 'relationships': [
                {'source': 'a', 'target': 'b', 'kind': 'related', 'description': 'test', 'refs': ['K1']}]}
        original = 'irrelevant ' * 100 + 'MMSE memory ' * 100
        refs = {'K1': {'text': original, 'document_id': 'g', 'page': 3, 'offset': 2400}}
        context, evidence, record = retrieve([graph], {'g'}, {'gid': 'p'}, [], [1, 0],
            refs, 'MMSE memory', Config(evidence_excerpt_chars=300), lambda c, r: True)
        e = evidence['K1']
        self.assertEqual(e['text'], original[e['excerpt_start']:e['excerpt_end']])
        self.assertEqual(e['page_text_offset'], 2400 + e['excerpt_start'])
        self.assertGreater(e['excerpt_start'], 0)
        self.assertEqual(record['patient_facts_omitted'], 0)
        ids = {(n['gid'], n['id']) for n in context['entities']}
        self.assertTrue(context['relationships'])
        for rel in context['relationships']:
            self.assertIn((rel['gid'], rel['source']), ids)
            self.assertIn((rel['gid'], rel['target']), ids)
        short = prompt_graph(context)
        self.assertEqual([e['refs'] for e in short['entities']], [e['refs'] for e in context['entities']])
        alias_ids = {e['id'] for e in short['entities']}
        for rel in short['relationships']:
            self.assertIn(rel['source'], alias_ids)
            self.assertIn(rel['target'], alias_ids)
        with self.assertRaisesRegex(ValueError, 'no_graph_evidence'):
            retrieve([graph], {'g'}, {'gid': 'p'}, [], [1, 0], refs, 'MMSE',
                     Config(), lambda c, r: False)

    def test_budget_counts_utf8_and_reserves_revision_space(self):
        cfg = Config(context_tokens=4096, output_tokens=1024)
        self.assertTrue(request_fits(cfg, 'test', 'x' * 1000))
        self.assertFalse(request_fits(cfg, 'test', '甲' * 1100))
        self.assertFalse(request_fits(cfg, 'test', 'x' * 1000, reserve_bytes=2500))

    def test_stage_and_etiology_are_separate_labels(self):
        p = FakeModel().generate('', {}, '', Prediction)
        p.cognitive_stage = 'MCI'
        p.etiology_label = p.primary_label = 'AD'
        validate_prediction(p, 'clinical_review', set(), set())
        self.assertEqual(comparison_labels(p)['evaluation_class'], 'MCI')
        p.cognitive_stage = 'DEMENTIA'
        self.assertEqual(comparison_labels(p)['evaluation_class'], 'AD')
        p.cognitive_stage = 'NC'
        self.assertEqual(comparison_labels(p)['neurogra_syndrome_class'], 'CN')
        p.primary_label = 'MCI'
        with self.assertRaisesRegex(ValueError, 'etiology'):
            validate_prediction(p, 'clinical_review', set(), set())


if __name__ == '__main__':
    unittest.main()
