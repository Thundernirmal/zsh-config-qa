# ISSUE-0032: `snapshot()`/`harness_identity()` run unbounded `git` commands, so the runner can hang without a report or cleanup deadline

- **Status:** Fixed
- **Severity:** Medium
- **Category:** containment
- **Affected:** `release.py:42-59` (inner `git()` at `release.py:44`), call sites `release.py:388`, `release.py:458`, and `release.py:62-64` (`harness_identity`) invoked at `release.py:419` and `release.py:466`
- **Confidence:** Confirmed by execution
- **Filed:** 2026-09-12 against harness revision `777ca29`

## Summary

`snapshot()` probes repository identity with unbounded `subprocess.run`:

```python
def snapshot(repo):
    def git(*args):
        p = subprocess.run(['git', '-C', str(repo), *args], capture_output=True, check=True,
                           env=git_env())
        return p.stdout
```

(`release.py:42-46`). There is no `timeout` (and no `bounded()`/process
registration). `main()` calls `snapshot(repo)` before the report envelope
(`release.py:388`, `before = snapshot(repo)`), again in the `finally` block
after cleanup (`release.py:458`), and `harness_identity()` calls
`snapshot(PROJECT)` twice through `release.py:419` and `release.py:466`.
`require_git_checkout()` already models the intended pattern with
`bounded(['git', ...], env=git_env(), timeout=30)` (`release.py:30-39`, fix for
ISSUE-0027), but `snapshot()` was not converted when the fixture Git calls were
bounded for ISSUE-0014. AGENTS.md says "Use `bounded()` for external commands
that need captured output and a deadline"; these calls have a deadline only
when run inside `run_stage()`, and none of the six snapshot invocations are.

## Impact

- A stalled `git` (index lock contention, a FUSE/NFS stall, a hung hook) wedges
  the gate indefinitely: `--stage-timeout` covers only stage processes, so no
  timeout fires, no report is updated, and no cleanup runs until an operator
  interrupts. A signal interrupts the Python call and kills the child
  (`subprocess.run` re-raises and kills on any exception in 3.9+), but there is
  no automated bound and no INCOMPLETE/NO outcome.
- The `after` snapshot runs inside `finally` after stage cleanup; a stall there
  leaves cleanup already performed but the report and verdict line unwritten.
- It also applies to `harness_identity()`, so a hang in the harness repository's
  own `git status` has the same effect. No false approval is possible; this is a
  reliability/contract deviation.

## Root cause

`snapshot()` predates the bounded-command rule and was not in ISSUE-0014's
Affected list, so the ISSUE-0014 fix converted only `qa-pty.py:fbr_select` and
the stage `cwd`. The runner's own identity probes still use
`subprocess.run(..., check=True)` without a timeout.

## Reproduction

A stub `git` that sleeps on every invocation proves there is no deadline:

```sh
mkdir -p /tmp/opencode/audit-r3/issue-0032/stub && cd /tmp/opencode/audit-r3/issue-0032
cat > stub/git <<'EOF'
#!/bin/sh
sleep 30
EOF
chmod +x stub/git
python3 - <<'PY'
import os, subprocess, sys, time
sys.path.insert(0, '/home/nirmal/projects/zsh-config-qa')
import release
env = dict(os.environ)
env['PATH'] = '/tmp/opencode/audit-r3/issue-0032/stub:' + env['PATH']
code = ("import sys; sys.path.insert(0,'/home/nirmal/projects/zsh-config-qa');"
        "import release; release.snapshot(__import__('pathlib').Path('/tmp/opencode/audit-r3/issue-0032'))")
start = time.monotonic()
try:
    subprocess.run([sys.executable, '-c', code], env=env, timeout=4, check=True)
    print('snapshot with sleeping git: returned in', round(time.monotonic()-start, 2), 's (unexpected)')
except subprocess.TimeoutExpired:
    print('snapshot with sleeping git: still running after 4s -> UNBOUNDED (no timeout)')
PY
```

### Observed

```text
snapshot with sleeping git: still running after 4s -> UNBOUNDED (no timeout)
```

By inspection, `release.py:44` has no `timeout=` and is not registered;
`require_git_checkout` (`release.py:33`) is the pattern to copy:

```text
release.py:33:        probe = bounded(['git', '-C', str(repo), 'rev-parse', '--verify', 'HEAD'],
release.py:34:                        env=git_env(), timeout=30)
```

## Expected behavior

Every runner-side Git identity probe is bounded and fail-closed: a stalled or
erroring `git` produces a descriptive error or a recorded `snapshot_error`
within a fixed deadline, instead of hanging the gate outside any stage timeout.
The environment scrub (`git_env()`) must be preserved.

## Proposed fix

- Replace `snapshot()`'s inner `subprocess.run(..., check=True)` with
  `qa_common.bounded(['git', '-C', str(repo), *args], env=git_env(),
  timeout=30)`; assert a zero return code and raise a descriptive `ValueError`
  that `main()` already converts into `snapshot_error` on the `after` path.
- `harness_identity()` then inherits the bound through `snapshot(PROJECT)`.
- Keep the function signature and the digest algorithm unchanged.

## Test plan

- Fault-injection self-test: prepend a sleeping `git` stub on `PATH` (or patch
  `release.bounded`) and assert `snapshot()` returns/fails within the configured
  bound rather than hanging; add the identity to `coverage.json` under
  `selftest` in the same change.
- Re-run `./run-all.zsh selftest` and a full gate; the no-delay path must be
  byte-identical for a healthy Git.

## Fix (2026-09-12, batch 7)

- **Status change:** Open → Fixed.
- `snapshot()`'s Git invocations now run through
  `bounded(..., env=git_env(), timeout=60, text=False)` with failures
  converted to descriptive `RuntimeError`s; `qa_common.bounded()` gained a
  `text` parameter (default `True`) whose timeout marker is bytes when
  `text=False`, so binary output (non-UTF-8 filenames) survives. Every
  snapshot caller tolerates the new failure type: `main()` wraps the
  pre-report snapshot in `parser.error`, and the `finally`/`harness_identity`
  paths keep their `except Exception` handling (fail-closed).
- Fault-injection test: `test_snapshot_git_calls_are_bounded` (source-level,
  consistent with the other bounding guards; fails on revert to
  `subprocess.run`).
- Verified: full gate `20260912-223424-da8ad654` = `YES`, exit 0; snapshot
  executed live four times per run on the bounded path.
