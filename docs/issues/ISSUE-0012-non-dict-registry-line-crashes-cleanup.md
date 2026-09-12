# ISSUE-0012: A valid-JSON non-dict line in processes.jsonl raises an uncaught TypeError in cleanup()

- **Status:** Fixed
- **Severity:** Medium
- **Category:** cleanup
- **Affected:** `release.py:121-131`, `release.py:126`
- **Confidence:** Confirmed by execution
- **Filed:** 2026-09-12 against harness `60f633a` plus the pending audit working tree
- **Reviewed:** 2026-09-12 against harness `7e97581`; confirmed actionable as filed

## Review disposition

Confirmed. Shape validation is absent and `TypeError` still escapes the
per-entry cleanup handler, aborting later process and credential cleanup. The
input is abnormal, but cleanup ledgers are a fail-closed boundary; Medium is
defensible because the failure interrupts actual resource recovery.

## Summary

`cleanup()` parses each line of `processes.jsonl` and immediately indexes it as a dict:
`entry['start']` (`release.py:126`). A syntactically valid JSON line that is not an object
— for example `[]` — raises `TypeError: list indices must be integers or slices, not str`.
The handler at `release.py:130` catches only `(ValueError, KeyError, PermissionError)`, so
the exception escapes `cleanup()`.

## Impact

- `cleanup()` aborts mid-pass. Any later registry entries and — importantly — the entire
  credential-cleanup block (`release.py:132-146`) are skipped, so synthetic credentials
  registered for the run may remain in the Secret Service.
- In `main()`, cleanup runs in the `finally` block (`release.py:318`) and on the
  failure path (`release.py:307`). An uncaught exception replaces the intended report
  finalization: `report.json` keeps its last on-disk state, `report.md`/`latest.json` are
  not written, and the process exits with a traceback instead of the documented verdict
  path.
- The failure is fail-closed for approval (the report cannot become `YES`), but it is a
  crash instead of an actionable cleanup error.
- A crash in `cleanup()` during `--cleanup` recovery leaves survivors and no summary.

## Root cause

The registry reader validates JSON syntax but not shape. The `except` tuple covers
missing keys (`KeyError`) but not `TypeError` from indexing a list/string/number, so the
error path itself is fragile. The same applies to a JSON line such as `"abc"` or `42`.

## Reproduction

```sh
mkdir -p /tmp/opencode/audit-r1/issue-0012 && cd /tmp/opencode/audit-r1/issue-0012
python3 - <<'PY'
import sys, traceback
from pathlib import Path
HARNESS = '/home/nirmal/projects/zsh-config-qa'
sys.path.insert(0, HARNESS)
import qa_common as common
import release

BASE = Path('/tmp/opencode/audit-r1/issue-0012')
WORK = BASE / 'run'
WORK.mkdir(parents=True, exist_ok=True)
common.atomic_json(WORK / common.MARKER, dict(id='0012', work=str(WORK), repo=str(BASE / 'repo')))
(WORK / 'processes.jsonl').write_text('[]\n')
print('processes.jsonl contents:', repr((WORK / 'processes.jsonl').read_text()))
try:
    result = release.cleanup(WORK)
    print('cleanup returned:', result)
except Exception:
    traceback.print_exc()
PY
```

### Observed

```text
Traceback (most recent call last):
  File "<stdin>", line 15, in <module>
  File "/home/nirmal/projects/zsh-config-qa/release.py", line 126, in cleanup
    if entry['start'] and process_identity(entry['pid']) == entry['start']:
       ~~~~~^^^^^^^^^
TypeError: list indices must be integers or slices, not str
processes.jsonl contents: '[]\n'
```

(The `contents:` line is buffered stdout and appears after the traceback when both streams
share a terminal; the traceback is emitted on stderr. The exception escapes `cleanup()` —
the `except` at `release.py:130` catches `ValueError`, `KeyError`, and `PermissionError`,
not `TypeError`.)

## Expected behavior

A malformed registry line must be reported as a cleanup error ("process cleanup: ..."),
not crash the cleanup pass. The rest of the registry and the credential cleanup must
still run, and `--cleanup` must print the error list and return nonzero.

## Proposed fix

Validate shape before indexing, and widen the handler, e.g.:

```python
if not isinstance(entry, dict):
    errors.append(f'process cleanup: invalid registry entry {line!r}')
    continue
pid, start = entry.get('pid'), entry.get('start')
if start and pid and process_identity(pid) == start:
    os.killpg(pid, signal.SIGKILL)
```

Add `TypeError` to the caught tuple as a belt-and-braces measure, and make the credential
block independent so one bad registry line cannot skip credential cleanup.

## Test plan

- Fault-injection test: write `[]` (and a bare string/number) to `processes.jsonl`; assert
  `cleanup()` returns a non-empty error list, does not raise, and still processes a
  subsequent valid entry and the credentials file.
- Add the test identity to `coverage.json` under `selftest`.

## Fix (2026-09-12, batch 3)

- **Status change:** Open → Fixed.
- `cleanup()` validates each registry line is a JSON object with an `int` (not
  bool) `pid` and a string `start`, raising `ValueError` per bad line; `TypeError`
  was added to the caught tuple, and the credential block can no longer be
  skipped by one bad registry line (each line is handled independently).
- Fault-injection test: `test_cleanup_tolerates_malformed_registry_entries`
  (`[]`, `{"pid": 1}`, `"junk"`, `42` each produce one reported error; cleanup
  returns instead of raising; fails on revert via uncaught `TypeError`).
- Verified: full gate `20260912-210646-ff36a6ad` = `YES`, exit 0.
