# ISSUE-0007: snapshot()/harness_identity() are blinded by inherited GIT_DIR/GIT_WORK_TREE

- **Status:** Open
- **Severity:** High
- **Category:** isolation
- **Affected:** `release.py:25-41`, `release.py:44-51`, `release.py:254`, `release.py:321`, `release.py:329`
- **Confidence:** Confirmed by execution
- **Filed:** 2026-09-12 against harness `60f633a` plus the pending audit working tree
- **Reviewed:** 2026-09-12 against harness `7e97581`; confirmed actionable as filed

## Review disposition

Confirmed. `snapshot()` still invokes Git with the inherited runner
environment, independently of the scrubbed stage environment. Git repository
redirection variables can therefore invalidate both target and harness
identity evidence. This is a real false-approval path and remains High.

## Summary

`snapshot()` shells out to `git -C <repo>` with the runner's inherited environment. If the
operator's environment exports `GIT_DIR` (or `GIT_WORK_TREE`), Git ignores the requested
checkout and operates on the exported repository or work tree. `snapshot()` then records
the wrong commit and computes a digest from paths that do not exist in the target. The
same mechanism affects `harness_identity()` and the before/after comparison
(`release.py:254`, `release.py:321`, `harness_identity()` at `release.py:278`/`329`).

`clean_env()` strips `GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE`, `GIT_CONFIG`, and
`GIT_CONFIG_COUNT` for stage children (`qa_common.py:64-69`), but the runner's own
`snapshot()` calls execute before/after that and use `os.environ` unchanged.

## Impact

- `snapshot(target)` can report the harness's commit instead of the target's, so the
  recorded target identity and the `YES` scope are wrong from the first line of the report.
- Because the same tainted environment is used for the `before` and `after` snapshots, a
  target mutated *during* the run can still compare equal, defeating the
  `target_unchanged` guard that `decide()` requires for `YES` (`release.py:321-328`).
- `harness_identity()` can compare a different repository before and after, hiding (or
  fabricating) harness changes.
- `GIT_WORK_TREE` alone does not change the reported commit but does change the digest and
  flips `dirty` to `True`, turning a clean target into a false `NO`/`INCOMPLETE`.

Once observed, these are reproducible environment-dependent redirections, not theoretical
misconfigurations: running the gate from a Git hook, a `git worktree` shell, or any
wrapper that exports `GIT_DIR` is enough.

## Root cause

`snapshot()` assumes `git -C repo` is authoritative. Git's environment variables take
precedence over `-C` for repository discovery (`GIT_DIR`, `GIT_WORK_TREE`,
`GIT_INDEX_FILE`, `GIT_CONFIG*`). The runner validates its own output paths and target
contents but never sanitizes the environment it uses for identity probing; only the
per-stage child environment is scrubbed.

## Reproduction

```sh
mkdir -p /tmp/opencode/audit-r1/issue-0007 && cd /tmp/opencode/audit-r1/issue-0007
python3 - <<'PY'
import os, subprocess, sys
from pathlib import Path
HARNESS = Path('/home/nirmal/projects/zsh-config-qa')
sys.path.insert(0, str(HARNESS))
import release

BASE = Path('/tmp/opencode/audit-r1/issue-0007')
TARGET = BASE / 'target'
subprocess.run(['rm', '-rf', str(TARGET)], check=True)
TARGET.mkdir(parents=True)
(TARGET / 'init.zsh').write_text(':\n')
env = dict(os.environ); env.pop('GIT_DIR', None); env.pop('GIT_WORK_TREE', None)
subprocess.run(['git', 'init', '-q', str(TARGET)], env=env, check=True)
subprocess.run(['git', '-C', str(TARGET), 'add', '.'], env=env, check=True)
subprocess.run(['git', '-C', str(TARGET), '-c', 'user.name=QA', '-c', 'user.email=qa@example.invalid',
                '-c', 'commit.gpgsign=false', 'commit', '-qm', 'target'], env=env, check=True)
harness_head = subprocess.run(['git', '-C', str(HARNESS), 'rev-parse', 'HEAD'],
                              capture_output=True, text=True, env=env).stdout.strip()
target_head = subprocess.run(['git', '-C', str(TARGET), 'rev-parse', 'HEAD'],
                             capture_output=True, text=True, env=env).stdout.strip()

clean = release.snapshot(TARGET)
os.environ['GIT_DIR'] = str(HARNESS / '.git')
tainted = release.snapshot(TARGET)
os.environ.pop('GIT_DIR')
os.environ['GIT_WORK_TREE'] = str(HARNESS)
tainted_wt = release.snapshot(TARGET)
os.environ.pop('GIT_WORK_TREE')

print('target HEAD (truth)     :', target_head)
print('harness HEAD            :', harness_head)
print('snapshot(clean).commit  :', clean['commit'])
print('snapshot(GIT_DIR).commit:', tainted['commit'])
print('commits equal           :', tainted['commit'] == clean['commit'])
print('sha256 equal (GIT_DIR)  :', tainted['sha256'] == clean['sha256'])
print('tainted dirty flag      :', tainted['dirty'])
print('GIT_WORK_TREE commit    :', tainted_wt['commit'])
print('GIT_WORK_TREE dirty     :', tainted_wt['dirty'])
print('GIT_WORK_TREE sha equal :', tainted_wt['sha256'] == clean['sha256'])
PY
```

### Observed

```text
target HEAD (truth)     : acacd1b8abaf97950ed55d01b70673f292871066
harness HEAD            : 60f633acea21dfcaf1baf9048f370a0e6fdd22db
snapshot(clean).commit  : acacd1b8abaf97950ed55d01b70673f292871066
snapshot(GIT_DIR).commit: 60f633acea21dfcaf1baf9048f370a0e6fdd22db
commits equal           : False
sha256 equal (GIT_DIR)  : False
tainted dirty flag      : True
GIT_WORK_TREE commit    : acacd1b8abaf97950ed55d01b70673f292871066
GIT_WORK_TREE dirty     : True
GIT_WORK_TREE sha equal : False
```

With `GIT_DIR` exported, `snapshot(target)` reported the harness commit
`60f633a...`; with only `GIT_WORK_TREE` exported, the digest changed and `dirty` became
`True` although the target checkout was clean and unchanged.

Note (inspection): `clean_env()` removes `GIT_CONFIG`/`GIT_CONFIG_COUNT` but not
`GIT_CONFIG_GLOBAL`, `GIT_CONFIG_SYSTEM`, or `GIT_CONFIG_KEY_*`/`GIT_CONFIG_VALUE_*`, so
those inherited settings reach stage children as well.

## Expected behavior

Identity probing must use the requested checkout regardless of the caller's Git
environment. `snapshot(target)` must return the target's commit and digest when `GIT_DIR`
or `GIT_WORK_TREE` are exported, and must never silently compare harness state as target
state.

## Proposed fix

- Build an explicit Git environment for every identity call, stripping `GIT_DIR`,
  `GIT_WORK_TREE`, `GIT_INDEX_FILE`, `GIT_OBJECT_DIRECTORY`, `GIT_ALTERNATE_OBJECT_DIRECTORIES`,
  `GIT_CONFIG*`, and `GIT_CEILING_DIRECTORIES` (share one helper with `clean_env()`).
- Additionally pass `--git-dir`/`--work-tree` explicitly or use `git -C repo` with
  `env=git_env` so repository discovery cannot be redirected.
- Scrub `GIT_*` once at the top of `main()` before any snapshot, or extend
  `clean_env()`'s key list to cover the missing `GIT_CONFIG_*` variables and reuse it.

## Test plan

- Fault-injection test: create two Git repositories, export `GIT_DIR=<other>/.git`, and
  assert `snapshot(repo)` returns `repo`'s commit/digest and `dirty` reflects only the
  target.
- Same test for `GIT_WORK_TREE`.
- Test that a mutation to the target between `before` and `after` is detected while
  `GIT_DIR` is exported.
- Add the new test identity to `coverage.json` under `selftest`.
