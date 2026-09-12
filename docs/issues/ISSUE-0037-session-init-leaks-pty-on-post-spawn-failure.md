# ISSUE-0037: `Session.__init__` leaks the spawned zsh/PTY when post-spawn setup fails

- **Status:** Fixed
- **Severity:** Low
- **Category:** cleanup / resource-lifecycle
- **Affected:** `qa-pty.py:100-125` (spawn at `:109-118`, `register_process` at `:119`, `os.close(slave)` at `:120`)
- **Confidence:** Confirmed by execution (injection) and inspection
- **Filed:** 2026-09-12 against harness revision `7aafe90` (third independent review pass)

## Summary

`Session.__init__` spawns an interactive `zsh` in a new session and then does
further setup (`register_process(self.proc.pid)`, `os.close(slave)`) with no
exception handling. If either step raises, the constructor propagates the
error without returning a `Session`, so no caller can ever call `close()`: the
child zsh stays alive and the PTY descriptors leak.

## Impact

A registry-append failure (`processes.jsonl` unwritable or on a full disk,
I/O error) leaves an interactive zsh running in its own session plus open PTY
descriptors. During a `pty` stage the child persists until the qa-pty process
exits; when scenarios run directly through `load_pty()`/unit tests there is no
runner cleanup at all. This is the same failure-before-`sync()` class as
ISSUE-0009, but earlier: the object that owns the child never exists.

## Root cause

No `try`/`except` or `finally` around the lines between `subprocess.Popen`
(`qa-pty.py:109-118`) and the end of `__init__`; `register_process` opens and
writes a file with no error path, and the child is only reachable through
`self.proc`, which is not yet owned by a returned object.

## Reproduction (executed 2026-09-12, sandbox `/tmp/opencode/audit3/leak.py`)

The module is loaded as `tests/test_gate.py` does (patched `WORK`/`REPO`), then
`qa_pty.register_process` is replaced with a function that raises `OSError`:

```
init raised: injected register_process failure
leaked zsh children: [['157393', '157392', '157393', 'zsh']]   # pid, ppid, pgid, cmd
```

The child was alive in its own process group after `__init__` raised; the
reviewer had to kill `pgid 157393` manually. A second review reproduced the
same leak by patching `register_process`.

## Expected behavior

Any failure after the spawn tears down the child and its file descriptors
before propagating: `killpg(self.proc.pid, SIGKILL)` (guarded), close
`self.master`, and close `slave` if it is still open. Callers can also wrap
construction as they do for `sync()` (ISSUE-0009), but the constructor should
not leak when it cannot return an object.

## Proposed fix

- Wrap the post-spawn setup in `try`/`except BaseException` that performs the
  teardown above and re-raises.
- Initialize `self.closed`/`self.proc` before the spawn so the cleanup can be
  shared with `close()`.
- Alternatively register the process before the first post-spawn statement and
  wrap `os.close(slave)` separately.

## Test plan

- Fault-injection test: patch `register_process` to raise; assert `Session(...)`
  raises and that no descendant zsh process remains (`/proc` descendant scan or
  the registered-PID registry) and no master fd stays open.
- Add the identity to `coverage.json` under `selftest` in the same change.

## Fix (2026-09-12, batch 8)

- **Status change:** Open → Fixed.
- `Session.__init__` wraps the spawn, registration, and slave close in
  `try/except BaseException`: on any post-spawn failure it kills the child's
  process group (guarded), waits it (bounded), closes the master and slave
  descriptors, marks the session closed, and re-raises — so the constructor
  never leaks a live zsh or PTY descriptors. A `Popen` failure itself is
  covered too (master/slave closed).
- Fault-injection test: `test_session_init_tears_down_on_post_spawn_failure`
  (patches `register_process` to raise; asserts the spawned pid is gone;
  verified to fail on revert, where the child survives).
- Verified: full gate `20260912-235724-019e9c8b` (`YES`, exit 0).
