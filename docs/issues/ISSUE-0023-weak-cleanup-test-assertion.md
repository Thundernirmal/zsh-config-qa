# ISSUE-0023: The credential-backend cleanup test asserts only a non-empty error list

- **Status:** Open
- **Severity:** Low
- **Category:** evidence-integrity
- **Affected:** `tests/test_gate.py:167-170`
- **Confidence:** Confirmed by execution
- **Filed:** 2026-09-12 against harness `60f633a` plus the pending audit working tree

## Summary

The fault-injection test for credential backend failure is:

```python
def test_cleanup_backend_failure_is_not_success(self):
    (self.work/'credentials.jsonl').write_text('{"name":"QA_SELFTEST_ABC"}\n')
    with patch.object(release,'bounded',return_value=subprocess.CompletedProcess([],1,'','service down')):
        self.assertTrue(release.cleanup(self.work))
```

(`tests/test_gate.py:167-170`). The assertion only checks that `cleanup()` returned a
non-empty list. `cleanup()` can return a list for reasons unrelated to the credential
backend — a malformed `processes.jsonl` entry, a stray descendant process, or any future
error path. Such a run would satisfy `assertTrue(...)` while the credential failure it is
supposed to catch went unnoticed (or, symmetrically, the test would pass because the
credential code never ran).

The test also does not assert *which* error was produced, so a regression that changed the
message, stopped calling `secret-tool lookup`, or skipped the credential block entirely
can still pass as long as some other error exists.

## Impact

- The self-test stage can report `pass` for a harness whose credential cleanup is broken,
  weakening the fault-injection coverage the gate relies on (AGENTS.md: "Add a
  fault-injection self-test when changing verdicts, ... cleanup").
- Because credential cleanup is the real-service boundary, a false pass here removes the
  only automated proof that backend failure cannot be mistaken for success.

## Root cause

The assertion targets the shape of the return value (`truthy`) instead of the observable
error content and the backend interaction. No `mock` call assertions constrain
`release.bounded`.

## Reproduction

Sandboxed equivalent of the test body, plus an unrelated registry error:

```sh
mkdir -p /tmp/opencode/audit-r1/issue-0023 && cd /tmp/opencode/audit-r1/issue-0023
python3 - <<'PY'
import subprocess, sys
from pathlib import Path
from unittest.mock import patch
HARNESS = '/home/nirmal/projects/zsh-config-qa'
sys.path.insert(0, HARNESS)
import qa_common as common
import release

BASE = Path('/tmp/opencode/audit-r1/issue-0023')
WORK = BASE / 'run'
WORK.mkdir(parents=True, exist_ok=True)
common.atomic_json(WORK / common.MARKER, dict(id='selftest', work=str(WORK), repo=str(BASE / 'repo')))
(WORK / 'credentials.jsonl').write_text('{"name":"QA_SELFTEST_ABC"}\n')

def run_cleanup(result):
    with patch.object(release, 'bounded', return_value=result):
        return release.cleanup(WORK)

# Healthy backend (lookup exit 1, empty stderr) + unrelated process-registry error.
(WORK / 'processes.jsonl').write_text('{"pid": 12345}\n')
healthy = subprocess.CompletedProcess([], 1, '', '')
errors = run_cleanup(healthy)
print('healthy backend + registry error ->', errors)
print('existing assertion assertTrue(errors) passes:', bool(errors))
(WORK / 'processes.jsonl').unlink()

# Honest backend failure: the only error is the credential message.
failing = subprocess.CompletedProcess([], 1, '', 'service down')
errors = run_cleanup(failing)
print('failing backend only            ->', errors)
PY
```

### Observed

```text
healthy backend + registry error -> ["process cleanup: 'start'"]
existing assertion assertTrue(errors) passes: True
failing backend only            -> ['credential cleanup could not verify absence: QA_SELFTEST_ABC']
```

The first case proves the assertion is satisfied by a `process cleanup` error while the
credential backend was healthy; the second shows what the test is actually meant to
observe.

## Expected behavior

The test fails unless the cleanup error list contains the credential-specific message for
the registered synthetic name (and ideally proves the lookup/clear calls were made). A
generic truthy result must not satisfy it.

## Proposed fix

Strengthen the assertion and constrain the backend interaction, e.g.:

```python
with patch.object(release, 'bounded',
                  return_value=subprocess.CompletedProcess([], 1, '', 'service down')) as backend:
    errors = release.cleanup(self.work)
self.assertIn('credential cleanup could not verify absence: QA_SELFTEST_ABC', errors)
self.assertNotIn('process cleanup', '; '.join(errors))
lookup = [call.args[0] for call in backend.call_args_list if 'lookup' in call.args[0]]
self.assertEqual(len(lookup), 1)
```

Also assert the exact error list on a clean work directory, so unrelated error paths cannot
stand in.

## Test plan

- Positive control: healthy backend plus an unrelated process-registry error must fail the
  strengthened test.
- Negative control: the patched backend failure must fail it as well.
- Keep `test_cleanup_refuses_unrelated_credential` and the PID-reuse test unchanged; add
  the strengthened test identity to `coverage.json` under `selftest` in the same change.
