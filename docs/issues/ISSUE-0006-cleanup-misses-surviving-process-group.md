# ISSUE-0006: cleanup() reports success while a recorded process group whose leader has exited survives

- **Status:** Open
- **Severity:** High
- **Category:** cleanup
- **Affected:** `release.py:113-158`, `release.py:126-129`, `qa_common.py:85-90`
- **Confidence:** Confirmed by execution
- **Filed:** 2026-09-12 against harness `60f633a` plus the pending audit working tree

## Summary

`cleanup()` only signals a recorded process group when the recorded leader PID still has
the exact start-time identity captured at registration:

```python
if entry['start'] and process_identity(entry['pid']) == entry['start']:
    os.killpg(entry['pid'], signal.SIGKILL)
```

(`release.py:126-127`). `process_identity()` returns `None` once the leader is gone or a
zombie (`qa_common.py:85-90`). A process group can outlive its leader when the leader
exits while background members remain; that is exactly the common stage pattern
(`sh -c 'long-running &'`). In that case the identity comparison fails, `killpg` is never
called, and `cleanup()` returns `[]` — printed as `Cleanup verified.` by the
`--cleanup` recovery path (`release.py:240-242`).

## Impact

- The documented SIGKILL recovery path (`README.md:69-75`, `./run-all.zsh --cleanup <run>`)
  can report success while recorded stage processes keep running. After the original
  runner is killed, its Linux subreaper adoption is gone, so `own_descendants()` in the
  recovery process cannot see those orphans either.
- A false "cleanup verified" accompanies a real resource leak (test processes, Nix
  builds, PTY children), which is a High-severity cleanup failure under the rubric and a
  precondition for cross-run interference.
- The in-run path mitigates this through subreaper adoption only while the original runner
  is alive; recovery after SIGKILL/power loss has no such protection.

## Root cause

The registry stores only the leader PID plus its start-time, and cleanup treats leader
identity as a proxy for the whole process group. Group membership is not recorded or
re-derived. Liveness of the group is never tested (`os.killpg(pgid, 0)`) and surviving
members are not enumerated, so the `[start truthy, leader identity mismatch]` case falls
through silently instead of being reported or recovered.

## Reproduction

```sh
mkdir -p /tmp/opencode/audit-r1/issue-0006 && cd /tmp/opencode/audit-r1/issue-0006
python3 - <<'PY'
import json, os, signal, sys, time
from pathlib import Path
HARNESS = '/home/nirmal/projects/zsh-config-qa'
sys.path.insert(0, HARNESS)
import qa_common as common
import release

BASE = Path('/tmp/opencode/audit-r1/issue-0006')
REPO, WORK = BASE / 'repo', BASE / 'run'
REPO.mkdir(parents=True, exist_ok=True); WORK.mkdir(parents=True, exist_ok=True)
(REPO / 'init.zsh').write_text(':\n')
common.atomic_json(WORK / common.MARKER, dict(id='0006', work=str(WORK), repo=str(REPO)))

# Session leader (pgid == pid) starts a background survivor, then exits.
pid = os.fork()
if pid == 0:
    os.setsid()
    os.execvp('sh', ['sh', '-c', 'sleep 300 &'])
start = None
for _ in range(100):
    start = common.process_identity(pid)
    if start:
        break
    time.sleep(0.01)
os.waitpid(pid, 0)  # leader is reaped; only the background sleep remains in pgid
print('recorded start identity :', start)
print('leader identity now     :', common.process_identity(pid))
print('pgid                    :', pid)

survivor = None
for entry in Path('/proc').iterdir():
    if not entry.name.isdigit():
        continue
    try:
        fields = (entry / 'stat').read_text().rsplit(')', 1)[1].split()
        if int(fields[2]) == pid:  # pgrp
            survivor = int(entry.name)
    except (OSError, IndexError, ValueError):
        pass
print('surviving group member  :', survivor, '(alive:', survivor is not None and Path(f'/proc/{survivor}').exists(), ')')

(WORK / 'processes.jsonl').write_text(json.dumps({'pid': pid, 'start': start}) + '\n')
errors = release.cleanup(WORK)
print('cleanup() returned      :', errors)
alive = survivor is not None and Path(f'/proc/{survivor}').exists()
print('survivor alive after    :', alive)
if alive:  # audit must not leave leaked processes behind
    os.killpg(pid, signal.SIGKILL)
    time.sleep(0.1)
    print('survivor after killpg   :', Path(f'/proc/{survivor}').exists())
PY
```

### Observed

```text
recorded start identity : 2353074
leader identity now     : None
pgid                    : 219021
surviving group member  : 219022 (alive: True )
cleanup() returned      : []
survivor alive after    : True
survivor after killpg   : False
```

`cleanup()` returned an empty error list while PID 219022 (same pgid, leader 219021 dead)
was still running. A manual `os.killpg(pid, SIGKILL)` removed the survivor.

## Expected behavior

Recovery cleanup must not report success while any process belonging to a recorded group
is alive. Either the survivor is terminated, or cleanup returns a non-empty error list
naming the surviving pgid/PIDs. "Leader identity mismatched" must be treated as
`unverified`, not `done`.

## Proposed fix

- On registration, record the process-group id (`os.getpgid(pid)`) alongside the leader
  identity, e.g. `{'pid': pid, 'pgid': pgid, 'start': ...}` (`release.py:171-172`,
  `qa_common.py:93-97`).
- In cleanup, for every recorded entry: if the leader identity matches, `killpg(pgid)`;
  otherwise enumerate `/proc/*/stat` for live members whose `pgrp == pgid` and whose
  ancestry/session can be tied to the registered run; kill those groups and, if any
  process remains, append a `process cleanup: surviving pgid <pgid>` error.
- If member-level identity cannot be proven for arbitrary groups, fail closed: an entry
  whose recorded leader is gone but whose pgid still exists must be reported as an error
  (unverified cleanup), never silently dropped.
- Keep the existing PID-reuse guard: never `killpg` from a bare numeric pgid without a
  recorded member identity check.

## Test plan

- Add a self-test that records a group whose leader exits before cleanup (matching the
  reproduction), asserts `cleanup()` is non-empty (or that the survivor is dead), and
  kills the survivor in teardown.
- Add a test that a recorded entry with a stale leader identity but a live pgid yields an
  error, while the existing `test_cleanup_does_not_signal_reused_pid` still passes.
- Update `coverage.json` selftest identities in the same change.
