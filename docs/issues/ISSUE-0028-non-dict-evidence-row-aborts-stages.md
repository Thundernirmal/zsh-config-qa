# ISSUE-0028: A non-dict JSON evidence row raises `AttributeError` and aborts every remaining stage

- **Status:** Fixed
- **Severity:** Low
- **Category:** evidence-integrity
- **Affected:** `release.py:70-81` (`r.get('name')` / `r.get('status')` assume dict rows), `release.py:203-211` (`run_stage` catches only `ValueError`), `release.py:310-314` (blanket handler stops the stage loop)
- **Confidence:** Confirmed by execution
- **Filed:** 2026-09-12 against harness `60f633a` plus the pending audit working tree
- **Reviewed:** 2026-09-12 against harness `7e97581`; confirmed actionable as filed

## Review disposition

Confirmed. The reader still assumes every decoded row is a mapping and the
stage wrapper only handles `ValueError`. The verdict remains fail-closed, but
explicit shape validation is needed to retain stage-local attribution and
continue independent diagnostic stages. Low is appropriate.

## Summary

`read_results()` parses each JSONL line and immediately calls `r.get(...)`.
Truncated or invalid JSON is handled (`json.JSONDecodeError` is a `ValueError`),
but a syntactically valid non-dict row — `123`, `"x"`, `[1,2]` — raises
`AttributeError: 'int' object has no attribute 'get'`. `run_stage` catches only
`ValueError` around `read_results` (`release.py:210`), so the `AttributeError`
escapes, `main`'s blanket handler records a single `runner` failure
(`release.py:313-314`), and every remaining selected stage is abandoned without
evidence.

This is the evidence-ledger sibling of ISSUE-0012 (non-dict `processes.jsonl`
row) and shares the same fix pattern.

## Impact

- A corrupted or foreign-ledger line degrades failure attribution from "stage
  failed: malformed evidence" to "runner failed", and drops the evidence of
  stages that would have run afterward (for example `env`, `fzf`, and both PTY
  passes).
- Properly fail-closed for approval (the run cannot be `YES`), so severity is
  Low; the cost is lost diagnostic coverage and a misleading runner-level error.

## Root cause

Shape is validated by convention (the harness's own `record()` always writes a
dict) but not by `read_results()`, whose job is to reject malformed evidence.
The `except` clause in `run_stage` mirrors only the parse-error class rather
than all validation failures.

## Reproduction

```sh
python3 - <<'PY'
import sys, tempfile, os, pathlib
sys.path.insert(0, '/home/nirmal/projects/zsh-config-qa')
import release
work = pathlib.Path(tempfile.mkdtemp(prefix='qa-evidence-', dir='/tmp/opencode'))
env = dict(os.environ, QA_WORK_DIR=str(work))
try:
    release.run_stage('tampered', ['sh','-c','printf "123\\n" > "$QA_RESULTS_FILE"'],
                      work, env, 5, ['one'])
except Exception as error:
    print('PROPAGATED:', type(error).__name__, error)
PY
```

### Observed

```text
RUN  tampered (log: /tmp/opencode/qa-evidence-.../tampered.log)
PROPAGATED: AttributeError 'int' object has no attribute 'get'
```

`read_results` alone raises `AttributeError` for `123`, `"x"`, and `[1,2]`
instead of the documented `ValueError` for malformed evidence.

## Expected behavior

`read_results()` rejects any row that is not a JSON object with the documented
`ValueError` message; `run_stage` marks that stage `fail` with the malformed-
evidence detail and continues to the next selected stage. A single bad line
never becomes a runner-level abort.

## Proposed fix

```python
rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
if any(not isinstance(row, dict) for row in rows):
    raise ValueError('malformed case evidence: row is not an object')
```

and widen the `run_stage` handler to `except (ValueError, AttributeError)`, or
restrict it to `ValueError` once shape validation is in place.

## Test plan

- Unit test: `read_results` with `123`, `"x"`, and `[1,2]` raises `ValueError`.
- Stage test: a stage that exits 0 while writing a non-dict row is `fail`, and a
  following selected stage still runs.
- Add the named self-test identities to `coverage.json` in the same change.
- Re-run the full gate after the fix.

## Fix (2026-09-12, batch 1)

- **Status change:** Open → Fixed.
- `read_results()` raises `ValueError` when any decoded row is not a JSON
  object, and additionally when `name`/`status` are not strings (this closes
  the adjacent `TypeError` escape found in review, e.g. a row value of
  `["a"]`, which previously aborted `run_stage` the same way). `run_stage`
  already catches `ValueError`, so a malformed row now fails that stage
  locally and the remaining stages still run.
- Fault-injection test added: `test_non_dict_evidence_row_is_malformed`
  (covers `123`, `"x"`, `[1,2]`, non-string `name`/`status`, empty and
  duplicate expected lists), registered in `coverage.json` in the same change.
- Related fix discovered while testing this batch: `selftest.py`'s
  `EvidenceResult` recorded `addSuccess` rows after test cleanups (into the
  launcher ledger) but `addFailure`/`addError`/`addSkip` while the test's
  environment patch was still active, so a failing self-test's row was written
  to the test's own temp file and vanished from the stage ledger. Fixed by
  capturing `STAGE_RESULTS_FILE` at import and passing an explicit
  `destination` to `qa_common.record()` (new optional parameter, default
  behavior unchanged); fault-injection test
  `test_failed_selftest_outcome_lands_in_stage_ledger` proves the row lands in
  the launcher ledger and nowhere else. This defect had been silently masked
  because every recorded self-test had previously passed; the stage still
  failed closed via inventory mismatch, so no false approval path existed.
- Verified: review approved (regression check vs prior commit passed; all
  previous exact-set/status validation intact); full gate
  `20260912-204752-2d13b992` returned `YES`, exit 0 with 38 selftest rows.
