# ISSUE-0026: Picker-select scenarios press Enter after fixed sleeps without confirming the filter; `fkill-signal` can signal the wrong process

- **Status:** Open
- **Severity:** Medium
- **Category:** flaky
- **Affected:** `qa-pty.py:589-591` (`ctrl-t-insert`), `qa-pty.py:666-670` (`zhelp-queue`), `qa-pty.py:709-710` (`fbr-select`), `qa-pty.py:748-749` (`fkill-signal`), `qa-pty.py:923-924` (`npkg-add`)
- **Confidence:** Hypothesis for the race; target-side destructive behavior confirmed by inspection
- **Filed:** 2026-09-12 against harness `60f633a` plus the pending audit working tree
- **Reviewed:** 2026-09-12 against harness `7e97581`; retained as a hypothesis with narrowed priority

## Review disposition

Confirmed as an actionable synchronization hypothesis, not as an observed
failure. The contract explicitly rejects a fixed sleep as readiness proof.
Prioritize `fkill-signal`, whose wrong selection could affect an unrelated user
process. The other named cases have owned-state postcondition checks and mainly
risk a false failure; they should share a verified selection helper but do not
carry the same safety impact.

## Summary

Five picker scenarios wait for the fzf frame header, type a filter string, sleep
a fixed `0.5s`, and then press Enter:

```python
session.wait_for("Processes", timeout=20)
session.send("^" + str(dummy.pid) + "$")
time.sleep(0.5)
session.send(b"\r")
```

(`qa-pty.py:746-749`). `AGENTS.md` states that a fixed sleep alone is not proof
that a prompt or picker is ready. `wait_for("Processes")` proves only that the
header was drawn, not that the query has been applied and the intended row is
focused.

Most of these are false-failure risks: `fbr-select` would check out the wrong
branch, `zi-select` would not change directory, `npkg-*` would mutate the wrong
profile entry. `fkill-signal` is different: the target sends a single SIGTERM
immediately when one PID is selected (confirmation is required only for SIGKILL
or multi-select; `lib/functions-system.zsh:177-198`), so an unapplied `^PID$`
filter could signal whatever process fzf currently has focused. The scenario
starts its own `sleep` dummy, but neither the scenario nor the harness verifies
the focused entry is that dummy before Enter.

## Impact

- Intermittent false failures under load (the Nix/network load the same stage
  creates increases the risk).
- Worst case, a test run signals an unrelated process owned by the operator —
  a side effect outside the owned run, contrary to the harness's cleanup and
  isolation contract. No such incident was observed in recorded runs.

## Root cause

Enter is sent as soon as the frame appears plus a constant delay; there is no
observable check that the filter text has been processed or that the selected
line matches the intended target before the destructive key.

## Reproduction

There is no deterministic reproduction; the evidence is code inspection:

```sh
grep -n -A4 'wait_for("Processes"' qa-pty.py
grep -n -B2 -A6 'time.sleep(0.5)' qa-pty.py
# Target single-selection behavior:
grep -n 'SIGTERM\|SIGKILL\|confirm' ~/.config/zsh/lib/functions-system.zsh | sed -n '1,20p'
```

### Observed

The filter bytes and Enter are written sequentially; nothing between them
proves the query was applied. The target code path for a single selected PID
sends SIGTERM without a confirmation prompt.

## Expected behavior

Before a destructive Enter, the scenario waits for evidence that the intended
entry is focused/selected — for example, wait for the dummy PID (or branch,
package, queued command) text to appear as the current highlight, and assert it
is still the intended row immediately before sending `\r`. A fixed delay may
remain as a settle aid but must not be the readiness proof.

## Proposed fix

- Replace `wait_for(header) + sleep + filter + sleep + Enter` with a helper that
  waits for the expected match line to render after the filter (e.g.
  `wait_for(f"...{pid}...")` on the frame) and, where fzf exposes it, uses
  `--select-1`/`--exit-0` semantics or a query the scenario can verify.
- For `fkill-signal`, re-read the frame and assert the focused line contains the
  dummy PID before Enter; prefer a target path with explicit confirmation if
  one exists.
- Document the remaining settle delay, if any, as a delay rather than proof.

## Test plan

- A fault-injection test that delays the picker's filter rendering (or a fake
  fzf) and asserts the scenario does not press Enter until the expected match
  appears; add its identity to `coverage.json` under `selftest` if it becomes a
  named harness test.
- Re-run `./run-all.zsh pty` (both repetitions) after the change.
