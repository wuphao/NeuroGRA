"""Reproducible routing probes; no patient systems or model calls are used."""
import argparse
import json
from pathlib import Path

from neurogra.platform.patient_chat import PatientChat
from neurogra.platform.multi_patient import is_cohort_question, is_cross_question


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    source = Path(__file__).parent / 'acceptance/completion_questions.json'
    dataset = json.loads(source.read_text(encoding='utf-8'))
    results = []
    for case in dataset['cases']:
        question = case['question']
        actual = {'report': PatientChat.wants_full_report(question),
                  'cross': is_cross_question(question, case['comparison_patients']),
                  'cohort': is_cohort_question(question)}
        results.append({'id': case['id'], 'split': case['split'], 'question': question,
                        'expected': case['expected_signals'], 'actual': actual,
                        'passed': actual == case['expected_signals']})
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({'scope': dataset['scope'], 'total': len(results),
        'passed': sum(row['passed'] for row in results), 'results': results},
        ensure_ascii=False, indent=2), encoding='utf-8')
    failures = [row for row in results if not row['passed']]
    print(json.dumps({'total': len(results), 'failures': failures}, ensure_ascii=False))
    return bool(failures)


if __name__ == '__main__':
    raise SystemExit(main())
