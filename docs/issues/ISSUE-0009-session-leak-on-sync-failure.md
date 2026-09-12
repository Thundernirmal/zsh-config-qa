# ISSUE-0009: Scenarios constructing `Session` before `try/finally` leak the PTY/zsh when `sync()` fails

- **Status:** Fixed
- **Severity:** Medium
- **Category:** resource-lifecycle
- **Affected:** `qa-pty.py:414-415`, `443-444`, `464-465`, `480-481`, `496-497`, `684-685`, `702-703`, `778-779`, `899-900`, `915-916`; correct pattern at `qa-pty.py:383-391` (`fresh_zsh`)
- **Confidence:** Confirmed by execution (hanging startup HOME) and inspection
- **Filed:** 2026-09-12 against harness `60f633a` plus the pending audit working tree
- **Reviewed:** 2026-09-12 against harness `7e97581`; confirmed actionable as filed

## Review disposition

Confirmed. Several call sites still acquire a live `Session` and perform a
fallible `sync()` before entering their cleanup scope. Outer stage cleanup is a
backstop, but it does not prevent leaked sessions from contaminating the
remainder of the PTY sweep; Medium remains reasonable.

## Summary

Ten scenarios create a `Session` and call `session.sync(timeout=45)` **before** entering `try/finally`:

```python
session = Session(SCRATCH, extra_env=extra_env)      # probe_shell, qa-pty.py:414
session.sync(timeout=45)                              # qa-pty.py:415
try:
    ...
finally:
    session.close()
```

If `sync()` raises — zsh startup failure, a startup `.zshrc` that blocks/errors, IO trouble, an unresponsive shell — the exception propagates out of the scenario without `session.close()` ever being reached. `run()` catches the exception and records a failure (`qa-pty.py:373-380`), then the sweep continues to the next scenario while the PTY master fd and the zsh process remain alive until stage-level cleanup (the runner's descendant/registry sweep in `release.py:113-158` after the stage finishes or the outer stage timeout fires). The affected sites are listed in Affected above; `fresh_zsh()` (`383-391`) and `cgm_roundtrip` (`812-815`) already use the safe `try/except` or `try` covering `sync()`, so this is an inconsistency, not a design limitation.

## Impact

- A failing startup turns one scenario failure into a leaked interactive shell plus an open PTY fd for the remainder of the stage. With `--repeat 2` and 28 scenarios, a systematically broken HOME can leak many shells and fds.
- The leaked `zsh` holds the scenario's HOME (shared `QAHOME`/cache for the fzf and Nix scenarios) and the pty; later scenarios that inspect children or cache state can become flaky or report misleading failures, and the report's cleanup section blames stage cleanup rather than the scenario.
- Not observed in recorded runs: every recorded PTY pass closed normally. The bug is the failure/interrupt path.

## Root cause

`session.close()` is only in `finally` after `sync()`. Between construction and `try:` there is no owner for the resource. `Session` does implement `__enter__`/`__exit__` (`qa-pty.py:337-341`), which suggests the intended pattern but none of these call sites use it.

## Reproduction

With a HOME whose `.zshrc` never returns to a prompt (`issue9-pty-repro.py`, patched `qa_pty.Session` in an owned `/tmp` work dir):

```sh
$ python3 issue9-pty-repro.py
sync() raised: timed out waiting for b'QA-SYNC-…-1'; output tail:
after failed sync: PID 219825 poll=None /proc-alive=True
scenario has no reference that closes it; the PTY/zsh survives until stage cleanup
after explicit close(): poll=-9 /proc-alive=False
rc=0
```

The child zsh was still running (`poll=None`, `/proc/<pid>` present) after the exception, and only `session.close()` terminated it. A real scenario has no `session` variable in scope after the exception propagates to `run()`.

## Expected behavior

Every `Session` created by a scenario must be closed on all paths, including a failure inside or before `sync()`. `run()` must find no leaked PTY/zsh child from a failed scenario.

## Proposed fix

Move `sync()` inside the existing `try` (or wrap the construction+sync in `try`/`except` with `session.close()` in the handler):

```python
session = Session(SCRATCH, extra_env=extra_env)
try:
    session.sync(timeout=45)
    session.check(...)
finally:
    session.close()
```

Because `Session` already supports the context-manager protocol, the most robust change is `with Session(...) as session:` plus `session.sync(...)` inside the `with` body — then even a failed `sync()` triggers `__exit__` → `close()`. Do this for all ten sites; `fresh_zsh()` stays as-is.

## Test plan

- Fault-injection test: point one scenario's HOME at a `.zshrc` that sleeps (as in the reproduction), call the scenario through `run()`, and assert (a) the scenario is recorded as `fail` and (b) the driver has no remaining interactive-zsh child after `run()` returns (check `/proc` descendants or the registered PID registry is empty).
- Re-run `./run-all.zsh pty` twice and confirm pass counts and `wait_children_empty` behavior are unchanged.

## Fix (2026-09-12, batch 4)

- **Status change:** Open → Fixed.
- Every scenario that opens a `Session` now closes it when startup fails:
  `sync()` moved inside `try/finally` at `probe_shell`, `nounset_startup`,
  `fzf_cold_start`, `fzf_warm_start`, `fzf_blocked`, `fbr_picker`,
  `fbr_select`, `zi_select`, `npkg_remove_picker`, and `npkg_add_picker`;
  `cgm_roundtrip` creates the session inside the try with a `None` guard so
  the credential cleanup still runs when `Session()` itself raises
  (`fresh_zsh` already closed on failure).
- Fault-injection test: `test_scenario_session_is_closed_when_startup_fails`
  (fake `Session` whose `sync()` raises; asserts `nounset_startup` and
  `env_no_color` call `close()`; fails on revert).
- Verified: full gate `20260912-213912-57c5679f` = `YES`, exit 0. Review note:
  fkill-signal's one pre-try `check()` was also moved inside its try in this
  batch.
