"""Runs every test and prints a human-checkable report: each test case shows its input, the
expected result, the actual result and "test case N pass", followed by a summary.

    TEST_DATABASE_URL=postgresql+psycopg://user:password@host:5432/db python tests/run_report.py

The database must be disposable: every test empties its tables.
"""

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import support  # noqa: E402  (skips everything when TEST_DATABASE_URL is not set)


def main() -> int:
    suite = unittest.defaultTestLoader.discover(str(HERE))
    result = unittest.TextTestRunner(verbosity=0, stream=sys.stderr).run(suite)

    passed = [r for r in support.RESULTS if r[2]]
    failed = [r for r in support.RESULTS if not r[2]]
    print('\n' + '=' * 72)
    print(f'SUMMARY: {len(support.RESULTS)} test cases, {len(passed)} pass, {len(failed)} FAIL '
          f'({result.testsRun} test functions)')
    for number, title, _ in failed:
        print(f'  FAIL  test case {number}: {title}')
    for test, _ in result.errors + result.failures:
        print(f'  PROBLEM in {test.id()}')
    print('=' * 72)
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(main())
