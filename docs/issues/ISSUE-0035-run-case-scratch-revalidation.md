# ISSUE-0035: `qa_common.run_case()` executes each case with `cwd=work/scratch` without re-validating the directory

- **Status:** Fixed
- **Severity:** Low
- **Category:** containment
- **Affected:** `qa_common.py:166` (`cwd=work / 'scratch'`), with only `verify_work()` at `qa_common.py:148`; contrast `run-safe.zsh:26-29`, `run-env.zsh:24-27`, `setup-fixtures.zsh:16`, and `qa-pty.py:1019` (ISSUE-0030)
- **Confidence:** Confirmed by execution
- **Filed:** 2026-09-12 against harness revision `777ca29`

## Summary

`run_case()` — the function that actually executes every command case — checks
the owned `work` directory with `verify_work(work, repo)`
(`qa_common.py:148`), then launches the child with

```python
result = bounded(['zsh', '-d', '-f', str(script)], env=env, cwd=work / 'scratch', ...)
```

(`qa_common.py:166`). Nothing re-validates that `work/scratch` is a real
directory inside the owned run. The sweep entrypoints reject a missing or
symlinked scratch (`run-safe.zsh:26-29`, `run-env.zsh:24-27`), and
`setup-fixtures.zsh:16` recreates it, but the per-case consumer trusts the path
it is handed. The same gap in `qa-pty.py` is filed as ISSUE-0030; this issue
covers the command-case consumer shared by both sweeps, including direct
invocations of the documented CLI (`python3 qa_common.py case ...`) inside an
owned run.

## Impact

Same family as ISSUE-0018/0030: defense-in-depth against `scratch` being
replaced by a symlink after the entrypoint guard (or a caller invoking
`run_case` directly). The case bodies contain relative recursive operations
(`rm -rf qa-mkcd`, `rm -rf ../gunrepo`, `rm -rf out`), which resolve against the
link target's parent if `cwd` follows a symlink. Normal gate runs are safe: the
run directory is freshly allocated `0700`, `verify_work` rejects a symlinked
`work` or ancestor, and `setup-fixtures.zsh` recreates a real `scratch`. No
normal-flow escape was observed; no automated test guards the sweep guards.

## Root cause

The symlink guard is duplicated at the entrypoints rather than enforced where
the directory is used. `run_case` assumes its caller already validated the
scratch path.

## Reproduction (safe; no destructive command)

```sh
mkdir -p /tmp/opencode/audit-r3/issue-0035/{work,victim,repo}
ln -s /tmp/opencode/audit-r3/issue-0035/victim /tmp/opencode/audit-r3/issue-0035/work/scratch
cd /tmp/opencode/audit-r3/issue-0035
python3 - <<'PY'
import os, sys
from pathlib import Path
sys.path.insert(0, '/home/nirmal/projects/zsh-config-qa')
import qa_common as common

base = Path('/tmp/opencode/audit-r3/issue-0035')
work, repo = base/'work', base/'repo'
(repo/'init.zsh').write_text(':\n')
common.atomic_json(work/common.MARKER, dict(id='issue0035', work=str(work), repo=str(repo)))
os.environ.update(QA_WORK_DIR=str(work), ZSH_CONFIG_DIR=str(repo),
                  QA_RESULTS_FILE=str(work/'results.jsonl'))
rc = common.run_case('scratch-symlink-case', 'print -r -- "$PWD" > "$QA_WORK_DIR/pwd.out"; true')
print('run_case rc:', rc)
print('case PWD was:', (work/'pwd.out').read_text().strip())
PY
```

### Observed

```text
run_case rc: 0
case PWD was: /tmp/opencode/audit-r3/issue-0035/victim
```

The case executed with its working directory resolved through the symlink into
`victim` even though the sweep entrypoints would have rejected the same layout.

## Expected behavior

Every consumer that launches case code with `cwd=work/scratch` rejects a
`scratch` that is missing, a symlink, or outside the owned `work` (a shared
`scratch_path(work)` helper would centralize this, as ISSUE-0018's disposition
suggested), and the guard has a fault-injection test.

## Proposed fix

- Add a small validator (e.g. `qa_common.scratch_path(work)`) that checks
  `work/scratch` is a directory, not a symlink, and inside `work`; call it from
  `run_case` before `bounded(...)`, and reuse it in the three shell runners and
  `qa-pty.py`.
- Add a unit test with a symlinked `scratch` asserting `run_case` fails before
  spawning the child; register the identity in `coverage.json` under `selftest`
  in the same change.

## Test plan

- Fault-injection test as above (and mirror ISSUE-0030's qa-pty guard) with the
  regular `./run-all.zsh selftest` plus `safe env pty` focused stages.
- Keep the normal flow green: a real `scratch` created by `setup-fixtures.zsh`
  must be accepted unchanged.

## Fix (2026-09-12, batch 7)

- **Status change:** Open → Fixed.
- `run_case()` re-validates `work/scratch` (rejects a symlink or a missing
  directory with `ValueError`) before every case and uses the validated path
  as the child's cwd, matching the guards in the sweep entrypoints and
  `qa-pty.py`.
- Fault-injection test: `test_run_case_rejects_unsafe_scratch` (scratch
  replaced by a symlink → `ValueError`; fails on revert, where the case would
  run through the link).
- Verified: full gate `20260912-223424-da8ad654` = `YES`, exit 0.
