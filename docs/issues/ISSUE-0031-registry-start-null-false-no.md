# ISSUE-0031: Registry records `start: null` for already-exited children; strict cleanup validation turns it into a false `NO`

- **Status:** Fixed
- **Severity:** Medium
- **Category:** cleanup
- **Affected:** `qa_common.py:104-108` (`register_process`), `release.py:276-277` (`run_stage` registry write), `release.py:216-218` (strict validation), `release.py:202-263` (`cleanup`)
- **Confidence:** Confirmed by execution
- **Filed:** 2026-09-12 against harness revision `777ca29`

## Summary

`register_process()` and `run_stage()` record each owned process immediately
after `Popen()`:

```python
with open(Path(work) / 'processes.jsonl', 'a') as stream:
    stream.write(json.dumps({'pid': pid, 'start': process_identity(pid)}) + '\n')
```

(`qa_common.py:104-108`; `release.py:276-277` has the same shape). For a child
that has already exited but is not yet reaped — a zombie — `process_identity()`
returns `None` (`qa_common.py:96-101` returns `None` when `/proc/<pid>/stat`
state is `Z`), so the registry line becomes `{"pid": N, "start": null}`.

Since commit `2ea2d8b` (the ISSUE-0006/0012 fix), `cleanup()` requires `start`
to be a string and raises `invalid registry entry` for any other value
(`release.py:216-218`), which is appended to the cleanup error list
(`release.py:235-236`). A single error sets `cleanup_ok=False` and `decide()`
returns `NO` (exit 1) even when every stage passed (`release.py:141-146`).

Before that validation, the entry was silently skipped
(`if entry['start'] and ...`), because a child with no recorded identity cannot
be a surviving leak. The fix for malformed registry lines therefore introduced a
new false-failure path for a pre-existing registration race.

## Impact

- Intermittent false `NO` on healthy runs. Two retained runs on the fixed tree
  fail exactly this way with a passing stage and no target/harness fault:
  `.runs/20260912-220250-f918321c` (`NO`, `selftest: pass`,
  `process cleanup: invalid registry entry: '{"pid": 85477, "start": null}'`)
  and `.runs/20260912-220408-dd33fcec` (`NO`, `selftest: pass`,
  `process cleanup: invalid registry entry: '{"pid": 88541, "start": null}'`).
- `register_process()` is called for **every** `bounded()` command
  (`qa_common.py:115`), including the fast `tool_metadata()` `--version` probes
  (`release.py:86`), so the zombie window recurs across runs. A pre-fix
  full-gate run with the same `start: null` line still returned `YES`
  (`.runs/20260912-204421-fa98b636/processes.jsonl`), confirming the entry
  itself does not indicate a leak.
- Operators see a cleanup failure naming a long-gone PID instead of a clean
  verdict; focused runs are affected as well as full runs.

## Root cause

Registration captures `process_identity()` synchronously after `Popen()`; a
child that exits before that read is a zombie and yields `None`. The registry
then stores JSON `null`, and `cleanup()` conflates "corrupted ledger entry" with
"the child was already gone when the entry was written". The process is dead, so
there is nothing to recover and no cleanup defect to report.

## Reproduction

Deterministic; the child is a zombie and has not been reaped when its identity is
read, exactly as in `bounded()` between `Popen()` and `communicate()`:

```sh
mkdir -p /tmp/opencode/audit-r3/issue-0031/{work,repo} && cd /tmp/opencode/audit-r3/issue-0031
python3 - <<'PY'
import os, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, '/home/nirmal/projects/zsh-config-qa')
import qa_common as common, release

base = Path('/tmp/opencode/audit-r3/issue-0031'); work, repo = base/'work', base/'repo'
common.atomic_json(work/common.MARKER, dict(id='issue0031', work=str(work), repo=str(repo)))
os.environ['QA_WORK_DIR'] = str(work)

p = subprocess.Popen(['true'])
time.sleep(0.3)                      # child exited, still unreaped -> zombie
print('process_identity(zombie):', common.process_identity(p.pid))
common.register_process(p.pid)       # exact path bounded() uses
print('processes.jsonl:', (work/'processes.jsonl').read_text().strip())
errors = release.cleanup(work)
print('cleanup errors:', errors)
p.wait()
print('healthy run would be:', release.decide([dict(status='pass')], False, True, True, not errors))
PY
```

### Observed

```text
process_identity(zombie): None
processes.jsonl: {"pid": 111174, "start": null}
cleanup errors: ['process cleanup: invalid registry entry: \'{"pid": 111174, "start": null}\'']
healthy run would be: ('NO', 1)
```

Retained-run evidence (read-only):

```sh
python3 - <<'PY'
import json
from pathlib import Path
for run in ('20260912-220250-f918321c', '20260912-220408-dd33fcec'):
    report = json.loads((Path('.runs')/run/'report.json').read_text())
    print(run, report['verdict'], report['cleanup_errors'])
PY
```

```text
20260912-220250-f918321c NO ['process cleanup: invalid registry entry: \'{"pid": 85477, "start": null}\'']
20260912-220408-dd33fcec NO ['process cleanup: invalid registry entry: \'{"pid": 88541, "start": null}\'']
```

## Expected behavior

A child that had already exited when it was registered cannot be a surviving
leak; its entry must not turn cleanup into a failure. Genuinely malformed lines
(`[]`, `"junk"`, `42`, `{"pid": 1}`) must still produce cleanup errors, and
entries with a valid recorded identity keep their existing PID-reuse protection.

## Proposed fix

- In `register_process()` and the `run_stage()` write, only write an entry when
  `process_identity(pid)` returns a string; if the child is already gone, omit
  the entry (nothing can survive to recover) or record an explicit
  `"start": null` marker that `cleanup()` accepts as "already exited at
  registration".
- In `cleanup()`, keep rejecting non-dict lines, non-int/bool `pid`, and
  non-string/non-null `start`, but treat a `null` start as a no-op rather than
  an error. Keep the existing malformed-entry tests green.
- Add a fault-injection test that registers a real zombie with a sandbox
  `QA_WORK_DIR` and asserts `cleanup()` returns `[]`, and record its identity in
  `coverage.json` under `selftest` in the same change.

## Test plan

- Deterministic zombie registration test as in Reproduction (no stage runs).
- Keep `test_cleanup_tolerates_malformed_registry_entries` unchanged and green.
- Add the new test identity to `coverage.json` and re-run `./run-all.zsh
  selftest` and a full gate before claiming a new `YES`.

## Fix (2026-09-12, batch 7)

- **Status change:** Open → Fixed.
- `cleanup()` now treats `"start": null` as a **known state** (the leader was
  registered after it exited, e.g. a zombie) rather than a malformed entry:
  such entries take the leaderless-group branch, so live non-descendant
  members are still reported as unverified while a quiet registry produces no
  error — the false `NO` path is gone. A missing `start` key, a non-string
  non-null start, a bool pid, and non-object rows remain invalid, so the
  ISSUE-0012 protections are unchanged.
- Fault-injection test: `test_cleanup_accepts_uncaptured_start_identity`
  (null-start entry with no live members → `cleanup()` returns `[]`; fails on
  revert, where the strict check raises). The existing malformed-entry test
  still requires a missing key to error.
- Verified: full gate `20260912-223424-da8ad654` = `YES`, exit 0.
