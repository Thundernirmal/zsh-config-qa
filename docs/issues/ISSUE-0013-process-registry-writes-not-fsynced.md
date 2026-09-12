# ISSUE-0013: Process-registry writes are not fsync'd, unlike case records

- **Status:** Refuted
- **Severity:** Low
- **Category:** cleanup
- **Affected:** `release.py:171-172`, `qa_common.py:93-97`, compare `qa_common.py:121-131`
- **Confidence:** Confirmed by execution
- **Filed:** 2026-09-12 against harness `60f633a` plus the pending audit working tree
- **Reviewed:** 2026-09-12 against harness `7e97581`; refuted because the reproduction does not establish the claimed defect

## Review disposition

Refuted. The demonstrated zero-length file occurs when the writer is killed
before the stream is closed; an `fsync()` placed after `write()`/`flush()` has
the same unavoidable pre-sync interruption window and cannot repair that
case. After normal close, the append is visible to a recovery process following
runner-only SIGKILL. A machine power loss also terminates the registered
processes, so persistence of this process-cleanup registry is not needed to
kill survivors after reboot. An `fsync()` may be chosen as general durability
hardening, but the evidence here does not establish an actionable cleanup bug
or show that the proposed fix changes the reproduced outcome.

## Summary

`record()` makes case evidence durable before returning:

```python
with open(destination, 'a') as stream:
    stream.write(json.dumps(item) + '\n')
    stream.flush()
    os.fsync(stream.fileno())
```

(`qa_common.py:126-129`). The process registry does not:

```python
with (work / 'processes.jsonl').open('a') as registry:
    registry.write(json.dumps({'pid': p.pid, 'start': process_identity(p.pid)}) + '\n')
```

(`release.py:171-172`), and `register_process()` (`qa_common.py:93-97`) uses the same
plain close-only write. The `with` block flushes to the kernel on close, but the process
can be `SIGKILL`ed between `Popen()` returning and that close, and neither path ever
calls `fsync`.

## Impact

- SIGKILL window: if the runner dies after starting a stage or bounded child but before
  the registry line reaches the kernel, the process group is unregistered. Recovery
  (`./run-all.zsh --cleanup <run>`) then has no record of it and cannot terminate it; the
  orphaned stage/PTY/Nix work keeps running.
- Power-loss window: even after close, an un-fsynced append can be lost. The README
  explicitly promises recovery after "SIGKILL, power loss, and machine crashes"
  (`README.md:69-75`), so registry entries need the same durability as the evidence they
  back (`AGENTS.md` treats registry/evidence as recovery inputs).
- Severity is Low because the timing window is narrow and evidence records themselves are
  already fsynced; impact is a leaked process rather than a false approval.

## Root cause

Two append paths for recovery-critical state share the same helper-level omission: no
flush/fsync and no directory sync. Only `record()` implements durability. The registry
line is also written after the child is started (`release.py:168-172`), so the window
cannot be closed by writing earlier — it can only be narrowed by durable writes and
choosing a registration strategy that survives loss.

## Reproduction

```sh
mkdir -p /tmp/opencode/audit-r1/issue-0013 && cd /tmp/opencode/audit-r1/issue-0013
python3 - <<'PY'
import os, signal, subprocess, sys
from pathlib import Path

BASE = Path('/tmp/opencode/audit-r1/issue-0013')
BASE.mkdir(parents=True, exist_ok=True)
CHILD = r'''
import os, signal, sys
path, mode = sys.argv[1], sys.argv[2]
stream = open(path, 'a')
stream.write('{"pid": 12345, "start": 999}\n')
if mode == 'kill-before-close':
    os.kill(os.getpid(), signal.SIGKILL)   # SIGKILL inside the registry window
stream.close()
'''
for mode in ('kill-before-close', 'close-without-fsync'):
    target = BASE / f'{mode}.jsonl'
    if target.exists():
        target.unlink()
    subprocess.run([sys.executable, '-c', CHILD, str(target), mode], check=False)
    print(f'{mode:22s} -> file size = {target.stat().st_size}')
PY
```

### Observed

```text
kill-before-close      -> file size = 0
close-without-fsync    -> file size = 29
```

A `SIGKILL` before the `with`-block close loses the write entirely (size 0). Once closed,
the line reaches the kernel (size 29) but is still not durable against power loss because
no `fsync` is performed.

## Expected behavior

Recovery-critical registry appends are made durable (flush + `fsync`) before the code
continues, and the registry format remains append-only JSONL so a partially written final
line is detectable. The README's recovery promise should hold for entries the runner had
time to write.

## Proposed fix

- Add a shared helper (e.g. `append_jsonl(path, value)`) that opens in append mode, writes,
  flushes, `os.fsync()`s, and optionally syncs the parent directory.
- Use it in `release.py:171-172` and `register_process()` (`qa_common.py:93-97`).
- Consider registering bounded children before awaiting them (already the case) and
  reading the registry with a tolerant parser so a torn final line does not abort cleanup
  (see ISSUE-0012).
- If the cost of fsync per registration is a concern, document the trade-off explicitly;
  do not silently drop durability for recovery state while evidence records keep it.

## Test plan

- Unit test that patches `os.fsync` and asserts `register_process()` calls it once per
  append (mirrors the existing evidence-durability expectation).
- Crash-window test using a helper that kills itself before close and asserts the line is
  either present or cleanly absent (no torn JSON), and that cleanup treats a torn final
  line as an error rather than crashing.
- No `coverage.json` change unless a new named selftest is added for the helper; then add
  its identity in the same change.
