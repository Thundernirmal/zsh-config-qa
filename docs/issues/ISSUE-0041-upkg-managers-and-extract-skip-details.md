# ISSUE-0041: `upkg managers` fails on a host without a package manager instead of skipping; archive skip detail misnames the cause

- **Status:** Fixed
- **Severity:** Low
- **Category:** contract-drift
- **Affected:** `run-safe.zsh:192` (`upkg managers`), `run-safe.zsh:131-134` (`extract tar.xz`/`extract tar.bz2`), `setup-fixtures.zsh:29-34`
- **Confidence:** Confirmed by inspection; target behavior confirmed in the target checkout
- **Filed:** 2026-09-12 against harness revision `7aafe90` (third independent review pass)

## Summary

ISSUE-0025 established the skip policy for cases whose optional tools are
missing: record `skip`, aggregate `INCOMPLETE`, never `NO`. Two residuals
remain. `upkg managers` is still an unguarded `qa_nz`, so a host with none of
the supported package managers records a failure. The `extract tar.xz` and
`extract tar.bz2` cases skip with `missing fixture ...` when the real cause is
the absent compressor, because the fixture stage only creates those archives
when `xz`/`bzip2` exist.

## Impact

- A host without any supported manager (pacman/apt/dnf/brew/npm/...) turns
  the `safe` stage into `NO` (exit 1) even though the harness itself declares
  such optional integrations incomplete, not failed.
- The archive skip detail points at a missing fixture instead of the missing
  `xz`/`bzip2`, misleading diagnosis.

## Root cause

`upkg managers` has no single binary to gate on, so the `qa_opt`/`qa_opt_nz`
helpers do not apply directly and the case was left as a plain `qa_nz`
(`run-safe.zsh:192`). The archive cases call `qa_fixture` without a `needs`
argument (`run-safe.zsh:131-134`), while `setup-fixtures.zsh:29-34` skips
archive creation when the compressor is absent.

## Reproduction (inspection, with target behavior from the selected checkout)

```
$ sed -n '192p' run-safe.zsh
qa_nz 'upkg managers'      'upkg managers'
$ grep -n 'No supported package managers' ~/.config/zsh/lib/functions-upkg.zsh
1064:    print -u2 -- 'No supported package managers detected.'
$ grep -n 'sample.tar.xz' setup-fixtures.zsh
30:  command tar cJf files/sample.tar.xz -C files archive-src.txt   # only when xz exists
```

The target's own regression suite asserts that `upkg managers` retains the
`No supported package managers detected.` error detail
(`scripts/test-upkg.zsh:938`), i.e. a nonzero exit on such a host is expected
target behavior, not a defect to fail the sweep for.

## Expected behavior

A host with no supported package manager records `upkg managers` as a skip
with a clear detail (for example `no supported package managers`), keeping the
stage `incomplete`; the archive cases record `missing xz`/`missing bzip2`
rather than a fixture name.

## Proposed fix

- Detect the no-manager case (probe `upkg managers` in a controlled child and
  skip when the target's "No supported package managers detected." message
  appears, or gate on the set of manager binaries the target supports) and
  route it through `qa_skip`.
- Pass `xz`/`bzip2` as the `needs` argument of the two `qa_fixture` calls so
  the skip detail names the compressor.

## Test plan

- Simulate a PATH without any supported manager (or stub `upkg`) and assert
  the case records `skip` and the stage aggregates `incomplete`, not `fail`.
- With `xz` removed from PATH, assert the `extract tar.xz` skip detail names
  `xz`.
- Update `coverage.json` only if case names change (they should not).

## Fix (2026-09-12, batch 8)

- **Status change:** Open → Fixed.
- `run-safe.zsh` now probes the target child once for `upkg managers` and
  records `skip: no supported package managers` when the target's own
  "No supported package managers detected." diagnostic appears (the
  target-expected nonzero exit on such hosts), else runs the case as before;
  the case records exactly once either way. `extract tar.xz`/`extract tar.bz2`
  now pass `needs='xz'`/`needs='bzip2'` to `qa_fixture`, so the skip detail
  names the compressor instead of a fixture name. Case names unchanged.
- The simulated no-manager host was not exercised live on this machine (a
  manager exists); the branch is a one-line `qa_skip` consistent with the
  established gating helpers.
- Verified: full gate `20260912-235724-019e9c8b` (`YES`, exit 0), safe 63/63 pass.
