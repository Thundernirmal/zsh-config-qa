# ISSUE-0014: Target-owned stages run with `cwd=PROJECT`; fixture Git setup uses unbounded `subprocess.run`

- **Status:** Fixed
- **Severity:** Low
- **Category:** containment
- **Affected:** `release.py:161-172` (cwd at `release.py:168`), stage commands at `release.py:285-293`; `qa-pty.py:696-700`
- **Confidence:** Confirmed by inspection
- **Filed:** 2026-09-12 against harness `60f633a` plus the pending audit working tree
- **Reviewed:** 2026-09-12 against harness `7e97581`; partially confirmed and requires scope correction

## Review disposition

Partially confirmed. The two direct Git setup calls are genuinely unbounded
relative to the normal per-command contract and should use the shared bounded
primitive. A single implicit cwd for every stage is also brittle. However, the
proposed `cwd=repo` correction is unsafe for this project: the selected target
must remain read-only, and a target script using relative writes should not be
given its checkout as the write destination. Any cwd change should be explicit
per stage and use an owned run location unless the target script self-locates
without writing. Keep this issue open for the bounded-call fix and revised cwd
design; do not apply its current proposed cwd verbatim.

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

## Fix (2026-09-12, batch 6)

- **Status change:** Open → Fixed.
- **Owned cwd for every stage:** `release.run_stage` now launches all stage
  commands with `cwd=work` (the owned 0700 run directory) instead of
  `cwd=PROJECT`. Verified that every stage self-locates: `selftest.py` and
  `qa-pty.py` resolve via `__file__`/env, the target's `run-tests.zsh` `cd`s to
  its repo, and the target's `test-fzf-pty.py` derives `REPO_ROOT` from
  `__file__`. A stray relative write now lands in the run directory instead of
  the harness repository (where it would have invalidated the harness
  fingerprint mid-run). Sweep case children keep their own
  `cwd=work/scratch`.
- **Bounded fixture Git:** `fbr_select`'s detach and branch force-create now
  use `common.bounded(argv, cwd=SCRATCH, timeout=30)` with asserted return
  codes (registered processes, whole-group timeout kill) instead of unbounded
  `subprocess.run`.
- Fault-injection tests: `test_stage_commands_run_in_the_owned_run_directory`
  (fails if `cwd=PROJECT` returns) and `test_fbr_fixture_git_setup_is_bounded`
  (asserts `bounded` and the absence of `subprocess.run(["git"` in the
  scenario body).
- Verified: full gate `20260912-215458-9601a735` = `YES`, exit 0.
