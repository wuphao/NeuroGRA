"""Six real-model engineering replays; not a blinded accuracy experiment."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CASES = ['002_S_0685', '002_S_0413', '041_S_4041', '041_S_4427', '003_S_4373', '003_S_4152']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT / 'output/rwe_batch_screenshots/reports')
    parser.add_argument('--output', type=Path, default=ROOT / 'output/diagnosis_repair_pilot_v2')
    parser.add_argument('--cases', nargs='+', default=DEFAULT_CASES)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    results = []
    for number in args.cases:
        directory = args.output / number
        print('真实模型工程重放：' + number, flush=True)
        if not (directory / 'report.json').is_file():
            try:
                with (args.output / (number + '.console.log')).open('a', encoding='utf-8') as log:
                    subprocess.run([sys.executable, '-u', str(ROOT / 'code/tools/diagnose_saved_report.py'),
                        '--source', str(args.source / number), '--output', str(directory)], cwd=ROOT,
                        stdout=log, stderr=subprocess.STDOUT, check=True, timeout=1020)
            except Exception as exc:
                results.append({'patient': number, 'error': str(exc)})
        if (directory / 'report.json').is_file():
            report = json.loads((directory / 'report.json').read_text(encoding='utf-8'))
            outcome = report.get('diagnostic_outcome')
            results.append({'patient': number, 'diagnostic_outcome': outcome})
        (args.output / 'summary.json').write_text(json.dumps({'scope': '真实模型、非盲法、仅诊断阶段工程重放',
            'patients': results}, ensure_ascii=False, indent=2), encoding='utf-8')
    print('结果：' + str(args.output / 'summary.json'), flush=True)


if __name__ == '__main__':
    main()
