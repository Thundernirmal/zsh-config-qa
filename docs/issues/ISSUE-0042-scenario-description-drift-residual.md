# ISSUE-0042: Remaining scenario descriptions overclaim what the bodies assert

- **Status:** Fixed
- **Severity:** Low
- **Category:** contract-drift
- **Affected:** `qa-pty.py:982` (`nounset-startup` description) with body `qa-pty.py:485-487`; `qa-pty.py:985` (`fzf-blocked` description) with body `qa-pty.py:538-540`
- **Confidence:** Confirmed by inspection
- **Filed:** 2026-09-12 against harness revision `7aafe90` (third independent review pass)

## Summary

ISSUE-0019 corrected the two descriptions that were judged to overclaim
(`fkill`, `ctrl-t`, layouts). A full comparison of all 28
`SCENARIOS` descriptions against their bodies finds two remaining drifts:

- `nounset-startup`: "NO_UNSET startup keeps zoxide **and other
  integrations**" (`qa-pty.py:982`) while the body asserts only
  `z=1` and `zi=1` (`qa-pty.py:485-487`).
- `fzf-blocked`: "fzf below 0.68 **blocks pickers** only" (`qa-pty.py:985`)
  while the body probes only Ctrl+T (`\x14`, `qa-pty.py:538-540`); Ctrl+R and
  Alt+C are not checked.

## Impact

`python3 qa-pty.py --list` and the scenario inventory are read as the
delivered coverage contract; a reader overestimates what a passing matrix
proves. No verdict is weakened by the descriptions themselves.

## Root cause

Descriptions were written for the intent of the scenario; the body assertions
were narrowed during review, but the text was not updated in the same change.

## Reproduction (inspection)

```
$ python3 qa-pty.py --list | grep -E 'nounset-startup|fzf-blocked'
nounset-startup    NO_UNSET startup keeps zoxide and other integrations
fzf-blocked        fzf below 0.68 blocks pickers only
$ sed -n '485,487p;538,540p' qa-pty.py
assert "parameter not set" not in session.text(), ...
state = PROBE_FILE.read_text().strip()
assert "z=1" in state and "zi=1" in state, ...
...
session.send(b"\x14")
time.sleep(1.0)
assert not session._children(), "Ctrl+T opened a picker despite blocked fzf"
```

## Expected behavior

Each description states exactly what the body asserts, or the body is widened
so the description is true.

## Proposed fix

- Either narrow the descriptions ("NO_UNSET startup keeps zoxide available",
  "fzf below 0.68 blocks Ctrl+T only") or extend the bodies (assert additional
  integration functions and check Ctrl+R/Alt+C do not spawn an fzf child).
- No case names change, so `coverage.json` is untouched.

## Test plan

- Re-run `python3 qa-pty.py --list` and diff the descriptions against the
  assertions by inspection.
- If assertions are widened, run both PTY repetitions and confirm all 28
  scenarios still pass.

## Fix (2026-09-12, batch 8)

- **Status change:** Open → Fixed.
- Chose to widen the bodies so the descriptions become true (rather than
  narrowing them):
  - `nounset-startup` now also asserts that `fbr`, `cgm`, `npkg`, and `upkg`
    are defined after a `NO_UNSET` startup. `cgm`/`npkg` are required only
    when their tools (`secret-tool`/`nix`) are present in the probe state, so
    tool-less hosts follow the skip/incomplete policy instead of a false NO
    (review finding, applied).
  - `fzf-blocked` now probes Ctrl+T, Ctrl+R, and Alt+C and asserts each spawns
    no fzf child over an observation window (the per-keypress diagnostic is
    not guaranteed because the blocked state is cached per fzf path, so the
    proof is the bounded absence window plus the startup diagnostic).
- Descriptions updated where the semantics changed; no case names changed.
- Verified: both PTY repetitions pass all 28 scenarios; full gate `20260912-235724-019e9c8b` (`YES`, exit 0).
