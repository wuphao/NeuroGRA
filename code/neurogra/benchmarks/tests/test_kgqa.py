"""Offline regression tests for experiment validity; no models/graph writes."""
import csv
import json
import io
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from neurogra.benchmarks.kgqa.core import (
    GROUPS, bounded_context, canonical, load_queries, read_case, read_json,
    write_case, write_json)
from neurogra.benchmarks.kgqa.review import export_review, score, FIELDS
from neurogra.benchmarks.kgqa.runner import run_cases


class FakeGateway:
    def __init__(self):
        self.calls = self.embedding_calls = 0
        self.usage = {'prompt_tokens': 0, 'output_tokens': 0, 'embedding_tokens': 0}
        self.fail = False
        self.inputs = []

    def preflight(self):
        return {'test': 'digest'}

    def generate(self, system, payload, stage, *, answer=False):
        self.inputs.append(payload)
        self.calls += 1
        if self.fail:
            raise RuntimeError('simulated generation failure')
        return '答案[E1]' if payload['evidence'] else '答案'


class FakeAdapters:
    def __init__(self):
        self.calls = 0

    def retrieve(self, group, question, directory):
        self.calls += 1
        return {'evidence': [] if group == 'llm' else [{'id': 'source', 'citation': 'E1',
                'text': '原文'}], 'kg_used': int(group == 'kg_kb')}


class KGQATest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.base = Path(self.temp.name)
        self.run = self.base / 'run'
        self.gateway, self.adapters = FakeGateway(), FakeAdapters()
        self.questions = [{'id': 'Q08', 'question': '问题一'},
                          {'id': 'Q12', 'question': '问题二'}]

    def tearDown(self):
        self.temp.cleanup()

    def execute(self, **kwargs):
        with patch('neurogra.benchmarks.kgqa.runner.model_bindings'):
            with redirect_stdout(io.StringIO()):
                run_cases({'test': 1}, {'files': {}, 'limitations': []}, self.questions,
                          list(GROUPS), 1, self.run, self.gateway, self.adapters, **kwargs)

    def reviews(self, review_dir, correct='1'):
        mapping = read_json(review_dir / 'private_mapping.json')
        paths = []
        for reviewer in ['A', 'B']:
            p = self.base / (reviewer + '.csv')
            with p.open('w', encoding='utf-8-sig', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=FIELDS)
                writer.writeheader()
                for blind, item in mapping['cases'].items():
                    writer.writerow({'blind_id': blind, 'reviewer': reviewer, 'correct': correct,
                        'key_points_hit': '2', 'key_points_total': '3',
                        'evidence_sufficient': '' if item['group'] == 'llm' else '1',
                        'condition_misuse': '0'})
            paths.append(p)
        return paths

    def test_query_reader_rejects_gold(self):
        p = self.base / 'q.jsonl'
        p.write_text(json.dumps({'id': 'Q01', 'question': 'Q', 'answer': 'secret'}))
        with self.assertRaisesRegex(ValueError, 'gold'):
            load_queries(p)

    def test_query_reader_rejects_duplicate_ids(self):
        p = self.base / 'q.jsonl'
        p.write_text('\n'.join([json.dumps({'id': 'Q01', 'question': 'Q'})] * 2))
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            load_queries(p)

    def test_packing_keeps_whole_units_and_contiguous_citations(self):
        items = [{'id': 'big', 'text': '中' * 1000}, {'id': 'ok', 'text': '原文'},
                 {'id': 'ok', 'text': 'duplicate'}, {'id': 'ok2', 'text': '原文2'}]
        packed, omitted = bounded_context(items, 250)
        self.assertEqual(omitted, ['big'])
        self.assertEqual([r['citation'] for r in packed], ['E1', 'E2'])
        self.assertEqual(packed[0]['text'], '原文')
        self.assertLessEqual(len(canonical(packed).encode('utf-8')), 250)

    def test_case_tampering_is_detected(self):
        p = self.base / 'case.json'
        write_case(p, {'answer': 'original'})
        value = read_json(p)
        value['answer'] = 'changed'
        write_json(p, value)
        with self.assertRaisesRegex(ValueError, 'record_changed'):
            read_case(p)

    def test_four_arm_cross_product_and_resume_skip(self):
        self.execute()
        self.assertEqual(self.gateway.calls, 8)
        self.assertEqual(self.adapters.calls, 8)
        self.assertEqual(read_json(self.run / 'progress.json')['counts']['completed'], 8)
        for payload in self.gateway.inputs:
            self.assertEqual(set(payload), {'question', 'evidence'})
        self.execute(resume=True)
        self.assertEqual(self.gateway.calls, 8)

    def test_existing_output_requires_explicit_resume(self):
        self.execute()
        with self.assertRaisesRegex(ValueError, 'use_resume'):
            self.execute()

    def test_resume_rejects_changed_queries(self):
        self.execute()
        self.questions[0]['question'] = 'changed'
        with self.assertRaisesRegex(ValueError, 'resume_config'):
            self.execute(resume=True)

    def test_errors_are_persisted_and_retry_reuses_retrieval(self):
        self.gateway.fail = True
        self.execute()
        self.assertEqual(read_json(self.run / 'progress.json')['counts']['error'], 8)
        self.execute(resume=True)
        self.assertEqual(self.gateway.calls, 8)
        self.gateway.fail = False
        self.execute(resume=True, retry_errors=True)
        self.assertEqual(self.adapters.calls, 8)
        self.assertEqual(read_json(self.run / 'progress.json')['counts']['completed'], 8)
        record = read_case(self.run / 'cases' / 'Q08_llm_r1.json')
        self.assertEqual(len(record['previous_errors']), 1)
        self.assertEqual(len(record['attempts']), 2)

    def test_review_pending_never_becomes_zero_accuracy(self):
        self.execute()
        review = self.base / 'review'
        self.assertEqual(export_review(self.run, review), 8)
        data = score(self.run, review / 'private_mapping.json', [review / '评分表.csv'])
        self.assertIsNone(data['full_per_repetition']['1']['llm']['accuracy'])
        self.assertEqual(data['full_per_repetition']['1']['llm']['pending_or_disputed'], 2)
        for row in read_json(review / '匿名回答.json'):
            self.assertNotIn('group', row)
        self.assertTrue((review / '证据核查.json').exists())

    def test_two_reviews_accuracy_and_common_subset(self):
        self.execute()
        review = self.base / 'review'
        export_review(self.run, review)
        paths = self.reviews(review)
        data = score(self.run, review / 'private_mapping.json', paths)
        self.assertEqual(data['full_per_repetition']['1']['kg_kb']['accuracy'], 1)
        self.assertEqual(data['common_Q08_Q11_per_repetition']['1']['kg_kb']['expected'], 1)
        self.assertEqual(data['full_per_repetition']['1']['kg_kb']['auxiliary']['key_point_coverage']['mean'], 2/3)
        self.assertIsNone(data['full_per_repetition']['1']['llm']['auxiliary']['evidence_sufficient']['mean'])

    def test_disagreement_and_single_review_are_pending(self):
        self.execute()
        review = self.base / 'review'
        export_review(self.run, review)
        paths = self.reviews(review)
        single = score(self.run, review / 'private_mapping.json', paths[:1])
        self.assertIsNone(single['full_per_repetition']['1']['kb']['accuracy'])
        paths[1].write_text(paths[1].read_text(encoding='utf-8-sig').replace(',B,1,', ',B,0,'), encoding='utf-8-sig')
        disagreement = score(self.run, review / 'private_mapping.json', paths)
        self.assertIsNone(disagreement['full_per_repetition']['1']['kb']['accuracy'])

    def test_duplicate_reviewer_rejected(self):
        self.execute()
        review = self.base / 'review'
        export_review(self.run, review)
        paths = self.reviews(review)
        with self.assertRaisesRegex(ValueError, 'duplicate_reviewer'):
            score(self.run, review / 'private_mapping.json', [paths[0], paths[0]])

    def test_gold_is_review_only_and_frozen(self):
        self.execute()
        gold = self.base / 'gold.jsonl'
        gold.write_text('\n'.join(json.dumps({**q, 'answer_points': ['secret']})
                                 for q in self.questions), encoding='utf-8')
        review = self.base / 'review'
        export_review(self.run, review, gold_path=gold)
        self.assertFalse(any('secret' in canonical(p) for p in self.gateway.inputs))
        write_json(review / '参考答案.json', {'changed': True})
        with self.assertRaisesRegex(ValueError, 'frozen_gold_changed'):
            score(self.run, review / 'private_mapping.json', [review / '评分表.csv'])

    def test_technical_failure_in_primary_denominator(self):
        self.gateway.fail = True
        self.execute()
        review = self.base / 'review'
        export_review(self.run, review)
        data = score(self.run, review / 'private_mapping.json', [review / '评分表.csv'])
        self.assertEqual(data['full_per_repetition']['1']['kb']['accuracy'], 0)
        self.assertEqual(data['full_per_repetition']['1']['kb']['technical_errors'], 2)


if __name__ == '__main__':
    unittest.main()
