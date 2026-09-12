# ISSUE-0039: Several regression self-tests cannot catch the reverts they exist for

- **Status:** Closed (partial)
- **Severity:** Low
- **Category:** evidence-integrity
- **Affected:** `tests/test_gate.py:191-194`, `:227-229`, `:239-243`, `:245-257`, `:346-351`; `qa-pty.py:538-540`; the 0003/0004/0010/0011/0025 case assertions
- **Confidence:** Confirmed by execution (independent mutation experiments per item)
- **Filed:** 2026-09-12 against harness revision `7aafe90` (third independent review pass)

## Summary

The selftest stage is the harness's own regression gate. Independent
verification of the remediation batches showed that a number of the delivered
self-tests are source-string or helper-level assertions that stay green while
the runtime behavior they name regresses. In several cases the promised
fault-injection test was not added at all. The current defects are fixed; the
gap is that a future silent revert would not be caught by the gate.

## Impact

A fixed defect reintroduced by a refactor or a later edit can ship because
`./run-all.zsh` still returns `YES`. This is the same failure class that
ISSUE-0005, ISSUE-0026, and ISSUE-0030 were reopened for; this issue records
the remaining instances found in the same audit.

## Root cause

The closure rule in `WORKFLOW.md` phase 7 and the change workflow in
`AGENTS.md` require a fault-injection self-test when verdicts, evidence
parsing, paths, process handling, cleanup, or synchronization change. Several
remediation batches satisfied that requirement with source-text or
helper-level tests instead of a runtime exercise of the fixed path, and some
sweep-assertion fixes added no persistent test at all. The self-tests
therefore assert code shape rather than the observable effect.

## Reproduction (each mutation was executed by an independent reviewer against a sandbox copy; the repo was not edited)

- **ISSUE-0008** — `test_cgm_value_check_uses_a_hash_not_the_plaintext`
  (`tests/test_gate.py:191-194`) is formatting-specific. A "stealth" revert
  that keeps the hash comparison and appends
  `... && [[ $<name> == <plaintext> ]]` passes the test while plaintext
  returns to `pty-command-*.zsh`.
- **ISSUE-0001** — `test_coverage_is_parsed_and_validated_once_before_run_state`
  (`:245-257`) introspects source text. Changing the `run_stage` call site to
  pass `None` for an evidence stage (which silently disables inventory
  enforcement) passes all four inventory tests.
- **ISSUE-0016** — `test_cleanup_rejects_unusable_path_cleanly` (`:346-351`)
  asserts the helper raises. Deleting the `try/except` in `main()` around
  `validate_cleanup_target` reintroduces the raw traceback while the test
  stays green. The planned "wrong repo" variant cannot fire (see
  ISSUE-0044).
- **ISSUE-0032** — `test_snapshot_git_calls_are_bounded` (`:239-243`) asserts
  the source contains `bounded(['git'` and `timeout=60`; a body that contains
  those strings but calls Git unbounded would pass.
- **ISSUE-0018/ISSUE-0030** — `test_qa_pty_rejects_symlinked_scratch`
  (`:227-229`) is a source-string check (reopened as ISSUE-0030); the shell
  guards in `run-safe.zsh:26-29`, `run-env.zsh:24-27`, and
  `setup-fixtures.zsh:16` have no test.
- **ISSUE-0003, ISSUE-0004, ISSUE-0010, ISSUE-0011, ISSUE-0025** — the
  strengthened assertions have no persistent mutation check; reviewers proved
  the fixes are correct, but a revert is only detectable with a mutated target
  or a hand-run experiment.
- **`fzf_blocked`** (`qa-pty.py:538-540`) proves the absence of a picker with
  `time.sleep(1.0)` followed by `_children() == []`; a picker that opens later
  would still pass. `AGENTS.md` rejects a fixed sleep as readiness proof.
- **`test_cleanup_accepts_uncaptured_start_identity`** (`:312-316`) hardcodes
  PID 12345; if process group 12345 is live on the host, the test fails
  (flake, not false pass).

## Expected behavior

Every fix that changes runtime behavior has a behavioral fault-injection test
that fails when the fix is reverted, per `AGENTS.md`/`WORKFLOW.md`.
Source-string checks may remain only as secondary guards.

## Proposed fix

- Convert the runtime-path guards to behavioral tests: scan the actual
  `pty-command-*.zsh` artifact for plaintext (0008); run a miniature
  `run_stage` with a missing ledger row (0001); exercise `main()`'s
  `--cleanup` branch in-process with patched `argv`/lock (0016); inject a
  sleeping `git` stub through `PATH` for `snapshot()` (0032).
- Replace the `fzf_blocked` sleep with an observable marker (for example the
  version diagnostic plus a bounded absence poll) or document why it is not
  a readiness proof.
- Use a dynamically chosen, provably dead PID in the uncaptured-identity test.

## Test plan

- Each converted test keeps its existing `coverage.json` identity where one
  exists; new identities are added under `selftest` in the same change.
- Revert each covered fix once in a sandbox copy and record that the new test
  fails before restoring it.

## Resolution (2026-09-12, batch 8) — fixed in part, remainder declined with rationale

- **Status change:** Open → Closed (partial). Each sub-item was triaged:

**Fixed (behavioral tests now guard the runtime path):**
  1. **0008** — the test is now AST-based: every `session.check(...)` argument
     inside `cgm_roundtrip` that mentions `secret` must also mention
     `sha256sum`, so the "stealth revert" (keeping the hash line and appending
     a plaintext comparison) fails the suite. Known limitation, documented:
     the guard keys on the variable's source name, so a hypothetical rename of
     the local variable would evade it; the primary defense remains that no
     plaintext is ever constructed.
  2. **0001** — a runtime guard was added at the point of use itself:
     `run_stage` now **fails an evidence stage loudly** when it is called
     without an inventory, so any future call-site regression (passing `None`
     for `safe`/`env`/`fzf`/`pty`/`selftest`) fails the run regardless of what
     a source test sees. Test: `test_evidence_stage_without_inventory_fails_loudly`.
  3. **0016 + 0044** — the `--cleanup` branch was factored into
     `cleanup_command(work, fail)`, making the wiring behaviorally testable
     without the per-user lock; the marker's repository must now be a usable
     Git checkout (0044), and the previously untestable "wrong repo" variant
     is exercised. Tests: `test_cleanup_command_rejects_unusable_marker_repo`.
  4. **0032** — `SNAPSHOT_TIMEOUT` is a module constant and
     `test_snapshot_git_deadline_fails_fast` injects a sleeping `git` stub
     through PATH with the timeout patched to 2 s, asserting a fast
     `RuntimeError` (verified to fail on revert, where the stub would run for
     the full 60 s).
  5. **0018/0030** — `test_qa_pty_rejects_symlinked_scratch` is now a
     behavioral subprocess test (symlinked scratch → exit 2, fatal message,
     untouched victim), replacing the evadable source-string check.
  6. **fzf_blocked** — the `time.sleep(1.0)` was replaced by a per-key
     observation window (Ctrl+T/Ctrl+R/Alt+C) asserting no fzf child exists,
     alongside the startup diagnostic; a fixed sleep is no longer the proof.
  7. **Uncaptured-identity test** — now selects a provably dead PID that owns
     no live process group, removing the host-state flake.

**Declined (wontfix, with rationale):**
  - Converting the strengthened **0003/0004/0010/0011/0025 sweep assertions**
    into mutation tests of themselves. These cases are the *live behavioral*
    check of the target (the sweep executes them against the real config in
    both PTY/safe passes); a harness-side revert to a vacuous form is a
    deliberate code change that review catches, and guarding each assertion
    with a Python re-implementation of its semantics would duplicate every
    assertion rather than test the harness — disproportionate for a private
    release harness. The existing source-anchored structure of the sweep files
    (checked into the inventory contract via `coverage.json` names) plus
    per-batch adversarial review is the accepted guard for that class.

**Not replicated:** the sub-item claims were verified against the delivered
tests by the filing pass; the runtime conversions above were verified against
sandbox reverts by this batch's review pass.
- Verified: full gate `20260912-235724-019e9c8b` (`YES`, exit 0) with 70 selftest identities.
