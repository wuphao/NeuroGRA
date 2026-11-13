"""Run existing engineering suites and retain per-test evidence for completion review."""
import argparse
import json
import time
import unittest
from pathlib import Path


class EvidenceResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records = []
        self.started = {}

    def startTest(self, test):
        self.started[test.id()] = time.monotonic()
        super().startTest(test)

    def record(self, test, status, detail=None):
        self.records.append({'test': test.id(), 'status': status,
                             'seconds': round(time.monotonic() - self.started.get(test.id(), time.monotonic()), 4),
                             **({'detail': detail} if detail else {})})

    def addSuccess(self, test):
        super().addSuccess(test)
        self.record(test, 'passed')

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.record(test, 'failed', self._exc_info_to_string(err, test))

    def addError(self, test, err):
        super().addError(test, err)
        self.record(test, 'error', self._exc_info_to_string(err, test))

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.record(test, 'skipped', reason)

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err is not None:
            self.record(subtest, 'failed', self._exc_info_to_string(err, test))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    suite = unittest.TestSuite()
    for package in ('clinical', 'platform'):
        suite.addTests(unittest.TestLoader().discover(str(root / 'code/neurogra' / package / 'tests'),
                                                      top_level_dir=str(root / 'code')))
    result = unittest.TextTestRunner(verbosity=1, resultclass=EvidenceResult).run(suite)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({'tests_run': result.testsRun, 'successful': result.wasSuccessful(),
        'scope': 'Existing synthetic engineering suites; not clinical effectiveness or production acceptance',
        'records': result.records}, ensure_ascii=False, indent=2), encoding='utf-8')
    return not result.wasSuccessful()


if __name__ == '__main__':
    raise SystemExit(main())
