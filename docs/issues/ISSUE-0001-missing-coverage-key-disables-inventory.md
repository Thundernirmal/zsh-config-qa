# ISSUE-0001: Missing or empty coverage.json key silently disables case-inventory enforcement

- **Status:** Open
- **Severity:** High
- **Category:** fail-open
- **Affected:** `release.py:274`, `release.py:300`, `release.py:203`, `release.py:70-81`
- **Confidence:** Confirmed by execution
- **Filed:** 2026-09-12 against harness `60f633a` plus the pending audit working tree

## Summary

`main()` loads `coverage.json` and passes `coverage.get(name)` into `run_stage`
(`release.py:300`). When a stage key is absent — or present but `null` — `coverage.get()`
returns `None`, and `run_stage` skips `read_results()` entirely (`if expected is not None`,
`release.py:203`). The stage is then marked `pass` from its exit status alone, with
`cases=[]`, even though nothing was read from the results ledger. A full run can therefore
reach `YES` while a required stage's case inventory is not enforced at all.

A second, smaller weakness: `read_results()` compares `set(names) != set(expected)`
(`release.py:76`), so duplicate required names in `coverage.json` collapse into one and are
accepted silently. Only duplicates in the recorded ledger are rejected.

A third variant is an explicitly empty list (`"safe": []`): `read_results(results, [])`
accepts a stage that exits 0 with zero recorded rows, so a one-character edit shrinks
required coverage just as effectively as deleting the key.

## Impact

- A required case that is skipped, missing, renamed, or duplicated can no longer turn the
  stage into `incomplete`/`fail` when the coverage key is gone. Both `run-safe.zsh` and
  `qa-pty.py` exit `0` when cases are skipped (`run-safe.zsh:191-196`, `qa-pty.py:1024`),
  and `run_stage` only downgrades skips to `incomplete` when it reads the ledger
  (`release.py:208-209`). With `expected=None` a skipped required scenario is a `pass`,
  which is a false-`YES` path.
- `coverage.json` is ordinary JSON with no schema check; a typo (`"safes"`), a deleted key,
  `"safe": null`, or `"safe": []` disables or shrinks enforcement with no diagnostic.
- Duplicate expected names are silently deduplicated, so the inventory does not need to be
  well-formed to be enforced.

## Root cause

There is no validation that every selected stage name exists in `coverage.json` and maps
to a non-empty list of strings. `coverage.get(name)` conflates "no inventory required"
with "key missing", and the `expected is not None` guard is the only switch between
"verify the ledger" and "trust the exit code".

## Reproduction

```sh
mkdir -p /tmp/opencode/audit-r1/issue-0001 && cd /tmp/opencode/audit-r1/issue-0001
python3 - <<'PY'
import json, os, sys
from pathlib import Path
HARNESS = '/home/nirmal/projects/zsh-config-qa'
sys.path.insert(0, HARNESS)
import release

WORK = Path('/tmp/opencode/audit-r1/issue-0001/work')
WORK.mkdir(parents=True, exist_ok=True)
ENV = dict(os.environ)

# 1) The real coverage loader: a missing key yields None.
coverage = json.loads((Path(HARNESS) / 'coverage.json').read_text())
print('coverage keys        :', sorted(coverage))
print('coverage.get("safe") :', type(coverage.get('safe')).__name__)
print('coverage.get("typo") :', repr(coverage.get('typo')))

# 2) expected=None: the same no-op command as below passes with zero evidence.
item = release.run_stage('no-expected', ['sh', '-c', 'true'], WORK, ENV, 5, expected=None)
print("expected=None -> status=%r detail=%r cases=%r" % (item['status'], item['detail'], item['cases']))
print('  results file exists:', (WORK / 'no-expected.jsonl').exists())

# 3) A required case makes the identical no-op command fail.
item = release.run_stage('with-expected', ['sh', '-c', 'true'], WORK, ENV, 5, expected=['x'])
print("expected=['x'] -> status=%r detail=%r" % (item['status'], item['detail']))

# 4) Expected lists are converted to sets, so duplicate required names collapse.
p = WORK / 'dup-expected.jsonl'
p.write_text(json.dumps({'name': 'x', 'status': 'pass'}) + '\n')
print('read_results(path, ["x","x"]) accepted rows:', release.read_results(p, ['x', 'x']))
PY
```

### Observed

```text
coverage keys        : ['env', 'fzf', 'pty', 'safe', 'selftest']
coverage.get("safe") : list
coverage.get("typo") : None
RUN  no-expected (log: /tmp/opencode/audit-r1/issue-0001/work/no-expected.log)
PASS       no-expected: exit 0
expected=None -> status='pass' detail='exit 0' cases=[]
  results file exists: False
RUN  with-expected (log: /tmp/opencode/audit-r1/issue-0001/work/with-expected.log)
FAIL       with-expected: missing or malformed case evidence: [Errno 2] No such file or directory: '/tmp/opencode/audit-r1/issue-0001/work/with-expected.jsonl'
expected=['x'] -> status='fail' detail="missing or malformed case evidence: [Errno 2] No such file or directory: '/tmp/opencode/audit-r1/issue-0001/work/with-expected.jsonl'"
read_results(path, ["x","x"]) accepted rows: [{'name': 'x', 'status': 'pass'}]
```

The first stage ran `true` and passed with `cases=[]` and no results file; the identical
command failed once an expected name was supplied.

## Expected behavior

- A selected stage with no coverage entry must fail setup (or the run must be refused)
  rather than silently dropping inventory enforcement.
- The loader should validate that every key maps to a non-empty list of unique strings,
  and `run_stage` should distinguish `None` (no inventory requested) from a real expected
  list (e.g. a missing-key sentinel that is an error for stage execution).
- Duplicate names in the expected list should be rejected rather than collapsed by `set()`.

## Proposed fix

1. After loading `coverage.json` (`release.py:274`), validate structure: a dict whose
   values are non-empty lists of unique strings; abort with a clear error if a selected
   stage name is missing.
2. Change `coverage.get(name)` (`release.py:300`) to `coverage[name]`, or pass a sentinel
   and have `run_stage` raise when the name is not in the inventory.
3. In `read_results()` (`release.py:70-81`), compare `list` order-insensitively but check
   `len(expected) == len(set(expected))` and raise on duplicates in `expected`.
4. Add a self-test: temporarily remove a stage key from an in-memory coverage mapping and
   assert the run refuses to start or the stage fails.

## Test plan

- Fault-injection test in `tests/test_gate.py`: coverage mapping without the stage key
  must not produce a `pass` stage; `coverage.json` missing a selected stage must abort.
- Unit test `read_results(p, ['x','x'])` raises `ValueError`.
- Re-run the full gate (`./run-all.zsh`) after the fix and confirm `coverage.json` keys are
  checked against `ALL_STAGES`.
