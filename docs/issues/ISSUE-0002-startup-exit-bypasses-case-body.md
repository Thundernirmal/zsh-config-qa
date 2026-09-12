# ISSUE-0002: A target init.zsh that calls `exit 0` makes run_case pass without executing the case body

- **Status:** Fixed
- **Severity:** High
- **Category:** fail-open
- **Affected:** `qa_common.py:148-157`
- **Confidence:** Confirmed by execution
- **Filed:** 2026-09-12 against harness `60f633a` plus the pending audit working tree
- **Reviewed:** 2026-09-12 against harness `7e97581`; confirmed with scope clarification

## Review disposition

Confirmed. `run_case()` still has no parent-observed sentinel proving control
returned from `source` and entered the case body. A sourced `exit 0` therefore
records that safe/env case as passing. This makes required case evidence
vacuous and warrants a fix. Scope clarification: the defect can contribute to,
but does not by itself guarantee, a full false `YES`, because regression and
PTY stages provide independent failure opportunities.

## Summary

`run_case()` builds a child Zsh script that first sources the target's `init.zsh` and then
runs the case body (`qa_common.py:148-151`). A `source`d file executes in that same shell,
so an `init.zsh` containing `exit 0` terminates the script during startup, before the
`qa_source_rc` check and before the case body. The parent only sees the process exit
status (`qa_common.py:157`): exit `0`, empty startup stderr, therefore `pass`. The case
body never ran, no marker exists, and the ledger records `status: "pass"`.

`AGENTS.md` requires "Startup stderr or a nonzero source status fails the case"; this path
evades both checks because the shell exits cleanly from inside `source`.

## Impact

Every command-sweep case (`qa`, `qa_nz`, and `qa_opt` cases) and every environment case
would pass vacuously if the selected checkout's `init.zsh` exits early. A checkout with a
startup bug that `exit 0`s (or a malicious/regressed `init.zsh`) produces a green sweep and
can contribute to a false `YES`. This is a required-coverage fail-open, not just a
cosmetic reporting defect.

## Root cause

The generated script relies on control returning to the line after `source` to detect
startup failure. `exit` is not just an error return; it ends the script. There is no
sentinel proving the body started, and `run_case()` treats "exit 0 + empty stderr" as
proof of successful startup and body execution.

## Reproduction

```sh
mkdir -p /tmp/opencode/audit-r1/issue-0002 && cd /tmp/opencode/audit-r1/issue-0002
python3 - <<'PY'
import json, os, shutil, sys
from pathlib import Path
HARNESS = '/home/nirmal/projects/zsh-config-qa'
sys.path.insert(0, HARNESS)
import qa_common as common

BASE = Path('/tmp/opencode/audit-r1/issue-0002')
REPO, WORK = BASE / 'repo', BASE / 'work'
shutil.rmtree(REPO, ignore_errors=True); shutil.rmtree(WORK, ignore_errors=True)
REPO.mkdir(parents=True)
(REPO / 'init.zsh').write_text('exit 0\n')
WORK.mkdir(); (WORK / 'scratch').mkdir()
common.atomic_json(WORK / common.MARKER, dict(id='issue0002', work=str(WORK), repo=str(REPO)))

os.environ['QA_WORK_DIR'] = str(WORK)
os.environ['ZSH_CONFIG_DIR'] = str(REPO)
os.environ['QA_RESULTS_FILE'] = str(WORK / 'results.jsonl')

rc = common.run_case('startup-exit-trap',
                     'print -r -- BODY_RAN > "$QA_WORK_DIR/body-ran"; false')
record = json.loads((WORK / 'results.jsonl').read_text())
print('run_case return code :', rc)
print('recorded status      :', record['status'])
print('recorded exit_code   :', record['exit_code'])
print('case body marker     :', (WORK / 'body-ran').exists())
print('stdout file contents :', repr((WORK / (record['evidence'] + '.stdout')).read_text()))
PY
```

### Observed

```text
PASS       startup-exit-trap
run_case return code : 0
recorded status      : pass
recorded exit_code   : 0
case body marker     : False
stdout file contents : ''
```

The body `...; false` was never executed (no marker, no output), yet the case was recorded
as `pass` and `run_case` returned `0`.

## Expected behavior

A target whose `init.zsh` terminates the shell — with any status — must fail the case, not
pass it. The harness must be able to distinguish "startup returned with rc 0" from
"startup ended the process".

## Proposed fix

Prove the body was reached. After the `source` line and diagnostics check, write a sentinel
before running the body, and require it in the parent after `bounded()` returns:

```zsh
source ... 2>startup.stderr
qa_source_rc=$?
if (( qa_source_rc != 0 )) || [[ -s startup.stderr ]]; then ...; exit 121; fi
: > "$QA_WORK_DIR/cases/$token.body-started"
<code>
```

Then in `run_case()`: `passed = result.returncode == 0 and sentinel.exists() and ...`; on a
missing sentinel record `fail` with detail "startup exited before case body". The sentinel
must be fresh per case (already unique via `token`) and must not be satisfiable by echoed
text.

## Test plan

- Fault-injection self-test (mirrors `test_startup_nonzero_rejected`): set
  `init.zsh` to `exit 0` and assert `run_case('exit-startup', 'false') == 1` and that the
  ledger row is `fail`.
- Keep the existing stderr and `return 7` startup tests green.
- Add the new test identity to `coverage.json` under `selftest`.

## Fix (2026-09-12, batch 1)

- **Status change:** Open → Fixed.
- `qa_common.run_case()` now writes a fresh per-case sentinel
  (`cases/<token>.body-started`) with `: >` after the `init.zsh` startup
  rc/stderr gate and before the case body, and the parent requires it before
  recording `pass`. A missing sentinel fails the case with detail
  "startup exited before the case body ran (possible exit in init.zsh)",
  regardless of the child's exit status.
- Fault-injection test added: `test_startup_exit_cannot_pass_case`
  (`init.zsh` = `exit 0`, case body `false`, asserts `fail` and the detail),
  registered in `coverage.json` in the same change.
- Verified: review approved (sentinel is per-case, checked only after the
  child exits, and cannot be satisfied by echoed text); full gate
  `20260912-204752-2d13b992` returned `YES`, exit 0.
