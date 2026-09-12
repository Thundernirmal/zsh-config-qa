# ISSUE-0018: `scratch` symlink is rejected only in `setup-fixtures.zsh`; sweeps check only `-d`

- **Status:** Fixed
- **Severity:** Low
- **Category:** containment
- **Affected:** `setup-fixtures.zsh:16` (guard) vs `run-safe.zsh:26-29` and `run-env.zsh:24-27` (missing guard)
- **Confidence:** Confirmed by execution (guard behavior); destructive consequence analyzed only
- **Filed:** 2026-09-12 against harness `60f633a` plus the pending audit working tree
- **Reviewed:** 2026-09-12 against harness `7e97581`; confirmed as defense-in-depth only

## Review disposition

Confirmed at Low severity. Normal launcher flow creates a private run and the
fixture stage replaces `scratch` with a real directory, so this is not a
demonstrated normal-flow escape. The consumers nevertheless perform relative
destructive operations without revalidating the directory they enter. A
shared scratch validator would close that narrow state-replacement gap without
weakening the existing ownership boundary.

## Summary

The fixture stage refuses a symlinked scratch directory:

```sh
[[ ! -L $scratch ]] || { print -u2 "fatal: scratch is a symlink"; exit 2; }   # setup-fixtures.zsh:16
```

but the two consumers only test that the path is a directory:

```sh
if [[ ! -d $scratch ]]; then                                                  # run-safe.zsh:26
  print -u2 -r -- "fatal: fixtures missing under $scratch; run ./setup-fixtures.zsh first"
  exit 2
fi
```

and the same `[[ ! -d $scratch ]]` in `run-env.zsh:24`. `[[ -d ]]` is true for a symlink to a directory, so neither sweep re-validates that `$scratch` is a real directory inside the owned run. `qa_common.run_case()` then executes every case with `cwd=work/'scratch'` (`qa_common.py:153`), and the case bodies contain recursive deletes such as `rm -rf qa-mkcd` (`run-safe.zsh:96`), `rm -rf ../gunrepo` (`run-safe.zsh:127`), and `rm -rf out` (`run-safe.zsh:108`). Through a symlinked `scratch`, `../gunrepo` resolves relative to the *link target's parent*, outside the owned run.

## Impact

Normal `./run-all.zsh` invocations are safe: the run directory is freshly created `0700`, `verify_work()` rejects a symlinked `QA_WORK_DIR` or any symlinked ancestor (`qa_common.py:26-32`), and `setup-fixtures.zsh` deletes and recreates `scratch` as a real directory. The gap is defense in depth and applies when:

- `QA_WORK_DIR` is supplied by the caller/operator to an existing or shared location and `scratch` is (or becomes) a symlink after `verify_work()`;
- another process running as the same user swaps `scratch` between the fixture stage and the sweep (`run-safe`/`run-env` never re-check);
- a future entrypoint calls `run-safe.zsh`/`run-env.zsh` without the fixture guard.

In those cases the sweeps' relative `rm -rf` operations can escape the owned run and delete content elsewhere. Splitting the guard between stages also makes the check inconsistent and easy to lose.

## Reproduction (check gap only; no destructive command executed)

```sh
$ mkdir -p /tmp/opencode/audit-r2/issue18/work /tmp/opencode/audit-r2/issue18/victim
$ ln -s /tmp/opencode/audit-r2/issue18/victim /tmp/opencode/audit-r2/issue18/work/scratch
$ ls -l /tmp/opencode/audit-r2/issue18/work/scratch
lrwxrwxrwx 1 nirmal nirmal 37 Sep 12 20:12 /tmp/opencode/audit-r2/issue18/work/scratch -> /tmp/opencode/audit-r2/issue18/victim
$ zsh -f -c '
scratch=/tmp/opencode/audit-r2/issue18/work/scratch
if [[ ! -d $scratch ]]; then print -r -- "run-safe/run-env check: scratch MISSING (rejected)"; else print -r -- "run-safe/run-env check: [[ -d ]] passes for symlink -> $(readlink $scratch)"; fi
if [[ -L $scratch ]]; then print -r -- "setup-fixtures guard: [[ ! -L ]] rejects the symlink"; else print -r -- "setup-fixtures guard: would accept"; fi
cd -- $scratch && print -r -- "cwd resolves through symlink to: $PWD -> $PWD:P"
'
run-safe/run-env check: [[ -d ]] passes for symlink -> /tmp/opencode/audit-r2/issue18/victim
setup-fixtures guard: [[ ! -L ]] rejects the symlink
cwd resolves through symlink to: /tmp/opencode/audit-r2/issue18/work/scratch -> /tmp/opencode/audit-r2/issue18/victim
```

No `rm -rf` was run against the symlink; the output shows only that the guard would not have stopped the sweeps and that `cwd` resolves into the link target.

## Expected behavior

Every consumer of `$scratch` rejects a symlinked scratch path (and, ideally, a scratch that is not inside the owned `WORK`) before running any case, with the same fatal behavior as `setup-fixtures.zsh:16`.

## Proposed fix

Add the guard to both sweeps, immediately after the `-d` check:

```sh
if [[ ! -d $scratch || -L $scratch ]]; then
  print -u2 -r -- "fatal: unsafe scratch under $scratch; run ./setup-fixtures.zsh first"
  exit 2
fi
```

Better: centralize it in `qa_common.py` (e.g. `verify_scratch(work)`) and call it from `setup-fixtures.zsh`, `run-safe.zsh`, and `run-env.zsh` so ownership/path rules live in one place; the helper should also verify `$scratch` is inside `$work` and not a symlink. Keeping `qa_common.py verify` as the single ownership gate is consistent with the existing architecture.

## Test plan

- Self-test (or shell check): create a `/tmp` work-like directory containing a `scratch` symlink to a sentinel directory; invoke the guard helper and assert it exits nonzero; then invoke it with a real directory and assert success. Add the check to `tests/test_gate.py` and its identity to `coverage.json` if it exercises harness code.
- Manual: after the fix, run `./run-all.zsh safe env` in the normal flow and confirm the fatal path is never taken.

## Fix (2026-09-12, batch 5)

- **Status change:** Open → Fixed.
- run-safe.zsh and run-env.zsh now reject a missing **or symlinked** scratch
  before any case runs (`[[ ! -d $scratch || -L $scratch ]]` → fatal, exit 2),
  closing the window where only `setup-fixtures.zsh` checked. The fixture
  stage recreates a real directory, and `verify_work` independently rejects
  symlinked work paths, so no legitimate layout is refused.
- Verified: review approved; full gate `20260912-215025-6c8985a5` = `YES`,
  exit 0.
