# ISSUE-0014: Target-owned stages run with `cwd=PROJECT`; fixture Git setup uses unbounded `subprocess.run`

- **Status:** Open
- **Severity:** Low
- **Category:** containment
- **Affected:** `release.py:161-172` (cwd at `release.py:168`), stage commands at `release.py:285-293`; `qa-pty.py:696-700`
- **Confidence:** Confirmed by inspection
- **Filed:** 2026-09-12 against harness `60f633a` plus the pending audit working tree

## Summary

`run_stage()` launches every stage with the harness root as its working directory:

```python
p = subprocess.Popen(argv, cwd=PROJECT, env=stage_env, ...)   # release.py:168
```

while two of the stage commands are target-owned executables invoked by absolute path (`release.py:287,291`):

```python
'regression': ['zsh', str(repo/'scripts/run-tests.zsh')],
'fzf':        [sys.executable, str(repo/'scripts/test-fzf-pty.py')],
```

Today this does not fail because the target's `scripts/run-tests.zsh` changes directory itself (`builtin cd -- "$repo_dir"`, target `scripts/run-tests.zsh:29`) and `scripts/test-fzf-pty.py` derives `REPO_ROOT` from `Path(__file__)` (target `scripts/test-fzf-pty.py:30`). The contract is still wrong: the target's own scripts are executed as if they lived in the harness checkout, and any target script that reads/writes relative paths or relies on the caller's cwd would operate on the harness repository rather than the selected target (or fail with confusing errors).

Separately, `fbr_select()` performs the fixture Git setup with unbounded `subprocess.run`:

```python
subprocess.run(["git", "-C", str(GITREPO), "checkout", "-q", "--detach"], check=True)   # qa-pty.py:699
subprocess.run(["git", "-C", str(GITREPO), "branch", "-f", "qa-feature"], check=True)   # qa-pty.py:700
```

No `timeout`, no output capture, no `bounded()`/process registration. If Git blocks (index lock contention, stalled filesystem, a hook), the scenario hangs until the outer `--stage-timeout` (default 1800 s) kills the whole stage group; the captured stage log then shows nothing about which Git command was stuck, and there is no per-command evidence.

## Impact

- **Containment/contract:** stage cwd is the private harness project, not the target under test. A future target change (or a target script with relative output paths) can write artifacts into `zsh-config-qa/` or silently test the wrong tree. This is a contract violation even while the current target happens to `cd` itself.
- **Bounding:** the unbounded Git calls violate the "use `bounded()` for external commands that need captured output and a deadline" rule; the only backstop is the stage-wide timeout, which also destroys the rest of the stage's evidence and runs cleanup on a healthy run.

## Expected behavior

- Stages that execute target-owned code should run with `cwd=repo` (the target), so the target's scripts see the same working directory a user/release process would provide. Harness-owned stages (`selftest`, `fixtures`, `safe`, `env`, `pty`) can keep `cwd=PROJECT` or receive an explicit cwd per stage.
- Fixture Git commands should go through `bounded([...], cwd=…, timeout=…)`, have their `returncode` asserted, and be registered for cleanup like other external processes.

## Proposed fix

1. Give `run_stage()` an explicit `cwd` argument (default `PROJECT`) and pass `cwd=repo` for `regression` and `fzf`; keep the absolute argv paths.
2. Replace the two `subprocess.run` calls in `fbr_select()` with `bounded(["git", ...], cwd=GITREPO, timeout=60)` plus explicit returncode checks and captured stderr in the failure detail. `qa_common.bounded` already kills the process group on timeout.

## Test plan

- Self-test: assert the stage plan maps `regression`/`fzf` to `cwd=repo` (testable by inspecting the `run_stage` call configuration or by running a stub target script that prints `$PWD`).
- Fault-injection: prepend a stub `git` on `PATH` that sleeps; run `fbr_select` and assert it fails within the configured bound rather than hanging (mirrors the existing bounded/timeout tests in `tests/test_gate.py`).
- Re-run `./run-all.zsh selftest safe env` and `./run-all.zsh` before claiming full coverage; no target files change.
