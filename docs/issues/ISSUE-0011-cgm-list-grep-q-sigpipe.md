# ISSUE-0011: `cgm list | command grep -q <name>` is an early-exiting-consumer SIGPIPE hazard under `PIPE_FAIL`

- **Status:** Fixed
- **Severity:** Low
- **Category:** flaky
- **Affected:** `qa-pty.py:827` (safe counterpart at `qa-pty.py:835`)
- **Confidence:** Confirmed by execution (mechanism); live case latent, not observed
- **Filed:** 2026-09-12 against harness `60f633a` plus the pending audit working tree
- **Reviewed:** 2026-09-12 against harness `7e97581`; confirmed as a latent Low issue

## Review disposition

Confirmed. The current isolated catalogue normally keeps output small, so this
is unlikely to fire today, but the pipeline still violates the harness's
producer-completion rule and has a straightforward capture-then-inspect fix.
Low severity correctly reflects that the live failure has not been observed.

## Summary

Inside the credential round trip, the presence check is a pipeline:

```python
session.check('cgm list | command grep -q ' + shlex.quote(name))          # qa-pty.py:827
```

Every `session.check()` body runs with `setopt PIPE_FAIL` (`qa-pty.py:284`, and also `qa_common.py:151`). If `grep -q` finds a match before the producer has finished writing, `grep` exits and `cgm` receives `SIGPIPE`; with `PIPE_FAIL` the pipeline status becomes 141, so the check fails even though the credential is listed. The absence check six lines later already uses the safe shape:

```python
session.check('cgm list > "$HOME/cgm-list" && ! command grep -q ' + name + ' "$HOME/cgm-list"')   # qa-pty.py:835
```

This is the exact pattern AGENTS.md warns about ("avoid `grep -q`/`head` as early-exiting consumers that can create incidental SIGPIPE; consume the output or capture it before inspecting it").

## Impact

A latent, output-size-dependent false failure of the `cgm`/`cgm-no-color` scenarios. Today `cgm list` prints one line per stored credential from an isolated HOME (typically one), which fits in the 64 KiB pipe buffer, so the producer finishes before `grep` exits and no failure has been observed. If the isolated catalogue grows or `cgm list` output changes, the scenario can fail intermittently with a misleading "command exited 141" and produce a `NO`/`INCOMPLETE` verdict for a healthy target. It also forces a stage re-run to diagnose.

## Mechanism demonstration

`yes` is killed by SIGPIPE as soon as `grep -q` exits; under `pipefail` the pipeline status reflects the producer:

```sh
$ zsh -f -c 'setopt pipefail; yes | command grep -q y; print -r -- "yes | grep -q rc=$?"'
yes | grep -q rc=141
$ zsh -f -c 'setopt pipefail; seq 1 400000 | command grep -q 1; print -r -- "seq 1 400000 | grep -q rc=$?"'
seq 1 400000 | grep -q rc=141
$ zsh -f -c 'setopt pipefail; seq 1 400000 > /tmp/opencode/audit-r2/issue11.out; command grep -q 1 /tmp/opencode/audit-r2/issue11.out; print -r -- "capture then grep -q rc=$?"'
capture then grep -q rc=0
```

The third command is the safe capture-then-inspect form used at `qa-pty.py:835`; it is unaffected by the producer being killed because there is no concurrent pipeline stage.

## Expected behavior

The presence check must not depend on whether `grep -q` exhausts the producer: capture `cgm list` to a file first, then inspect the file. A SIGPIPE from an early-exiting consumer must never turn a successful listing into a failed check.

## Proposed fix

Mirror the absence check at `qa-pty.py:835`:

```python
session.check('cgm list > "$HOME/cgm-list" && command grep -q ' + shlex.quote(name) + ' "$HOME/cgm-list"')
```

(No `!`; this asserts presence.) If a one-liner output check is desired, `cgm list` could instead be captured into a variable with `$(...)`, but the file form matches the existing sibling check and keeps evidence on disk.

## Test plan

- Re-run `./run-all.zsh pty` and confirm `cgm` and `cgm-no-color` still pass with the rewritten presence check.
- Optional harness self-test: a child shell under `PIPE_FAIL` runs `big-producer | grep -q` (expect 141) and the capture-then-grep form (expect 0); asserts the safe pattern is what the scenario uses. A static test can fail if `cgm list |` reappears in `qa-pty.py`.

## Fix (2026-09-12, batch 4)

- **Status change:** Open → Fixed.
- The presence check now uses the same redirect-to-file form as the absence
  check: `cgm list > "$HOME/cgm-list" && command grep -q <name>
  "$HOME/cgm-list"` — the early-exiting `grep -q` consumer no longer sits in a
  pipeline where the producer can die of SIGPIPE under `PIPE_FAIL` (the
  AGENTS.md:123 hazard). The file lands in the owned isolated HOME.
- Verified: full gate `20260912-213912-57c5679f` = `YES`, exit 0; both cgm
  scenarios pass in both PTY repetitions.
