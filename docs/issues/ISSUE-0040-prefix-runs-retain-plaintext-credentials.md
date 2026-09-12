# ISSUE-0040: Pre-fix `.runs/` directories retain plaintext synthetic credential values

- **Status:** Fixed
- **Severity:** Low
- **Category:** secret-hygiene
- **Affected:** 20 run directories under `.runs/` produced before commit `5a02c67` (ISSUE-0008 fix); 80 `pty-command-*.zsh` files
- **Confidence:** Confirmed by execution
- **Filed:** 2026-09-12 against harness revision `7aafe90` (third independent review pass)

## Summary

ISSUE-0008's expected behavior is that a synthetic credential value "must
never be written into run artifacts". Retained evidence from runs produced
before the fix still contains the plaintext `qa-<32-hex>` values in the
generated check scripts, even though post-fix runs are clean.

## Impact

The values are generated synthetic secrets and the run directories are mode
0700, so there is no real credential exposure. The artifacts nevertheless
contradict the post-fix invariant and are exactly the kind of retained
evidence an operator may copy into a bug report; the issue index says run
reports must not be published, and these older directories are the riskiest
part of that set.

## Root cause

The fix (commit `5a02c67`, batch 6) changed new checks to hash the value and
stopped writing plaintext into new `pty-command-*.zsh` files. It did not scrub
or mark artifacts created before it; run artifacts are intentionally retained
(`README.md:67`).

## Reproduction (executed 2026-09-12 in the harness root)

```
$ grep -rlE 'qa-[0-9a-f]{32}' .runs | awk -F/ '{print $2}' | sort -u | wc -l
20
$ grep -rlE 'qa-[0-9a-f]{32}' .runs | awk -F/ '{print $2}' | sort -u | tail -1
20260912-215025-6c8985a5          # newest affected run; post-5a02c67 runs have zero matches
$ grep -rlE 'qa-[0-9a-f]{32}' .runs | head -1
.runs/20260912-210646-ff36a6ad/pty-command-a61c7d20efed45ea8776607ef94d292a.zsh
```

Independent verification confirmed zero matches in the post-fix gates
(`20260912-215458-9601a735`, `20260912-220611-4c20bb3b`,
`20260912-223424-da8ad654`) and in every `.jsonl`/`.log`/`.md`/`.json` file
across all runs.

## Expected behavior

No retained run artifact contains a plaintext synthetic credential value, or
such artifacts are explicitly quarantined/marked as pre-fix evidence.

## Proposed fix

- One-time scrub of the affected `pty-command-*.zsh` files (or deletion of the
  20 pre-fix run directories, which are disposable local evidence per
  `README.md:67` and `AGENTS.md`).
- Optionally add a post-run local check that greps the new run directory for
  `qa-[0-9a-f]{32}` and records a finding; keep it out of the verdict because
  the value pattern is harness-specific.

## Test plan

- After the scrub, `grep -rlE 'qa-[0-9a-f]{32}' .runs` returns no
  `pty-command-*.zsh` file.
- Re-run `./run-all.zsh pty` and confirm the new run directory has no
  plaintext matches.

## Resolution (2026-09-12, batch 8)

- **Status change:** Open → Fixed.
- All 80 retained `pty-command-*.zsh` files from pre-fix runs were scrubbed in
  place (`qa-<32 hex>` → `<redacted synthetic credential>`); the scrub is a
  local, gitignored-artifact action, so nothing was committed. Post-scrub
  `grep -rlE 'qa-[0-9a-f]32' .runs` returns zero matches anywhere, and every
  run since the 0008 fix (verified for `20260912-215458-9601a735` and later)
  was already clean.
- **Declined:** the optional post-run plaintext grep check. The pattern is
  harness-specific, would add a standing secret-shaped scan to every run, and
  could false-positive on unrelated content; the invariant is enforced by
  construction now (the check bodies are hash-based). Documented here as the
  resolution for the declined sub-item.
