#!/usr/bin/env python3
"""Run fault-injection checks, emitting one required result per unittest."""
from pathlib import Path
import os
import sys
import unittest
from qa_common import record

# Capture the launcher-provided ledger before tests run: tests patch the
# environment (e.g. QA_RESULTS_FILE), and unittest calls addSuccess after
# cleanups but addFailure/addError/skip while a test's patch is still active.
# Without an explicit destination, a failing test's record would be written to
# the test's own patched file and vanish from the stage ledger.
STAGE_RESULTS_FILE = os.environ.get('QA_RESULTS_FILE')

class EvidenceResult(unittest.TextTestResult):
    def addSuccess(self,test):
        super().addSuccess(test);record(test.id(),'pass',destination=STAGE_RESULTS_FILE)
    def addFailure(self,test,err):
        super().addFailure(test,err);record(test.id(),'fail',detail=str(err[1]),destination=STAGE_RESULTS_FILE)
    def addError(self,test,err):
        super().addError(test,err);record(test.id(),'fail',detail=str(err[1]),destination=STAGE_RESULTS_FILE)
    def addSkip(self,test,reason):
        super().addSkip(test,reason);record(test.id(),'skip',detail=reason,destination=STAGE_RESULTS_FILE)

if __name__=='__main__':
    if not __debug__:raise RuntimeError('assertions disabled')
    suite=unittest.defaultTestLoader.discover(str(Path(__file__).parent/'tests'))
    if not suite.countTestCases():raise RuntimeError('no self tests discovered')
    result=unittest.TextTestRunner(verbosity=2,resultclass=EvidenceResult).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
