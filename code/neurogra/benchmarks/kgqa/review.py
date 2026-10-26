"""Blinded human review and strict scoring, separate from the inference process."""
import argparse
import csv
import json
from pathlib import Path
import random
import statistics

from .core import COMMON_IDS, canonical, digest, read_json, read_case, sha, write_json


FIELDS = ['blind_id', 'reviewer', 'correct', 'key_points_hit', 'key_points_total',
          'evidence_sufficient', 'condition_misuse', 'notes']


def export_review(run, destination, seed=20261017, gold_path=None):
    run, destination = Path(run), Path(destination)
    if destination.exists():
        raise ValueError('review_destination_already_exists')
    manifest = read_json(run / 'manifest.json')
    gold = None
    if gold_path:
        gold_rows = [json.loads(line) for line in Path(gold_path).read_text(encoding='utf-8-sig').splitlines() if line.strip()]
        gold_by_id = {row['id']: row for row in gold_rows}
        if len(gold_by_id) != len(gold_rows):
            raise ValueError('duplicate_gold_id')
        for q in manifest['questions']:
            if q['id'] not in gold_by_id or gold_by_id[q['id']]['question'] != q['question']:
                raise ValueError('gold_question_mismatch')
        gold = {'source_checksum': sha(gold_path),
                'questions': [gold_by_id[q['id']] for q in manifest['questions']]}
    records = [read_case(p) for p in sorted((run / 'cases').glob('*.json'))]
    if any(r['run_binding'] != manifest['binding'] for r in records):
        raise ValueError('review_case_binding_mismatch')
    records = [r for r in records if r['status'] == 'completed']
    random.Random(seed).shuffle(records)
    destination.mkdir(parents=True)
    mapping, packet, evidence_packet = {}, [], []
    with (destination / '评分表.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        for i, r in enumerate(records, 1):
            blind = f'B{i:04d}'
            mapping[blind] = {'case_id': r['case_id'], 'question_id': r['question']['id'],
                              'group': r['group'], 'answer_hash': digest(r['answer'])}
            packet.append({'blind_id': blind, 'question_id': r['question']['id'],
                           'question': r['question']['question'], 'answer': r['answer']})
            evidence_packet.append({'blind_id': blind,
                'evidence': r['answer_input']['payload']['evidence']})
            writer.writerow({'blind_id': blind})
    write_json(destination / 'private_mapping.json', {'run_binding': manifest['binding'],
               'seed': seed, 'cases': mapping, 'gold_hash': digest(gold) if gold else None})
    if gold:
        write_json(destination / '参考答案.json', gold)
    # Answer content might itself reveal an arm; do not claim perfect blinding.
    write_json(destination / '匿名回答.json', packet)
    write_json(destination / '证据核查.json', evidence_packet)
    (destination / '评分说明.txt').write_text(
        '每位评审者复制一份评分表，填写唯一 reviewer。correct 为0或1；必要结论和条件'
        '全部正确且无实质错误才填1。空白为待评，不得视为0。证据充分性需单独核查'
        '证据核查.json 及原文，不能凭回答中的引用判断。无外部证据该字段留空。'
        'condition_misuse 为0或1。要点覆盖可选，填写命中数与总数。'
        'private_mapping.json 不提供给盲评者。回答自身可能透露检索方式。\n', encoding='utf-8')
    return len(packet)


def verdict(rows, minimum_reviews, field='correct'):
    scored = [r for r in rows if r[field] != '']
    if len(scored) < minimum_reviews or len({r[field] for r in scored}) != 1:
        return None
    return int(scored[0][field])


def score(run, mapping_path, review_paths, *, minimum_reviews=2):
    if minimum_reviews < 1:
        raise ValueError('minimum_reviews_must_be_positive')
    run = Path(run)
    manifest = read_json(run / 'manifest.json')
    mapping = read_json(mapping_path)
    if mapping.get('gold_hash'):
        if digest(read_json(Path(mapping_path).parent / '参考答案.json')) != mapping['gold_hash']:
            raise ValueError('frozen_gold_changed')
    if mapping['run_binding'] != manifest['binding']:
        raise ValueError('review_mapping_run_mismatch')
    by_case, reviewers = {}, set()
    for path in review_paths:
        with Path(path).open(encoding='utf-8-sig', newline='') as stream:
            reader = csv.DictReader(stream)
            if reader.fieldnames != FIELDS:
                raise ValueError('invalid_review_columns')
            for row in reader:
                row = {k: v.strip() if isinstance(v, str) else v for k, v in row.items()}
                if None in row or any(v is None for v in row.values()):
                    raise ValueError('malformed_review_row')
                blind = row['blind_id']
                if blind not in mapping['cases']:
                    raise ValueError('unknown_blind_id')
                if all(not row[k].strip() for k in FIELDS if k != 'blind_id'):
                    continue
                if not row['reviewer'].strip():
                    raise ValueError('reviewer_required')
                key = (blind, row['reviewer'])
                if key in reviewers:
                    raise ValueError('duplicate_reviewer_case')
                reviewers.add(key)
                for field in ['correct', 'evidence_sufficient', 'condition_misuse']:
                    if row[field] not in {'', '0', '1'}:
                        raise ValueError('invalid_binary_review:' + field)
                hit, total = row['key_points_hit'], row['key_points_total']
                if bool(hit) != bool(total) or (hit and not (hit.isdigit() and total.isdigit()
                        and 0 <= int(hit) <= int(total) and int(total) > 0)):
                    raise ValueError('invalid_key_point_counts')
                by_case.setdefault(mapping['cases'][blind]['case_id'], []).append(row)
    results = []
    for rep in range(1, manifest['repetitions'] + 1):
        for q in manifest['questions']:
            for group in manifest['groups']:
                case_id = f"{q['id']}_{group}_r{rep}"
                p = run / 'cases' / (case_id + '.json')
                record = read_case(p) if p.exists() else None
                if record and record['run_binding'] != manifest['binding']:
                    raise ValueError('scoring_case_binding_mismatch')
                reviews = by_case.get(case_id, [])
                for item in mapping['cases'].values():
                    if item['case_id'] == case_id and record and record['status'] == 'completed':
                        if item['answer_hash'] != digest(record['answer']):
                            raise ValueError('review_answer_changed')
                technical_error = bool(record and record['status'] == 'error')
                completed = bool(record and record['status'] == 'completed')
                value = 0 if technical_error else verdict(reviews, minimum_reviews) if completed else None
                evidence = verdict(reviews, minimum_reviews, 'evidence_sufficient') if completed and group != 'llm' else None
                misuse = verdict(reviews, minimum_reviews, 'condition_misuse') if completed else None
                key_scores = [(int(r['key_points_hit']), int(r['key_points_total']))
                              for r in reviews if r['key_points_total']]
                coverage = (key_scores[0][0] / key_scores[0][1]
                            if len(key_scores) >= minimum_reviews and len(set(key_scores)) == 1 else None)
                results.append({'case_id': case_id, 'question_id': q['id'], 'group': group,
                    'repetition': rep, 'completed': completed, 'technical_error': technical_error,
                    'correct': value, 'reviews': reviews, 'evidence_sufficient': evidence,
                    'condition_misuse': misuse, 'key_point_coverage': coverage,
                    'seconds': sum(a['seconds'] for a in record.get('attempts', [])) if record else None,
                    'kg_used': record.get('retrieval', {}).get('kg_used', 0) if record else 0})
    def summarize(items):
        total = len(items)
        pending = sum(r['correct'] is None for r in items)
        correct = sum(r['correct'] == 1 for r in items)
        times = [r['seconds'] for r in items if r['seconds'] is not None]
        auxiliary = {}
        for field in ['evidence_sufficient', 'condition_misuse', 'key_point_coverage']:
            eligible = [r for r in items if r['completed'] and
                        (field != 'evidence_sufficient' or r['group'] != 'llm')]
            values = [r[field] for r in eligible if r[field] is not None]
            auxiliary[field] = {'eligible': len(eligible), 'reviewed': len(values),
                'mean': statistics.mean(values) if values and len(values) == len(eligible) else None}
        return {'expected': total, 'completed': sum(r['completed'] for r in items),
                'technical_errors': sum(r['technical_error'] for r in items),
                'pending_or_disputed': pending, 'correct': correct,
                'accuracy': correct / total if total and not pending else None,
                'success_rate': sum(r['completed'] for r in items) / total if total else None,
                'mean_seconds': statistics.mean(times) if times else None,
                'kg_used_cases': sum(r['kg_used'] > 0 for r in items), 'auxiliary': auxiliary}
    full, common = {}, {}
    for rep in range(1, manifest['repetitions'] + 1):
        full[str(rep)], common[str(rep)] = {}, {}
        for group in manifest['groups']:
            subset = [r for r in results if r['group'] == group and r['repetition'] == rep]
            full[str(rep)][group] = summarize(subset)
            common[str(rep)][group] = summarize([r for r in subset if r['question_id'] in COMMON_IDS])
    return {'run_binding': manifest['binding'], 'minimum_reviews': minimum_reviews,
            'review_checksums': {str(p): sha(p) for p in review_paths},
            'full_per_repetition': full, 'common_Q08_Q11_per_repetition': common,
            'cases': results, 'limitations': manifest['audit']['limitations']}


def main(argv=None):
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    export = sub.add_parser('export')
    export.add_argument('--run', required=True)
    export.add_argument('--output', required=True)
    export.add_argument('--gold', help='optional answer key; only the review process reads it')
    scoring = sub.add_parser('score')
    scoring.add_argument('--run', required=True)
    scoring.add_argument('--mapping', required=True)
    scoring.add_argument('--reviews', nargs='+', required=True)
    scoring.add_argument('--minimum-reviews', type=int, default=2)
    scoring.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    if args.command == 'export':
        print('exported', export_review(args.run, args.output, gold_path=args.gold))
    else:
        if args.minimum_reviews < 1:
            parser.error('minimum reviews must be positive')
        data = score(args.run, args.mapping, args.reviews, minimum_reviews=args.minimum_reviews)
        write_json(args.output, data)
        print(canonical(data['full_per_repetition']))


if __name__ == '__main__':
    main()
