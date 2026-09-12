# ISSUE-0021: atomic_json renames without fsync, so report durability does not match the README claim

- **Status:** Open
- **Severity:** Low
- **Category:** durability
- **Affected:** `qa_common.py:20-23`, used for `report.json` at `release.py:280`, `release.py:302`, `release.py:334`, and `latest.json` at `release.py:344`
- **Confidence:** Confirmed by inspection
- **Filed:** 2026-09-12 against harness `60f633a` plus the pending audit working tree
- **Reviewed:** 2026-09-12 against harness `7e97581`; confirmed as Low durability hardening

## Review disposition

Confirmed. Unlike ISSUE-0013, syncing here can materially close the crash
window after `atomic_json()` returns: file data and the rename can be made
durable before the runner proceeds. The risk is limited to system failure and
does not create a normal-path false approval, so Low remains correct.

## Summary

`atomic_json()` writes a temporary file and renames it over the destination:

```python
def atomic_json(path: Path, value) -> None:
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(value, indent=2) + '\n')
    tmp.replace(path)
```

(`qa_common.py:20-23`). The rename is atomic with respect to concurrent readers, but
neither the temporary file's data nor the containing directory entry is `fsync`ed. On
power loss / kernel crash, the new directory entry can be committed before the data
blocks, leaving an empty, partial, or zero-length file where the caller expected the
written document.

## Impact

- `report.json` is initialized with `verdict: INCOMPLETE` before stages run (`release.py:280`).
  README:34 promises "A half-written/interrupted run starts with an `INCOMPLETE` report,
  never a stale success." Without an fsync, a crash immediately after that write can leave
  `report.json` missing/empty rather than `INCOMPLETE`; the promise is stronger than the
  implementation.
- `latest.json` (`release.py:344`) is written the same way. A power loss after the last
  report update but before `latest.json`'s data is durable can leave the previous run's
  pointer in place, so `.runs/latest.json` and the latest `report.json` disagree about
  what the last completed run was. (The verdict is not read from `latest.json`, so this is
  a pointer-integrity issue, not a direct false `YES`.)
- Under ext4 `auto_da_alloc`, replacing an existing file usually forces data writeback, but
  this is filesystem-specific, not a guarantee. `AGENTS.md` treats report integrity as an
  architectural property; the code relies on rename alone.
- Severity is Low: the failure requires power loss/kernel crash, and the verdict is
  fail-closed under any normal interruption path (signals still run the `finally` block).

## Root cause

`Path.write_text` buffers in user space and closes without `os.fsync`; `Path.replace` does
the rename without syncing the file data or the parent directory. Only `record()` in
`qa_common.py:126-129` performs `flush` + `fsync`; the shared report writer does not.

## Reproduction

```sh
mkdir -p /tmp/opencode/audit-r1/issue-0021 && cd /tmp/opencode/audit-r1/issue-0021
python3 - <<'PY'
import json, os, sys, threading
from pathlib import Path
HARNESS = '/home/nirmal/projects/zsh-config-qa'
sys.path.insert(0, HARNESS)
import qa_common as common

BASE = Path('/tmp/opencode/audit-r1/issue-0021')
BASE.mkdir(parents=True, exist_ok=True)
TARGET = BASE / 'report.json'

# Direct observation: atomic_json issues no fsync at all.
calls = []
real_fsync = os.fsync
os.fsync = lambda fd: calls.append(fd)
try:
    common.atomic_json(TARGET, {'verdict': 'INCOMPLETE'})
finally:
    os.fsync = real_fsync
print('fsync calls during atomic_json:', len(calls))

# Concurrent readers never observe a torn document (rename is atomic).
stop = False
errors = []
def reader():
    while not stop:
        try:
            json.loads(TARGET.read_text())
        except Exception as error:
            errors.append(repr(error))
thread = threading.Thread(target=reader)
thread.start()
for i in range(1000):
    common.atomic_json(TARGET, {'verdict': 'INCOMPLETE', 'i': i, 'blob': 'x' * 65536})
stop = True
thread.join()
print('reader JSON parse errors during 1000 replacements:', len(errors), errors[:3])
print('leftover temp files:', sorted(p.name for p in BASE.iterdir() if p.name.endswith('.tmp')))
PY
```

### Observed

```text
fsync calls during atomic_json: 0
reader JSON parse errors during 1000 replacements: 0 []
leftover temp files: []
```

This confirms the positive half (rename atomicity: no torn reads, no leftover temp files)
and the negative half (no `fsync` was ever requested). Durability cannot be observed
without a crash; the power-loss window follows directly from the missing syscalls and is
labeled inspection.

## Expected behavior

`atomic_json` should make the new content durable before returning: fsync the temporary
file, then `os.replace`, then fsync the parent directory so the rename itself is durable.
After that, the README's interrupted-run guarantee ("starts with an INCOMPLETE report")
holds even across power loss, and `latest.json` never trails a durable completion.

## Proposed fix

```python
def atomic_json(path: Path, value) -> None:
    tmp = path.with_name(path.name + '.tmp')
    data = json.dumps(value, indent=2) + '\n'
    with open(tmp, 'w') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)
    dir_fd = os.open(path.parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
```

Keep the `.tmp` suffix so leftover temp files remain recognizable, and consider
`os.chmod(tmp, 0o600)` for report files (see ISSUE-0022).

## Test plan

- Unit test patching `os.fsync` to assert at least two calls (file and directory) per
  `atomic_json`.
- Keep a concurrency test asserting no reader sees invalid JSON and no `.tmp` remains.
- The existing atomic-write callers need no interface change; verify `report.json` and
  `latest.json` still load with `json.loads` after the change.
