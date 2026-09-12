#!/usr/bin/env python3
"""Run fault-injection checks, emitting one required result per unittest."""
from pathlib import Path
import sys
import unittest
from qa_common import record

class EvidenceResult(unittest.TextTestResult):
    def addSuccess(self,test):
        super().addSuccess(test);record(test.id(),'pass')
    def addFailure(self,test,err):
        super().addFailure(test,err);record(test.id(),'fail',detail=str(err[1]))
    def addError(self,test,err):
        super().addError(test,err);record(test.id(),'fail',detail=str(err[1]))
    def addSkip(self,test,reason):
        super().addSkip(test,reason);record(test.id(),'skip',detail=reason)

if __name__=='__main__':
    if not __debug__:raise RuntimeError('assertions disabled')
    suite=unittest.defaultTestLoader.discover(str(Path(__file__).parent/'tests'))
    if not suite.countTestCases():raise RuntimeError('no self tests discovered')
    result=unittest.TextTestRunner(verbosity=2,resultclass=EvidenceResult).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
