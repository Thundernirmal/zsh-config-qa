# ISSUE-0024: Stage environment scrub leaves Git/Nix redirect variables that can move fixture writes outside the run

- **Status:** Open
- **Severity:** Medium
- **Category:** isolation
- **Affected:** `qa_common.py:61-78` (scrub set at 64-69), `setup-fixtures.zsh:38-67`, `release.py:267`
- **Confidence:** Confirmed by execution (`GIT_OBJECT_DIRECTORY`); inspection for sibling variables
- **Filed:** 2026-09-12 against harness `60f633a` plus the pending audit working tree

## Summary

`clean_env()` removes `GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE`, `GIT_CONFIG`,
`GIT_CONFIG_COUNT`, `NIX_PROFILES`, and `NIX_PROFILE`, but leaves other Git/Nix
path redirects in the environment it hands to fixtures and cases:
`GIT_OBJECT_DIRECTORY`, `GIT_ALTERNATE_OBJECT_DIRECTORIES`, `GIT_COMMON_DIR`,
`GIT_NAMESPACE`, `NIX_STATE_DIR`, `NIX_CONFIG`, and inherited
`GIT_CONFIG_GLOBAL`/`GIT_CONFIG_SYSTEM`/`GIT_CONFIG_KEY_*`/`GIT_CONFIG_VALUE_*`.

With `GIT_OBJECT_DIRECTORY` exported, every fixture `git add`/`git commit`
(`setup-fixtures.zsh:43-46, 57-66`) writes object files to the operator's
redirected store instead of the owned run, and fixture repositories copied by
`run-safe.zsh:127` resolve objects through that store. The mutated state
survives run deletion and can cross-contaminate later runs, violating
`AGENTS.md`'s "all test mutation must remain within an owned run."

This is distinct from ISSUE-0007: that issue is about the runner's own
`snapshot()` reads being redirected; this one is about stage-side writes
escaping the run.

## Impact

- Fixture commits create object files outside `.runs/` that are never cleaned
  up with the run; a later run reusing the same operator store can see
  unexpected objects.
- `GIT_COMMON_DIR` can redirect refs/config entirely, making fixture behavior
  depend on operator state rather than the owned repository.
- Conditional on an operator-exported variable (workspace/build tooling does
  export these), so not a default-path failure; severity Medium.

## Root cause

The scrub list is a denylist that covers the most common Git overrides but not
the full redirect family, and it is incomplete for `GIT_CONFIG_*` variants.
There is no positive re-assertion of the fixture repository's paths.

## Reproduction

```sh
rm -rf /tmp/opencode/verify-d1 && mkdir -p /tmp/opencode/verify-d1/repo /tmp/opencode/verify-d1/outside
cd /tmp/opencode/verify-d1/repo
export GIT_OBJECT_DIRECTORY=/tmp/opencode/verify-d1/outside   # not scrubbed by clean_env
git init -q . && git config user.email qa@example.invalid && git config user.name QA
echo hi > f && git add f && git commit -qm first
find /tmp/opencode/verify-d1/outside -type f | wc -l   # objects written here
test -d .git/objects && echo yes || echo no             # never created
```

### Observed

```text
objects outside: 3, .git/objects exists: no
```

The commit succeeds while all three objects land under
`/tmp/opencode/verify-d1/outside`; `.git/objects` is not created. The same
environment is what `setup-fixtures.zsh` receives from `clean_env()`.

## Expected behavior

Every inherited Git/Nix path redirect is removed or explicitly replaced before
fixtures run, so all object/ref/state writes stay inside the owned run. A
self-test proves that exporting a redirect variable cannot move fixture writes.

## Proposed fix

- Extend the scrub list in `clean_env()` (`qa_common.py:64-69`) with
  `GIT_OBJECT_DIRECTORY`, `GIT_ALTERNATE_OBJECT_DIRECTORIES`, `GIT_COMMON_DIR`,
  `GIT_NAMESPACE`, `GIT_CEILING_DIRECTORIES`, `GIT_CONFIG_GLOBAL`,
  `GIT_CONFIG_SYSTEM`, `GIT_CONFIG_KEY_*`, `GIT_CONFIG_VALUE_*`, `NIX_STATE_DIR`,
  and `NIX_CONFIG`; strip prefix families, not just exact names.
- Share one `git_env()`/`nix_env()` helper between `clean_env()` and
  `release.snapshot()` so the two layers cannot drift (ISSUE-0007).
- Optionally set `GIT_OBJECT_DIRECTORY`/`GIT_COMMON_DIR` explicitly to the
  fixture repository's own `.git` paths in `setup-fixtures.zsh`, making the
  fixture immune to any future leak.

## Test plan

- Fault-injection test: export `GIT_OBJECT_DIRECTORY=<outside>` and run fixture
  creation; assert objects exist under the run and nothing is written outside.
- Add the test identity to `coverage.json` under `selftest` in the same change.
- Re-run the full gate after the fix.
