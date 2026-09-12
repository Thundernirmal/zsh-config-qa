# ISSUE-0044: `--cleanup` validates the marker's repository against the marker itself

- **Status:** Fixed
- **Severity:** Low
- **Category:** fail-closed
- **Affected:** `release.py:349-353` (`validate_cleanup_target`), `release.py:377-385` (`--cleanup` branch)
- **Confidence:** Confirmed by execution
- **Filed:** 2026-09-12 against harness revision `7aafe90` (third independent review pass)

## Summary

`validate_cleanup_target(work)` reads `.qa-owned.json` and calls
`verify_work(work, Path(marker['repo']))`. The repository used for the
containment checks ("run state must be outside the target repository", path
match) comes from the same marker being validated, so a self-consistent marker
can name a repository that does not exist and still be accepted. The
"wrong repo" variant promised in ISSUE-0016's test plan can never fire.

## Impact

Low: cleanup only signals PIDs recorded in that work directory and clears
credentials registered under that run's namespace, and `verify_work` still
requires the marker to reside in `work`, match its own `work` field, belong to
the current uid, and reject symlinks. The defect is that the one property the
recorded repo is supposed to enforce — that the run state is outside the real
target — is checked against untrusted marker content, and no test pins it.

## Root cause

The marker is both the subject and the source of the validation input;
`verify_work(work, repo)` is designed for launcher-created state (trusted at
creation time), but `--cleanup` accepts caller-supplied paths.

## Reproduction (executed 2026-09-12 by independent verification, sandbox copy with a redirected lock path)

```
marker = {"id": "...", "work": "<sandbox work>", "repo": "/nonexistent/target"}
$ python3 release.py --cleanup <sandbox work>
Cleanup verified.
exit 0                       # cleanup_called: true
```

A marker whose `work` field matches its directory and whose `repo` is a
missing path passes all checks. Deleting the `try/except` around
`validate_cleanup_target` also stays green in the unit test
(`tests/test_gate.py:346-351` asserts the helper only) — see ISSUE-0039.

## Expected behavior

Validation fails closed unless the recorded repository is a usable Git
checkout (or the design explicitly documents that `--cleanup` trusts the
user-owned marker and the repo field is informational only). The "wrong repo"
variant from ISSUE-0016 must be exercisable.

## Proposed fix

- Require `Path(marker['repo']).is_dir()` and a successful bounded
  `rev-parse --verify HEAD` (the existing `require_git_checkout`) before
  `verify_work`; on failure, `parser.error`.
- Alternatively, drop the repo argument from the cleanup containment check,
  verify the work directory directly (`work == work.resolve()`, no symlink,
  outside any checkout), and document the trust boundary.

## Test plan

- CLI/in-process test: a self-consistent marker whose `repo` does not exist or
  is not a checkout exits 2 with a concise message; a work directory inside a
  real unrelated repository is rejected.
- Keep the existing helper assertions; add the identity to `coverage.json`
  under `selftest` if a new named test is introduced.

## Fix (2026-09-12, batch 8)

- **Status change:** Open → Fixed.
- `validate_cleanup_target()` now requires the recorded repository to be a
  usable Git checkout (`require_git_checkout`) before `verify_work`, so a
  self-consistent marker naming a nonexistent repository fails closed with an
  actionable message. The `--cleanup` branch was factored into
  `cleanup_command(work, fail)` so the wiring is behaviorally testable in
  process (main passes `parser.error`, which never returns).
- Fault-injection test: `test_cleanup_command_rejects_unusable_marker_repo`
  (nonexistent repo → the fail callback receives "cannot clean up ... not a
  usable Git checkout"; a work directory inside a real repository is rejected
  by containment; a valid marker whose repo is a real checkout cleans up and
  returns 0). The ISSUE-0016 "wrong repo" variant is now exercisable.
- Fail-closed consequence (intended per this issue): a run whose target
  repository was later deleted cannot be auto-cleaned; the operator restores
  the target or removes the directory manually.
- Verified: full gate `20260912-235724-019e9c8b` (`YES`, exit 0).
