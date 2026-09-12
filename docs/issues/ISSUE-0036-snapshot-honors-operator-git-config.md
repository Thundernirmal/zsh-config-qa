# ISSUE-0036: `snapshot()`/`harness_identity()` honor the operator's HOME-derived Git config, unlike stage environments

- **Status:** Fixed
- **Severity:** Low
- **Category:** isolation / evidence-integrity
- **Affected:** `release.py:25-27` (`git_env`), `release.py:42-64` (`snapshot`), `release.py:67-74` (`harness_identity`); contrast `qa_common.py:85-86` (`GIT_CONFIG_GLOBAL=/dev/null`, `GIT_CONFIG_NOSYSTEM=1`)
- **Confidence:** Confirmed by execution
- **Filed:** 2026-09-12 against harness revision `7aafe90` (third independent review pass)

## Summary

`git_env()` strips every `GIT_*` variable but leaves the operator's `HOME` and
XDG config in place. The target clean/dirty check and the target/harness
fingerprints therefore depend on the operator's global Git configuration,
while every stage runs under `clean_env()`, which neutralizes global/system
config. The two baselines disagree.

## Impact

With `core.excludesFile` (or another global ignore source) covering a file in
the target, `git status --porcelain` reports the target clean and
`ls-files --others --exclude-standard` omits the file from the digest. A target
that is dirty, or that changes an untracked file during a run, can be recorded
`clean`/`target_unchanged` — weakening exactly the check `YES` depends on.
Global `status.showUntrackedFiles=no` has an analogous effect on the dirty
check (inspection). Read-only drift; no write escapes the run.

## Root cause

`snapshot()` runs Git with `env=git_env()` (`release.py:45`), and `git_env()`
(`release.py:25-27`) drops only variables whose names start with `GIT_`.
Global config discovery through `HOME`/`XDG_CONFIG_HOME` is untouched, unlike
the stage environment built by `clean_env()`, which sets
`GIT_CONFIG_GLOBAL=/dev/null` and `GIT_CONFIG_NOSYSTEM=1`.

## Reproduction (executed 2026-09-12, sandbox `/tmp/opencode/audit3/snap`)

```
$ git -C repo status --porcelain
?? untracked.tmp
$ printf '[core]\n\texcludesFile = %s\n' "$S/snap/global-ignore" > snap/home-a/.gitconfig
$ echo 'untracked.tmp' > snap/global-ignore
$ HOME=snap/home-b python3 -c "import release; print(release.snapshot(REPO))"
dirty= True  sha= bed718afda4b00e6
$ HOME=snap/home-a python3 -c "import release; print(release.snapshot(REPO))"
dirty= False sha= dc9e8841b164a87b
```

The same repository is clean with the global excludes file and dirty without
it. Two independent reviews reproduced the digest difference and confirmed the
non-git `harness_identity()` fallback cannot be blinded this way.

## Expected behavior

Snapshot and harness identity use the same Git environment baseline as the
stages: global and system config neutralized, so `dirty` and the digest depend
only on repository content and the target's own ignore files.

## Proposed fix

- Add `GIT_CONFIG_GLOBAL=/dev/null` and `GIT_CONFIG_NOSYSTEM=1` to
  `git_env()` (or to the `bounded()` environment in `snapshot()`).
- Keep the `GIT_*` strip so repository discovery still cannot be redirected.
- Keep the target's own `.gitignore` as the only intended ignore source.

## Test plan

- Sandbox fault injection: a temporary HOME whose `.gitconfig` sets
  `core.excludesFile` to a file covering an untracked target file; assert
  `snapshot(...)['dirty']` stays `True` and the digest equals the neutral-HOME
  digest.
- Add the identity to `coverage.json` under `selftest` in the same change.

## Fix (2026-09-12, batch 8)

- **Status change:** Open → Fixed.
- `git_env()` now sets `GIT_CONFIG_GLOBAL=/dev/null` and `GIT_CONFIG_NOSYSTEM=1`
  in addition to the `GIT_*` strip, so `snapshot()`, `harness_identity()`, and
  `require_git_checkout()` use the same config-neutral baseline as the stage
  environments. The target-clean check and both digests now depend only on
  repository content and the repository's own ignore files.
- Fault-injection test: `test_snapshot_uses_config_neutral_git_env` (a global
  `core.excludesFile` covering an untracked target file must leave
  `dirty=True` and the neutral-HOME digest; verified to fail on revert).
- Verified: independently reproduced the digest drift before the fix; full
  gate `20260912-235724-019e9c8b` (`YES`, exit 0).
