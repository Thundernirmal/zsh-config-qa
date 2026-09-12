# ISSUE-0010: `zhelp-queue` assertion `words[:2]` also accepts the usage template

- **Status:** Fixed
- **Severity:** Low
- **Category:** evidence-integrity
- **Affected:** `qa-pty.py:674`; target registry `lib/command-registry.zsh:106`
- **Confidence:** Confirmed by inspection (recorded ZLE-buffer evidence and shlex behavior)
- **Filed:** 2026-09-12 against harness `60f633a` plus the pending audit working tree
- **Reviewed:** 2026-09-12 against harness `7e97581`; confirmed actionable as filed

## Review disposition

Confirmed. Prefix-only token comparison accepts both the example and the
longer usage template. Exact buffer equality is the correct observable
contract, and Low severity matches this narrow evidence gap.

## Summary

The scenario description is "zhelp queues the selected example" (`qa-pty.py:950`), but the assertion only checks the first two tokens:

```python
words = shlex.split(session.capture_buffer())
assert words[:2] == ['upkg', 'plan'], f'wrong queued buffer: {words}'   # qa-pty.py:674
```

The target registers both a usage string and an example for the same command:

```
_zsh_help_register upkg-plan Packages 'Preview upgrades' 'upkg plan [--only <list>]' 'upkg plan' 'a supported package manager' action package-manager
```

(target `lib/command-registry.zsh:106`). `shlex.split('upkg plan [--only <list>]')[:2]` is exactly `['upkg', 'plan']`, so if zhelp queued the usage template instead of the example, the assertion would still pass — and the queued buffer would then be a command line containing `<list>` rather than the intended runnable example.

## Impact

The scenario cannot distinguish "queued the example" from "queued the usage template." A regression in zhelp's queue/insert logic that selects the wrong field would still report `zhelp-queue` as passed and `coverage.json` would still claim the behavior is verified.

## Evidence

Recorded ZLE-buffer probes from completed runs contain exactly the example (`upkg plan`):

```sh
$ cat .runs/20260912-191931-60e3a39e/zle-buffer-122fa1ab41e541b49ac5a2471fb8d74f
upkg plan
$ wc -c .runs/20260912-193357-431ba0a8/zle-buffer-dff6bb3a5e534c22bb1ebdf913816978
9 .runs/20260912-193357-431ba0a8/zle-buffer-dff6bb3a5e534c22bb1ebdf913816978
```

(All four retained queues — two repetitions in each of two runs — are 9 bytes, `upkg plan`.) The assertion accepts both forms:

```sh
$ python3 - <<'EOF'
import shlex
template = shlex.split('upkg plan [--only <list>]')
example  = shlex.split('upkg plan')
print('template ->', template)
print('example  ->', example)
print('template[:2] == example[:2]:', template[:2] == example[:2])
EOF
template -> ['upkg', 'plan', '[--only', '<list>]']
example  -> ['upkg', 'plan']
template[:2] == example[:2]: True
```

So the current evidence is green, but it does not prove what the assertion claims.

## Expected behavior

The queued buffer must equal the registered example exactly: `['upkg', 'plan']` — no usage placeholders (`[`, `<`, `>`), no extra tokens.

## Proposed fix

```python
assert words == ['upkg', 'plan'], f'wrong queued buffer: {words}'
```

Optionally add an explicit placeholder guard so a future registry change cannot smuggle bracket syntax:

```python
assert words == ['upkg', 'plan'] and not any(ch in session.capture_buffer() for ch in '<>[')
```

(Buffer text is short and from the test-only probe file, so reading it twice is safe; capture into a variable once if preferred.)

## Test plan

- Re-run `./run-all.zsh pty` and confirm `zhelp-queue` still passes with the exact-equality assertion.
- Fault-injection: queue the usage template in a controlled experiment (e.g. patch the ZLE buffer probe to return `upkg plan [--only <list>]`) and assert the strengthened check fails while the old `words[:2]` form passes.
- Keep `coverage.json`'s `zhelp-queue` identity unchanged.

## Fix (2026-09-12, batch 4)

- **Status change:** Open → Fixed.
- `zhelp_queue` now asserts the exact queued buffer
  (`words == ['upkg', 'plan']`), so queueing the usage template
  `upkg plan [--only <list>]` can no longer satisfy the scenario. The recorded
  evidence (ZLE buffer files) shows the real queue is exactly the example.
- No new self-test identity (scenario change covered by the pty stage; the
  exactness follows from the existing buffer-probe machinery).
- Verified: full gate `20260912-213912-57c5679f` = `YES`, exit 0 with both PTY
  repetitions passing.
