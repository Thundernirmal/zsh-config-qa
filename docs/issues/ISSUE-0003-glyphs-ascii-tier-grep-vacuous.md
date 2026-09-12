# ISSUE-0003: `glyphs ascii tier` env case cannot fail because the output annotation echoes the requested tier

- **Status:** Fixed
- **Severity:** Medium
- **Category:** evidence-integrity
- **Affected:** `run-env.zsh:46`; target `functions/ztheme:125`
- **Confidence:** Confirmed by execution (mutation experiment)
- **Filed:** 2026-09-12 against harness `60f633a` plus the pending audit working tree
- **Reviewed:** 2026-09-12 against harness `7e97581`; confirmed actionable as filed

## Review disposition

Confirmed. The assertion still searches the complete status text for a value
that is also echoed from its own input, so it does not prove the resolved glyph
tier. The anchored-result assertion described below is the right fix family.

## Summary

The env-matrix case

```
check 'glyphs ascii tier' 'ztheme current | grep -i ascii' 'ZSH_UI_GLYPHS=ascii'
```

(`run-env.zsh:46`) greps the whole `ztheme current` output for `ascii`. The target prints the resolved tier **and** the requested tier on one line:

```
print -r -- "glyphs: $glyph (requested: ${ZSH_UI_GLYPHS:-auto})$sample"
```

(target `functions/ztheme:125`). With `ZSH_UI_GLYPHS=ascii` the literal annotation `(requested: ascii)` is present even if the resolved `$glyph` is something else (e.g. `nerd`), so `grep -i ascii` matches unconditionally and the case can never fail.

## Impact

A real regression that ignores `ZSH_UI_GLYPHS=ascii` and resolves `nerd`/`unicode` glyphs still reports `glyphs ascii tier` as passed. The env stage's glyph-tier coverage is therefore not trustworthy, and a release verdict can be green while the ascii fixture/tier handling is broken. The same pattern is only safe for the other two glyph cases because their requested tier (`auto`) does not appear in the resolved field.

Secondary issue: `grep -q` as an early-exiting consumer in a pipe is the hazard AGENTS.md calls out for pipeline failure detection (`ztheme` output is small today, but the construct is wrong under `PIPE_FAIL`, which the case scripts set in `qa_common.py:151`).

## Root cause

The assertion matches an annotation that is derived from the input (`ZSH_UI_GLYPHS`) rather than the observable result (`$glyph`). It asserts that the variable was passed through, not that the tier was applied. `grep -i` also matches case-insensitively anywhere in the line, widening the false-positive surface.

## Reproduction

Isolated HOME with `HOME/.config/zsh -> target` (read-only), then a stand-in regression that resolves every requested tier to `nerd`:

```sh
$ H=/tmp/opencode/audit-r2/issue3-home   # HOME/.config/zsh -> target (read-only)
$ cat /tmp/opencode/audit-r2/issue3-repro.zsh
source "$HOME/.config/zsh/init.zsh"

# Regression stand-in: resolve any requested tier to 'nerd'.
_zsh_theme_resolve_glyph_tier() { REPLY=nerd; return 0 }

print -r -- '--- ztheme current output with ZSH_UI_GLYPHS=ascii and a broken resolver ---'
ztheme current

print -r -- '--- current case code: ztheme current | grep -i ascii ---'
ztheme current | grep -i ascii
print -r -- "current case grep rc=$?"

print -r -- '--- proposed code: capture, then grep -q "^glyphs: ascii" file ---'
ztheme current > /tmp/opencode/audit-r2/issue3.out
command grep -q '^glyphs: ascii' /tmp/opencode/audit-r2/issue3.out
print -r -- "proposed grep rc=$?"

$ TERM=xterm-256color HOME=$H ZSH_UI_GLYPHS=ascii zsh -d -f issue3-repro.zsh
--- ztheme current output with ZSH_UI_GLYPHS=ascii and a broken resolver ---
UI theme: terminal
fzf theme: terminal (inherits UI)
fzf layout: compact
glyphs: nerd (requested: ascii) [󰘳 󰄬 │]
color depth: truecolor
external fzf options: yes
--- current case code: ztheme current | grep -i ascii ---
glyphs: nerd (requested: ascii) [󰘳 󰄬 │]
current case grep rc=0
--- proposed code: capture, then grep -q "^glyphs: ascii" file ---
proposed grep rc=1
repro script rc=0
```

The current case exits 0 against a resolver that produced `nerd`; the anchored form correctly exits 1.

## Expected behavior

With `ZSH_UI_GLYPHS=ascii` the case must fail unless the **resolved** tier is `ascii`; i.e. the line must begin `glyphs: ascii ` (not merely mention `ascii` in the `(requested: …)` annotation).

## Proposed fix

Capture first, then match the resolved field only, avoiding `grep -q` in a pipe:

```sh
check 'glyphs ascii tier' 'ztheme current > "$HOME/ztheme.out" && command grep -q "^glyphs: ascii" "$HOME/ztheme.out"' 'ZSH_UI_GLYPHS=ascii'
```

(An equivalent zsh pattern match on captured output is fine; the requirement is anchored matching of the resolved tier plus no early-exiting consumer in a pipeline.)

## Test plan

- Verify the fixed case passes on the pristine target (`ztheme current` prints `glyphs: ascii (requested: ascii)`).
- Fault-injection: run the case code in a child shell where `_zsh_theme_resolve_glyph_tier` is stubbed to `REPLY=nerd` (as in the reproduction) and assert the case fails; the current form passes, proving the fix.
- Add the mutation as a harness self-test (or a documented manual check) so a future regression in the annotation format does not reopen the same hole.
- Re-run `./run-all.zsh env` (fixtures auto-added; expect exit 2 `INCOMPLETE` for the focused run, case `glyphs ascii tier` pass).

## Fix (2026-09-12, batch 5)

- **Status change:** Open → Fixed.
- run-env.zsh glyph cases now capture `ztheme current` into a per-case file
  under the isolated HOME and assert the **resolved** field
  (`command grep -q "^glyphs: ascii"`) for the `ZSH_UI_GLYPHS=ascii` and
  `LC_ALL=C` auto cases, and `^glyphs: unicode` for `NO_NERD_FONT`. The
  `(requested: ...)` annotation can no longer satisfy the match, so a target
  regression that ignores the explicit tier now fails the case. The grep runs
  against a file, not a pipeline (AGENTS.md hazard avoided). Case names
  unchanged, so `coverage.json` needed no edit.
- Verified: review approved with the mutation argument (resolved
  `unicode (requested: ascii)` passes the old check and fails the new one);
  full gate `20260912-215025-6c8985a5` = `YES`, exit 0.
