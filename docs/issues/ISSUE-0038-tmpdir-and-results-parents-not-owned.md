# ISSUE-0038: `TMPDIR`/`TEMP`/`TMP` stay inherited, and new results-root parents are created world-readable

- **Status:** Fixed
- **Severity:** Low
- **Category:** isolation
- **Affected:** `qa_common.py:70-89` (`clean_env`), `release.py:342-346` (`prepare_results_root`), `release.py:418` (`work.mkdir`)
- **Confidence:** Confirmed by execution
- **Filed:** 2026-09-12 against harness revision `7aafe90` (third independent review pass)

## Summary

Two owned-path gaps remain: child shells and stages inherit the operator's
`TMPDIR`/`TEMP`/`TMP`, so tools can create temporary files outside the run;
and when `--results-dir` names a path whose parents do not exist,
`Path.mkdir(parents=True, mode=0o700)` applies the private mode only to the
leaf, leaving intermediate directories at the umask default.

## Impact

- Temporary files from target functions, `tar`, `fzf`, or other tools can
  appear outside the owned run under an inherited `TMPDIR`, contrary to the
  "all test mutation within an owned run" contract and leaving stray state to
  clean up.
- Newly created results-root parents (for example `--results-dir
  /some/new/place`) can be group/other-readable (0755 under a 022 umask) while
  `README.md:34` describes the results root as private to the user.

Neither gap writes into the target checkout; both are containment/ownership
hygiene.

## Root cause

`clean_env()` strips the families listed at `qa_common.py:75-79` and then sets
known XDG/HOME variables, but not the temporary-directory variables.
`prepare_results_root()` validates the leaf's mode bits and relies on
`Path.mkdir(parents=True, mode=0o700)`, which does not chmod parents it
creates.

## Reproduction (executed 2026-09-12, sandbox `/tmp/opencode/audit3`)

```
$ python3 - # clean_env with a hostile base env
{'TMPDIR': '/tmp/outside-tmp', 'TEMP': '/tmp/outside-temp', 'TMP': '/tmp/outside-tmp2'}

$ python3 - # mkdir(parents=True, mode=0o700) under umask 022
a: 0o755
b: 0o755
root: 0o700
```

An independent verification pass confirmed `TMPDIR=/tmp/outside-tmp` survives
`clean_env()` and that a fixture commit wrote nothing there incidentally — but
nothing in the harness prevents it.

## Expected behavior

`clean_env()` points `TMPDIR`/`TEMP`/`TMP` at a directory owned by the run
(for example `HOME/.tmp`, created with the isolated HOME), and
`prepare_results_root()` either secures the full created chain or documents
that only the leaf is guaranteed private.

## Proposed fix

- In `make_home()`/`clean_env()`, create `home/.tmp` and set
  `TMPDIR=TEMP=TMP` to it.
- In `prepare_results_root()`, walk the created parents and tighten any
  directory the harness created (or create them explicitly with mode 0o700).
- Update the README's privacy sentence if the parent exception is kept.

## Test plan

- Unit test: `clean_env()` output's `TMPDIR`/`TEMP`/`TMP` resolve under
  `QA_WORK_DIR`, including when the base environment sets hostile values.
- Unit test: `prepare_results_root()` on a nested sandbox path under a 022
  umask leaves every created directory 0700.
- Add both identities to `coverage.json` under `selftest`.

## Fix (2026-09-12, batch 8)

- **Status change:** Open → Fixed.
- `clean_env()` points `TMPDIR`/`TEMP`/`TMP` at `/tmp/zsh-config-qa-<uid>-tmp`
  (mode 0700). The first attempt placed the directory inside the run home —
  which lives inside the harness repository — and the target's own regression
  suite caught the interaction immediately: the target's `croot` case (cd to
  the Git repository root) found the harness repo from its `mktemp` fixtures
  and succeeded where it must fail. The shared root therefore lives **outside
  any repository tree**; it is pruned only by a gate holding the uid lock, and
  the nested-invocation hazard found by review was resolved by removing the
  startup prune entirely (strays are bounded; /tmp is reboot-cleared).
- `prepare_results_root()` now creates the missing parent chain explicitly
  with mode 0700 (never chmods caller-owned directories), validates the leaf's
  mode bits, and rejects a root the user cannot write (`os.access`).
- Fault-injection tests: `test_clean_env_owns_tmpdir` (hostile inherited
  values replaced; private mode; outside the harness repo tree) and
  `test_prepare_results_root_secures_created_parents` (created chain 0700;
  broad caller-owned parent untouched; unwritable root rejected).
- Verified: full gate `20260912-235724-019e9c8b` (`YES`, exit 0), including the target regression suite's 1369
  assertions passing again.
