# ISSUE-0029: SIGHUP/SIGQUIT are not trapped, so a terminal hangup skips cleanup

- **Status:** Fixed
- **Severity:** Low
- **Category:** cleanup
- **Affected:** `release.py:281-284` (handlers installed only for `SIGTERM`/`SIGINT`), `release.py:316-317` (only those two are ignored during the final phase), `README.md:63`
- **Confidence:** Confirmed by inspection; signal disposition confirmed by execution
- **Filed:** 2026-09-12 against harness `60f633a` plus the pending audit working tree
- **Reviewed:** 2026-09-12 against harness `7e97581`; confirmed actionable as filed

## Review disposition

Confirmed. The runner still handles only SIGINT and SIGTERM, while its detached
stage/process groups can survive a terminal hangup after the runner receives
default SIGHUP termination. Extending the same interruption/finalization path
to SIGHUP and SIGQUIT is proportionate Low-severity cleanup hardening.

## Summary

The runner installs `KeyboardInterrupt`-raising handlers for `SIGTERM` and
`SIGINT` only. Python's default disposition for `SIGHUP` and `SIGQUIT` is
terminate, so closing the terminal or losing an SSH session kills the runner
without executing the `finally` block that performs cleanup and writes the
final report. Stage processes are started with `start_new_session=True`
(`release.py:169`) and PTY shells with `setsid()` (`qa-pty.py:102-106`), so they
do not receive the hangup and keep running as orphans.

`README.md:63` claims "Timeouts and ordinary termination signals trigger
cleanup." SIGHUP is the ordinary termination signal for a terminal hangup, so
the documented behavior is stronger than the implementation.

## Impact

- A closed terminal mid-run leaves stage/PTY/Nix children running; cleanup is
  deferred to the manual `./run-all.zsh --cleanup <run>` recovery path (which
  has its own gap, ISSUE-0006).
- No false approval: the run produces no final verdict and no `YES`.
- `SIGQUIT` (Ctrl-\) has the same effect and is even easier to trigger
  accidentally.

## Root cause

The signal-handler set was written for interactive Ctrl-C and `kill`/`timeout`
(audit's outer `--stage-timeout` uses SIGKILL on the stage group only), not for
session hangup. `signal.signal` accepts `SIGHUP`/`SIGQUIT` the same way as the
two handled signals.

## Reproduction

```sh
python3 -c 'import signal; print(signal.getsignal(signal.SIGHUP), signal.getsignal(signal.SIGQUIT))'
grep -n 'signal.signal' /home/nirmal/projects/zsh-config-qa/release.py
```

### Observed

```text
0 0
281:    signal.signal(signal.SIGTERM, stop)
282:    signal.signal(signal.SIGINT, stop)
```

`0` is `SIG_DFL`: no handler is installed for either signal. An end-to-end
hangup was not exercised (the audit rules forbid starting a gate run), but the
disposition and missing handlers are sufficient to establish that default
termination occurs without the `finally` block.

## Expected behavior

`SIGHUP` (and preferably `SIGQUIT`) follow the same path as `SIGTERM`: raise
`KeyboardInterrupt`, record the `interrupted` stage, run cleanup, write the
`INCOMPLETE` report, and return exit 2. `SIGKILL` and power loss remain covered
by the documented `--cleanup` recovery path.

## Proposed fix

```python
for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGQUIT):
    signal.signal(sig, stop)
...
for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGQUIT):
    signal.signal(sig, signal.SIG_IGN)
```

Update `README.md:63` (and `AGENTS.md` if it enumerates signals) to match the
actual handled set, and keep `--cleanup` documented as the SIGKILL/power-loss
fallback.

## Test plan

- Fault-injection test: start a run-like process that has a registered child,
  send `SIGHUP`, and assert the cleanup path ran (child gone, `INCOMPLETE`
  report written, exit 2). Reuse the `SIGTERM` pattern if one exists.
- Add the new self-test identity to `coverage.json` in the same change.
- Re-run the full gate after the change.

## Fix (2026-09-12, batch 2)

- **Status change:** Open → Fixed.
- `release.INTERRUPT_SIGNALS = (SIGTERM, SIGINT, SIGHUP, SIGQUIT)`; all route
  through `interrupt()` (KeyboardInterrupt), so a terminal hangup now runs the
  cleanup/report path and returns `INCOMPLETE` exit 2. `ignore_interrupt_signals()`
  replaces the two previous SIG_IGN calls during the final phase. `SIGKILL`
  and power loss remain covered by `--cleanup`.
- Fault-injection test: `test_sighup_and_sigquit_route_to_interrupt`
  (asserts SIGHUP/SIGQUIT are in the tuple, that the installed handler raises
  `KeyboardInterrupt`, and that the ignore phase takes effect; handlers saved
  and restored via `addCleanup`).
- README's "ordinary termination signals trigger cleanup" claim is now true;
  no documentation drift.
- Verified: full gate `20260912-205801-76cafce0` = `YES`, exit 0.
