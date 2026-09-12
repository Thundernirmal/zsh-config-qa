# ISSUE-0020: Dead code and report cosmetics in the harness sources

- **Status:** Fixed
- **Severity:** Low
- **Category:** hygiene
- **Affected:** `qa_common.py:5`, `tests/test_gate.py:5`, `qa-pty.py:193-203`, `release.py:338`
- **Confidence:** Confirmed by inspection
- **Filed:** 2026-09-12 against harness `60f633a` plus the pending audit working tree
- **Reviewed:** 2026-09-12 against harness `7e97581`; confirmed as non-blocking maintenance only

## Review disposition

Confirmed, but non-blocking. The unused imports/method and Markdown whitespace
are present exactly as described and have no verdict, evidence-integrity, or
runtime impact. They should be handled only as opportunistic maintenance; they
do not justify release work on their own.

## Summary

Four independent, low-impact hygiene defects found during the audit:

1. `hashlib` is imported in `qa_common.py:5` but never used in that module (all
   hashing lives in `release.py`).
2. `signal` is imported in `tests/test_gate.py:5` but never used (the PID tests
   use `release.process_identity`, not `signal`).
3. `Session.wait_quiet()` (`qa-pty.py:193-203`) is defined but called by no
   scenario, test, or helper.
4. The generated `report.md` stage links contain a trailing space in the link
   text, e.g. `[pty-1.log ](pty-1.log)`, because `release.py:338` interpolates
   `{stage.get("log", "details")} ]`.

## Impact

- Maintainer noise and drift risk only: the imports and method can mislead a
  future reader into thinking they participate in behavior, and dead code is a
  place for future changes to land unnoticed.
- The report cosmetic affects the human-readable evidence table, not the
  machine-readable `report.json` or any verdict.

## Root cause

Leftovers from earlier iterations; no lint or unused-symbol check exists in the
harness's own verification commands.

## Reproduction

```sh
cd /home/nirmal/projects/zsh-config-qa
grep -n 'hashlib' qa_common.py
grep -n 'signal' tests/test_gate.py
grep -n 'wait_quiet' qa-pty.py
head -12 .runs/20260912-193357-431ba0a8/report.md
```

### Observed

```text
qa_common.py:5:import hashlib
tests/test_gate.py:5:import signal
qa-pty.py:193:    def wait_quiet(self, quiet: float = 0.35, timeout: float = 20.0) -> None:
# report.md table rows (real run 20260912-193357-431ba0a8):
| selftest | pass | [selftest.log ](selftest.log) |
| pty-1 | pass | [pty-1.log ](pty-1.log) |
```

None of the identifiers appears anywhere else in its file; `wait_quiet` has no
call site.

## Expected behavior

- Unused imports are removed.
- `wait_quiet` is either removed or given a caller and a self-test that proves
  the behavior it claims.
- Generated Markdown link text has no trailing whitespace.

## Proposed fix

```python
# qa_common.py: remove `import hashlib`
# tests/test_gate.py: remove `import signal`
# qa-pty.py: delete Session.wait_quiet (or use it in close_picker and test it)
# release.py:338:
lines.append(f'| {stage["name"]} | {stage["status"]} | '
             f'[{stage.get("log", "details")}]({stage.get("log", "report.json")}) |')
```

## Test plan

- Existing self-test suite must stay green; no new case identity is required for
  import removal or whitespace.
- If `wait_quiet` is kept and wired in, add a fault-injection self-test and its
  identity to `coverage.json` in the same change.
- Re-run `./run-all.zsh` after the change (executable-file change) before any new
  `YES` is claimed.

## Fix (2026-09-12, batch 3)

- **Status change:** Open → Fixed.
- Removed the unused `hashlib` import (`qa_common.py`; hashing lives in
  `release.py`), removed the never-called `Session.wait_quiet`
  (`qa-pty.py`), and removed the trailing space from generated `report.md`
  stage-link text (`release.py`). The unused `p =` assignment before
  `secret-tool clear` in `cleanup()` was also removed as opportunistic
  maintenance (the refuted ISSUE-0017's only actionable residue; `clear` is
  still called and the independent lookup remains the authoritative absence
  check).
- The stale `import signal` in `tests/test_gate.py` is now genuinely used by
  the Batch-2 signal test, so it stays.
- No new test identity required (behavior-neutral); full suite verified:
  gate `20260912-210646-ff36a6ad` = `YES`, exit 0.
