# ISSUE-0043: Fixture Git setup runs unbounded inside `setup-fixtures.zsh`

- **Status:** Fixed
- **Severity:** Low
- **Category:** containment
- **Affected:** `setup-fixtures.zsh:38-66` (all `command git ...` invocations)
- **Confidence:** Confirmed by inspection
- **Filed:** 2026-09-12 against harness revision `7aafe90` (third independent review pass)

## Summary

The fixture stage builds its disposable repositories with direct
`git init/config/add/commit/clone/push` calls. None has an individual
deadline; only the outer `--stage-timeout` (default 1800 s) bounds the stage.
ISSUE-0032 bounded the runner-side identity probes, and ISSUE-0014 bounded
`fbr_select`'s fixture Git calls, but the shared fixture stage was left
outside that pattern.

## Impact

A Git command that stalls (index lock, filesystem/NFS stall, hostile
`~/.gitconfig` hook or alias — the fixture stage does not scrub the runner
environment beyond what `clean_env` provides to the stage process) blocks the
stage for the full outer timeout with no per-command evidence, and the failure
surfaces only as a stage timeout. Fixtures are local and disposable, so the
probability is low; the contract deviation is the point.

## Root cause

`setup-fixtures.zsh` is Zsh and does not use `qa_common.bounded()`; the
`bounded()` primitive is Python-only, so the fixture stage never had a
per-command timeout.

## Reproduction (inspection)

```
$ grep -c 'command git' setup-fixtures.zsh
17
$ grep -c 'timeout' setup-fixtures.zsh
0
```

The only bound is `--stage-timeout` in `release.py:362`.

## Expected behavior

Every external fixture command that can block has a deadline and a descriptive
failure message, consistent with `AGENTS.md` (bounded external commands) and
the fix for ISSUE-0032.

## Proposed fix

- Wrap each Git step with a deadline, for example a small Zsh helper that uses
  `timeout 60 command git ...` and checks the status, or move the Git fixture
  construction behind `qa_common.bounded()` through a Python entry point.
- Record the failing step name in the error output.

## Test plan

- Sandbox check: prepend a sleeping `git` stub on the fixture stage's PATH and
  assert the stage fails within the per-command bound with a message naming
  the step, rather than waiting for the outer stage timeout.
- No `coverage.json` change (fixtures are not an evidence stage).

## Fix (2026-09-12, batch 8)

- **Status change:** Open → Fixed.
- `setup-fixtures.zsh` gained `fgit()`: all 17 fixture Git steps run under
  `command timeout $fixture_timeout` (default 60 s, overridable for testing
  via `QA_FIXTURE_TIMEOUT`) and a failure exits 2 naming the step, consistent
  with the runner-side bounding from 0014/0032.
- Fault-injection test: `test_fixture_git_steps_are_bounded` (a sleeping `git`
  stub first on PATH, `QA_FIXTURE_TIMEOUT=2` → exit 2 within seconds with
  "fixture step ... deadline" on stderr; verified to fail on revert, where the
  stage would wait for the outer stage timeout).
- Verified: full gate `20260912-235724-019e9c8b` (`YES`, exit 0) (fixtures stage green on the real path).
