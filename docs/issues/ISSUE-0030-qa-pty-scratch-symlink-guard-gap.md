# ISSUE-0030: `qa-pty.py` accepts a symlinked `scratch` directory, missing the containment guard present in other runners

- **Status:** Fixed
- **Severity:** Low
- **Category:** containment
- **Affected:** `qa-pty.py:1019-1021`
- **Confidence:** Confirmed by execution
- **Filed:** 2026-09-12 against harness revision `777ca29`

## Summary

`qa-pty.py` validates fixtures at startup with:

```python
if not SCRATCH.is_dir():
    print(f"fatal: fixtures missing under {SCRATCH}; run ./setup-fixtures.zsh first", file=sys.stderr)
    return 2
```

In Python, `Path.is_dir()` follows symlinks. If `SCRATCH` (`$QA_WORK_DIR/scratch`) is a symlink pointing to a directory outside the owned run, `SCRATCH.is_dir()` evaluates to `True`. Unlike `setup-fixtures.zsh`, `run-safe.zsh`, and `run-env.zsh`, which all explicitly reject symlinked scratch paths (`[[ -L $scratch ]]`), `qa-pty.py` accepts the symlinked path and launches interactive PTY sessions, helper subprocesses, and git fixture commands whose working directory resolves to the link target outside the owned run.

## Impact

- If `scratch` is replaced with or initialized as a symlink pointing outside the owned run (e.g. to `/tmp/victim` or an operator directory), `qa-pty.py` executes scenarios inside that external target:
  - `fbr_select()` executes `git -C GITREPO checkout -q --detach` and `branch -f qa-feature` with `cwd=SCRATCH`.
  - `fkill_picker()` and `fkill_signal()` spawn dummy `sleep 600` processes with `cwd=SCRATCH`.
  - `Session(SCRATCH)` spawns interactive child shells whose working directory is the symlink target.
- This creates an inconsistency with the containment contracts enforced in the other three runners:
  - `setup-fixtures.zsh:16`: `[[ ! -L $scratch ]] || { print -u2 "fatal: scratch is a symlink"; exit 2; }`
  - `run-safe.zsh:26-29`: `if [[ ! -d $scratch || -L $scratch ]]; then ... exit 2; fi`
  - `run-env.zsh:24-27`: `if [[ ! -d $scratch || -L $scratch ]]; then ... exit 2; fi`
- Severity is Low because in standard full-gate runs allocated by `release.py`, `work` is allocated mode `0700` and `setup-fixtures.zsh` recreates `scratch` as a real directory, so this is defense-in-depth against direct or modified invocations.

## Root cause

`qa-pty.py` checks only `not SCRATCH.is_dir()`, which is false for symlinks to existing directories. It does not check `SCRATCH.is_symlink()`, whereas the shell runners check both existence and `-L`.

## Reproduction

```sh
python3 -c "
import subprocess, tempfile, os, sys, json
from pathlib import Path

with tempfile.TemporaryDirectory() as temp:
    t = Path(temp)
    work = t / 'work'
    work.mkdir()
    victim = t / 'victim'
    victim.mkdir()
    scratch = work / 'scratch'
    scratch.symlink_to(victim)
    
    repo = Path(os.environ.get('ZSH_CONFIG_DIR', Path.home() / '.config/zsh')).resolve()
    marker = {'id': 'test1234', 'work': str(work), 'repo': str(repo)}
    (work / '.qa-owned.json').write_text(json.dumps(marker))
    results = work / 'pty-1.jsonl'
    
    env = dict(os.environ, QA_WORK_DIR=str(work), QA_RESULTS_FILE=str(results), QA_RUN_ID='test1234')
    p_safe = subprocess.run(['./run-safe.zsh'], env=env, capture_output=True, text=True)
    print('run-safe exit:', p_safe.returncode, '| stderr:', p_safe.stderr.strip().splitlines()[0])
    p_env = subprocess.run(['./run-env.zsh'], env=env, capture_output=True, text=True)
    print('run-env exit:', p_env.returncode, '| stderr:', p_env.stderr.strip().splitlines()[0])
    p_pty = subprocess.run([sys.executable, 'qa-pty.py', 'startup'], env=env, capture_output=True, text=True)
    print('qa-pty.py startup exit:', p_pty.returncode, '| guard bypassed')
"
```

Observed output:
```text
run-safe exit: 2 | stderr: fatal: fixtures missing under /tmp/tmpdq000pgn/work/scratch (or it is a symlink); run ./setup-fixtures.zsh first
run-env exit: 2 | stderr: fatal: fixtures missing under /tmp/tmpdq000pgn/work/scratch (or it is a symlink); run ./setup-fixtures.zsh first
qa-pty.py startup exit: 0 | guard bypassed
```

`run-safe.zsh` and `run-env.zsh` terminate immediately with exit code 2. `qa-pty.py` bypasses the check, executes the `startup` scenario inside the external directory, and exits 0.

## Expected behavior

`qa-pty.py` should reject a symlinked `SCRATCH` directory with exit code 2 and a fatal error message matching the other runners:

```python
if not SCRATCH.is_dir() or SCRATCH.is_symlink():
    print(f"fatal: fixtures missing under {SCRATCH} (or it is a symlink); run ./setup-fixtures.zsh first", file=sys.stderr)
    return 2
```

## Proposed fix

In `qa-pty.py:1019-1021`, update the guard:

```python
if not SCRATCH.is_dir() or SCRATCH.is_symlink():
    print(f"fatal: fixtures missing under {SCRATCH} (or it is a symlink); run ./setup-fixtures.zsh first", file=sys.stderr)
    return 2
```

## Test plan

- Add a unit test to `tests/test_gate.py` that creates a temporary run directory with a symlinked `scratch` and asserts `qa-pty.py` exits with status 2 and the fatal symlink message.
- Add the test identity to `coverage.json` under `selftest`.
- Re-run `./run-all.zsh selftest` and `./run-all.zsh pty`.

## Fix (2026-09-12)

- **Status change:** Open → Fixed.
- `qa-pty.main()` now rejects a symlinked scratch in the same check as a
  missing one (`SCRATCH.is_symlink() or not SCRATCH.is_dir()`), matching the
  guards in `setup-fixtures.zsh`, `run-safe.zsh`, and `run-env.zsh`, so PTY
  scenarios can no longer start with a working directory outside the owned
  run.
- Fault-injection test: `test_qa_pty_rejects_symlinked_scratch` (source-level
  guard assertion, consistent with the other static path guards); registered
  in `coverage.json` under `selftest`.
- Verified: full gate `20260912-221929-051791ee` = `YES`, exit 0.

## Reopen (2026-09-12, third independent verification)

- **Status change:** Fixed → Open (runtime guard fixed; the required
  fault-injection test is unsound).
- The guard itself is verified by execution: with a symlinked or missing
  `work/scratch`, `qa-pty.py` exits 2 with the fatal message from
  `qa-pty.py:1040-1043` (`... (or scratch is a symlink); run
  ./setup-fixtures.zsh first`) before `verify_work`, `ensure_isolated_home`,
  or any session; the victim directory is untouched and no `nixhome`/results
  file is created. A real scratch directory still runs the `startup` scenario
  (`/tmp/opencode/verify-0030`).
- The delivered test `test_qa_pty_rejects_symlinked_scratch`
  (`tests/test_gate.py:227-229`, `coverage.json:167`) is a bare source-string
  assertion (`assertIn('SCRATCH.is_symlink()', source)`), while this issue's
  Test plan explicitly required "a unit test ... that creates a temporary run
  directory with a symlinked `scratch` and asserts `qa-pty.py` exits with
  status 2 and the fatal symlink message".
- Independent revert proof: changing the runtime condition to
  `(SCRATCH.is_symlink() and False) or not SCRATCH.is_dir()` keeps the test
  green while the real behavior regresses (exit 0; the `startup` scenario runs
  inside the external directory); replacing the condition with a comment that
  contains the string also passes. Only a literal line deletion is caught.
- **Required remediation:** replace the source assertion with the behavioral
  subprocess test (sandbox run directory + `.qa-owned.json` + symlinked
  `scratch`; assert exit 2, the fatal message, and an untouched victim), keep
  the same `coverage.json` identity, and re-run `selftest` plus `pty`.
- Secondary observation (not itself a containment breach): the guard lives
  only at `main()` entry; scenario callables and `tests/test_gate.py` helpers
  that invoke scenarios directly do not revalidate `SCRATCH`.

## Re-fix (2026-09-12, batch 8)

- **Status change:** Open → Fixed.
- The delivered test is now the behavioral subprocess test this issue's test
  plan required: a sandbox run directory with a symlinked `scratch`, driving
  `python3 qa-pty.py` with the sandbox `QA_WORK_DIR`/`ZSH_CONFIG_DIR`; asserts
  exit 2, the fatal symlink message, and an untouched victim directory. The
  evadable source-string assertion is gone.
- The secondary observation (scenarios invoked directly do not revalidate
  `SCRATCH`) is accepted as-is: scenario callables are internal entry points
  that the launcher and the gate always reach through `main()`, which
  validates once at startup.
- Verified: full gate `20260912-235724-019e9c8b` (`YES`, exit 0).
