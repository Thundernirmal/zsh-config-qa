# ISSUE-0005: `Session.check()` leaks `PIPE_FAIL` into the interactive shell under test

- **Status:** Open
- **Severity:** Medium
- **Category:** test-fidelity
- **Affected:** `qa-pty.py:281-295` (write at `qa-pty.py:284`)
- **Confidence:** Confirmed by execution (isolated snippet and patched Session)
- **Filed:** 2026-09-12 against harness `60f633a` plus the pending audit working tree

## Summary

`Session.check()` writes each command body to `WORK/pty-command-<token>.zsh` prefixed with

```python
script.write_text('setopt LOCAL_OPTIONS PIPE_FAIL\n' + code + '\n')   # qa-pty.py:284
```

and then runs `source <script>` at the interactive shell's top level (`qa-pty.py:286`). `LOCAL_OPTIONS` only restores options when an enclosing *function* (or a script inside a function) returns. A sourced file executed at the interactive top level is not a restoring scope, so `PIPE_FAIL` remains set in the shell after `check()` returns, for the rest of the scenario. Wrapping the same snippet in a function restores it correctly.

## Impact

- **Fidelity:** after the first `check()` in a scenario, every later command typed with `sendline()` / `send()` runs with `pipefail`, which is not the baseline. The target config does not enable `PIPE_FAIL` globally (only inside isolated `npkg` functions: `lib/functions-nix.zsh:89,262,417`), so the shell no longer matches a fresh interactive session.
- **False failures:** any interactive command whose pipeline deliberately tolerates an early-exiting consumer (`| head`, `| grep -q`) can now fail with exit 141/SIGPIPE, failing a scenario for harness reasons. Conversely, behavior that only occurs under `pipefail` can make later probes report a state a user would never see.
- Scenarios call `check()` several times (e.g. the whole `cgm_roundtrip` sequence at `qa-pty.py:827-835`, buffer probes at `qa-pty.py:299-300`), so the leak is active for most of each scenario's life. It does not affect the command under test inside that one `check()` (which sets the option intentionally) — it contaminates everything after it.

## Root cause

Misunderstanding of `LOCAL_OPTIONS` scope: it restores options on return from a function, but sourcing a file at the interactive top level executes in the caller's scope, so the "local" scope is the whole interactive shell.

## Reproduction

Part (a) — sourced at top level leaks:

```sh
$ printf 'setopt LOCAL_OPTIONS PIPE_FAIL\n' > /tmp/opencode/audit-r2/pipefail-snippet.zsh
$ zsh -f -c 'source /tmp/opencode/audit-r2/pipefail-snippet.zsh; if [[ -o pipefail ]]; then print -r -- TOP_LEVEL_LEAKED; else print -r -- TOP_LEVEL_CLEAN; fi'
TOP_LEVEL_LEAKED
```

Part (b) — same snippet inside a function is restored:

```sh
$ zsh -f -c 'f() { source /tmp/opencode/audit-r2/pipefail-snippet.zsh; }; f; if [[ -o pipefail ]]; then print -r -- AFTER_FUNCTION_SET; else print -r -- AFTER_FUNCTION_RESTORED; fi'
AFTER_FUNCTION_RESTORED
```

Part (c) — the real class, driven through a patched `qa_pty.Session` in an owned `/tmp` work dir (`issue5-pty-repro.py`; `Session` imported from the harness by path, module attributes overridden via environment as `tests/test_gate.py` does):

```sh
$ python3 issue5-pty-repro.py
before any check(): pipefail=off
after one check(): pipefail=on
after two check()s: pipefail=on
rc=0
```

The state probe is `print -r -- "QA5-$options[pipefail]-<token>"`, so terminal echo of the command cannot satisfy the marker. The script retains the leaked content:

```sh
$ cat /tmp/opencode/audit-r2/run-issue5/pty-command-*.zsh
setopt LOCAL_OPTIONS PIPE_FAIL
true
```

## Expected behavior

`check()` must run the command body with `PIPE_FAIL` in effect for the body, and must leave the shell's options exactly as they were before the call (the same guarantee `LOCAL_OPTIONS` gives inside a function).

## Proposed fix

Wrap the body in a function scope before sourcing, so `LOCAL_OPTIONS` restores on return (function definitions, `zle -N`, and `bindkey` still persist; only option state is scoped):

```python
script.write_text(
    'function __qa_check() {\n'
    '  setopt LOCAL_OPTIONS PIPE_FAIL\n'
    + code +
    '\n}\n__qa_check\n'
)
```

Alternatively record `[[ -o pipefail ]]` before sourcing and restore it afterwards, but the function wrapper generalizes to the other options a body might set. Note the check protocol (`source file; qa_rc=$?; print …`) still records the body's status.

## Test plan

- Add a fault-injection test beside `test_pty_assertion_cannot_pass_from_echo` in `tests/test_gate.py`: start a `Session`, capture `[[ -o pipefail ]]`, run `session.check('true')`, and assert the option is still unset (and that a deliberate pipeline failure inside a second `check('false | true')` is still detected while pipefail is active inside the body).
- Keep a unit-level regression for the sourced-snippet semantics (parts a/b above) if a pure-PTY test is too slow.
- Re-run `./run-all.zsh pty` (both repetitions) and confirm the credential and buffer-probe scenarios are unaffected.
