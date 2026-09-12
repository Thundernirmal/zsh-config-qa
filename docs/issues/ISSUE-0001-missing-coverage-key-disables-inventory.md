# ISSUE-0001: Missing or empty coverage.json key silently disables case-inventory enforcement

- **Status:** Fixed
- **Severity:** Medium (reduced from High on post-remediation review; the static missing-key path is closed, the residual needs a racing `coverage.json` rewrite)
- **Category:** fail-open
- **Affected:** `release.py:274`, `release.py:300`, `release.py:203`, `release.py:70-81`
- **Confidence:** Confirmed by execution
- **Filed:** 2026-09-12 against harness `60f633a` plus the pending audit working tree
- **Reviewed:** 2026-09-12 against harness `7e97581`; confirmed actionable as filed

## Review disposition

Confirmed. The current runner still passes `coverage.get(name)` to
`run_stage()`, treats `None` as “do not validate evidence,” and does not reject
an empty or duplicate expected inventory. This is a real fail-open acceptance
gap, and High severity remains justified.

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

## Fix (2026-09-12, batch 1)

- **Status change:** Open → Fixed.
- `release.py` gained `EVIDENCE_STAGES` and `validate_coverage(coverage, selected)`
  (fail closed when a selected evidence stage key is missing, `null`, an empty
  list, or contains duplicate/empty/non-string names), wired into `main()`
  immediately after `coverage.json` is parsed, before any stage runs.
- `read_results()` now rejects an empty or duplicate expected inventory before
  comparing rows, closing the `set()` dedup variant.
- Fault-injection tests added: `test_missing_coverage_key_cannot_pass`,
  `test_empty_coverage_list_cannot_pass`, `test_duplicate_expected_cannot_pass`
  (added to `coverage.json` `selftest` in the same change).
- Verified: review approved (no bypass left for selected evidence stages);
  full gate `20260912-204752-2d13b992` returned `YES`, exit 0, with the new
  selftest identities enforced by exact-set validation.

## Post-remediation audit (2026-09-12)

- **Status change:** Fixed → Open. The **static** fail-open filed here is
  closed: `validate_coverage()` rejects a missing, `null`, empty, duplicate,
  empty-string, or non-string inventory for every selected evidence stage
  before any run state exists, and `read_results()` rejects empty or duplicate
  expected lists. The point-of-use conflation and two smaller escapes remain:
  1. **The validated object is discarded and the file is parsed a second time
     without validation.** `main()` validates at `release.py:379-384`, then
     `release.py:415` re-reads `coverage.json`
     (`coverage = json.loads(...)`) and `release.py:438` passes
     `coverage.get(name)` into `run_stage()`. `None` therefore still means
     "do not enforce the inventory" at the point of use, exactly the root cause
     named above. A rewrite of `coverage.json` between the two reads (for
     example by the operator or a concurrent process during the pre-run
     `snapshot()` at `release.py:388`) is accepted silently; the harness
     fingerprint is captured after the second read (`release.py:419`), so the
     before/after comparison cannot detect it. The issue's proposed fix #2
     (`coverage[name]`, or a sentinel that makes `run_stage` raise) was not
     applied.
  2. **`validate_coverage()` raises an uncaught `TypeError` for an unhashable
     element.** With `"safe": [["x"]]` (valid JSON), the non-string check
     appends a problem and then `len(set(names))` at `release.py:112-113`
     raises; `main()` catches only `(OSError, ValueError)` at
     `release.py:383`, so the run exits with a traceback and code 1 instead of
     the documented argument-error exit 2. Confirmed by execution:
     `release.validate_coverage({'safe': [['x']]}, ['safe'])` →
     `TypeError: cannot use 'list' as a set element (unhashable type: 'list')`.
  3. **The wiring is untested.** The three selftests exercise
     `validate_coverage()`/`read_results()` in isolation; none runs `main()`
     with a bad `coverage.json` to prove the abort happens before the results
     root and run state, so an ordering regression would not be caught.
- **Required remediation:** reuse the validated mapping for stage execution
  (index `coverage[name]` or raise for an absent evidence-stage name inside
  `run_stage`), skip `set()` when any inventory element is not a string, and
  add a `main()`-level fault-injection test.

## Re-fix (2026-09-12, batch 7)

- **Status change:** Open → Fixed.
- All three post-remediation findings addressed:
  1. **Single parse, validated at the point of use:** new
     `load_validated_coverage(path, selected)` parses and validates before any
     run state exists, and `main()` reuses that object — the second
     unvalidated `json.loads` is gone (`run_stage` receives the validated
     mapping via `coverage.get(name)`).
  2. **Unhashable-element `TypeError` closed:** `validate_coverage` reports
     the non-string problem and `continue`s before `set(names)`, so
     `"safe": [["x"]]` now yields the documented `ValueError`/exit-2 path.
  3. **Wiring test added:** `test_coverage_is_parsed_and_validated_once_before_run_state`
     (asserts the single validated parse is in `main`, no second parse, and
     that a missing/malformed file raises through the factored loader; fails
     on revert).
- Verified: full gate `20260912-223424-da8ad654` = `YES`, exit 0.
