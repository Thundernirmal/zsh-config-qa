# ISSUE-0008: Synthetic credential value is retained in `pty-command-*.zsh` and bypasses redaction in failure diagnostics

- **Status:** Open
- **Severity:** High
- **Category:** secret-hygiene
- **Affected:** `qa-pty.py:283-284` (script write), `qa-pty.py:828` (value in check code), `qa-pty.py:146-191` (`wait_for`/`wait_for_since` tails), `qa-pty.py:373-380` (`run()` detail), redaction only at `qa-pty.py:313-317`; echo-off check at `qa-pty.py:819-824`
- **Confidence:** Confirmed by inspection (retained artifact plus inert placeholder experiment)
- **Filed:** 2026-09-12 against harness `60f633a` plus the pending audit working tree

## Summary

The synthetic credential value generated in `cgm_roundtrip` (`secret = 'qa-' + uuid…`, `qa-pty.py:806`) is interpolated into the check body

```python
session.check(f'cgm env {name} && [[ ${name} == {shlex.quote(secret)} ]]')   # qa-pty.py:828
```

`shlex.quote()` leaves `qa-<hex>` unquoted (no shell metacharacters), so the plaintext value is written into `WORK/pty-command-<token>.zsh` by `Session.check()` (`qa-pty.py:283-284`). `WORK` is the retained run directory (`.runs/<timestamp>-<id>/`), and run directories are intentionally kept for debugging, so the value persists on disk after a successful run.

Separately, the only redaction point is `Session.text()`:

```python
for value in self.redactions:                              # qa-pty.py:315
    text = text.replace(value, '<redacted synthetic credential>')
```

`wait_for()` / `wait_for_since()` build their timeout/exit diagnostics directly from `decode(strip_terminal_controls(self.output))` (`qa-pty.py:156-159`, `179-182`, `188-191`) without applying `self.redactions`, and `run()` stores `str(error)` as the recorded detail (`qa-pty.py:375-377`). If a failure occurs after the value has reached the PTY buffer (or after an `error` string built from a tail), the value can be copied into `pty-*.jsonl`, stage logs, or `report.*` unredacted.

## Impact

- The value is readable in plaintext in every retained run directory that exercises `cgm`/`cgm-no-color` (two copies per PTY repetition, two repetitions by default). Anyone with read access to `.runs/` (README says reports and logs may contain local paths/environment diagnostics and must not be published) can recover it. For a synthetic test credential the immediate harm is limited, but it normalizes a leak pattern that would be serious if the same template were reused with a real value, and it violates the harness's own "Never log values" contract (AGENTS.md credential rules).
- Failure diagnostics that bypass `redactions` are the secondary leak path; the echo-off verification is not a redaction mechanism.

## Evidence

Code inspection paths above. Additionally, a completed recorded run contains the value in a retained script (redacted here):

```sh
$ f=.runs/20260912-193357-431ba0a8/pty-command-47fb08afbd074156b67f663b37a25579.zsh
$ awk '/qa-/{print "match on line " NR; exit}' "$f"
match on line 2
$ sed -E "s/qa-[0-9a-f]{32}/<SYNTHETIC-SECRET>/g; s/QA_[A-Z0-9_]+/<CRED-NAME>/g" "$f"
setopt LOCAL_OPTIONS PIPE_FAIL
cgm env <CRED-NAME> && [[ $<CRED-NAME> == <SYNTHETIC-SECRET> ]]
```

(The raw file contains the 32-hex `qa-…` value verbatim; it is intentionally redacted in this document.)

No leak was observed through terminal echo: `cgm_roundtrip` waits for `Password:` and then for `ECHO` to be disabled before sending the value (`qa-pty.py:819-824`), and the recorded `pty-*.log` / `pty-*.jsonl` files contain zero occurrences of `qa-<32hex>`. The disk artifact and the unredacted diagnostic path remain.

Placeholder experiment (no credential value involved), confirming both properties mechanically:

```sh
$ python3 issue8-placeholder-repro.py
retained script pty-command-….zsh contains placeholder: True
wait_for() diagnostic contains placeholder: True
Session.text() is redacted: True
rc=0
```

The script appends an inert string to `session.redactions` and to the output buffer, then shows the `wait_for()` exception text contains it while `Session.text()` replaces it.

## Expected behavior

- The synthetic value must never be written into run artifacts, including `pty-command-*.zsh` and recorded diagnostics. Only non-reversible verification (e.g. a hash) needs to be retained.
- Every user-visible diagnostic (timeout tails, exception details, recorded `detail`) must pass through the same redaction as `Session.text()`.

## Proposed fix

1. Do not embed the plaintext in the check body. Export/derive the value inside the shell and compare a hash, e.g. write the check as
   `cgm env <name> && [[ "$(print -rn -- ${(P)name} | sha256sum)" == <hash> ]]`
   where `<hash>` is the SHA-256 of `secret` computed in Python (non-reversible; safe to retain). Keep `session.redactions.append(secret)` for any echo that still reaches the buffer.
2. Centralize redaction: add `Session.redact(value: str) -> str` (the loop currently in `text()`) and apply it in the two `wait_for*` diagnostics; have `run()` record a redacted `str(error)` (wait_for errors are the only ones that embed output).
3. Optionally scrub the `pty-command-*.zsh` file after the check body has run (or store it under a mode-0600 name and document that credentials must not appear there).

## Test plan

- Fault-injection test in `tests/test_gate.py`: run a patched `cgm`-style check with an inert placeholder, then assert the placeholder appears in `session.redactions` but in **no** file under `WORK` and in no recorded `detail`.
- Assert a forced `wait_for()` timeout with a registered redaction returns the placeholder only in redacted form.
- Re-run `./run-all.zsh pty` and confirm `cgm`/`cgm-no-color` still pass and `secret-tool lookup` proves deletion; then grep the retained run for the value namespace and confirm zero hits outside the registry.
