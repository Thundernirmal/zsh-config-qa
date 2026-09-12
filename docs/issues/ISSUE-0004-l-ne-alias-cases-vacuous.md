# ISSUE-0004: `L` and `NE` alias cases pass with no aliases defined (vacuous assertions)

- **Status:** Open
- **Severity:** Medium
- **Category:** evidence-integrity
- **Affected:** `run-safe.zsh:159-160`; target aliases `70-globals.zsh:14,18`
- **Confidence:** Confirmed by execution
- **Filed:** 2026-09-12 against harness `60f633a` plus the pending audit working tree

## Summary

Both global-alias checks are written so that the absence of the alias produces a successful exit:

```
qa 'L alias'  'print "aaa\nbbb" L >/dev/null'        'ZSH_GLOBAL_ALIASES=1'   # run-safe.zsh:159
qa 'NE alias' 'command false NE; (( $? == 1 ))'      'ZSH_GLOBAL_ALIASES=1'   # run-safe.zsh:160
```

Without the aliases these are ordinary shell words:

- `print "aaa\nbbb" L` prints the extra literal argument `L` (redirected to `/dev/null`) and exits 0.
- `command false NE` runs `false` with the harmless extra argument `NE`, exits 1, and the following `(( $? == 1 ))` evaluates true (exit 0).

So both cases pass in a shell where `L`/`NE` were never defined, where `ZSH_GLOBAL_ALIASES=1` handling is broken, or where the alias definitions were removed. The `NE` case additionally cannot detect a wrong expansion because `false` writes nothing to stderr; the alias only redirects stderr, and even a command that does write stderr would be masked by the `$? == 1` test (which holds for `false` with or without the alias).

## Impact

Two of the seven global-alias acceptance cases (the whole `== Meta (global aliases enabled) ==` block, `run-safe.zsh:154-161`) provide no evidence. A regression that stops enabling global aliases under `ZSH_GLOBAL_ALIASES=1`, or that removes `L`/`NE`, still passes the safe sweep.

## Root cause

The test asserts only a command's exit status that is identical with and without the alias. It never inspects alias state (`$galiases`) nor an observable effect that differs when the alias expansion is applied. `L` is a pipe to `less` (output content is the same in a non-tty); `NE` suppresses stderr on a command that emits none.

## Reproduction

Plain `zsh -f`, no aliases loaded (neither alias defined — `$+galiases[L]=0`, `$+galiases[NE]=0`):

```sh
$ zsh -f issue4-repro.zsh
aliases defined: L=0 NE=0
--- current L case under plain zsh -f (no global aliases) ---
L case rc=0
--- current NE case under plain zsh -f (no global aliases) ---
NE case rc=0
--- discriminating checks that do fail without the aliases ---
galiases-present rc=1
ls stderr bytes: 118
```

(The last check, `command ls /nonexistent-qa-path NE`, wrote 118 bytes of stderr when `NE` was undefined; with the alias defined it would be empty.)

The two commands are exactly the assertion bodies from `run-safe.zsh:159-160`.

## Expected behavior

Each alias case must fail when the alias is missing or does not expand as intended. The `NE` case must demonstrate stderr suppression on a command that actually writes to stderr; the `L` case must show that `L` expands (alias state or invocation effect).

## Proposed fix

Check the alias state and an observable effect, e.g.:

```sh
qa 'L alias'  '[[ $galiases[L] == "| less" ]]' 'ZSH_GLOBAL_ALIASES=1'
qa 'NE alias' 'rm -f "$HOME/ne.err"; command ls /nonexistent-qa-path 2>"$HOME/ne.err" NE; rc=$?; [[ $rc == 2 && ! -s "$HOME/ne.err" ]]' 'ZSH_GLOBAL_ALIASES=1'
```

The `NE` form places `2>"$HOME/ne.err"` *before* `NE` so the alias expansion (`2>/dev/null`) is applied last; without the alias the error text lands in the file and the assertion fails. (Verifying `$galiases` covers definition; the stderr form covers the effect.)

## Test plan

- Run both rewritten cases with aliases enabled: pass.
- Fault-injection: run the same bodies with `ZSH_GLOBAL_ALIASES` unset (or with the alias definitions skipped) and assert they fail; the current versions pass under both conditions.
- Keep the `G/W/H/T/NUL` cases as-is (their content comparisons do differ and are covered), but consider asserting `$galiases` for each so all five report real alias wiring.
- Re-run `./run-all.zsh safe` and confirm the Meta block still reports 5 passing cases.
