# Issue discovery and filing workflow

This document defines the repeatable process used to audit this harness, confirm
defects, and file them as individual issue documents. It is a maintenance
companion to `AGENTS.md`, not a second source of truth for harness behavior.

The workflow has one rule above all others: **a filed issue must be backed by
either an executed reproduction or an explicit line-by-line inspection, and the
two must be labeled.** No claim is filed from a hunch alone.

## Ground rules

- The configuration checkout under test is read-only. Reproduction must never
  write to the target repository, its Git state, or the operator's real
  `~/.zshrc`, history, keyring entries, or Nix profile.
- Sandboxes belong in `/tmp/opencode` (or a freshly allocated owned run). Live
  package, network, and credential operations only happen through
  `./run-all.zsh` stages, never from an ad hoc audit command.
- Credential experiments use only generated synthetic values, unique names, and
  the same register-before-store rules as the harness itself.
- A slow, clearly explained negative result is preferred over a plausible
  positive. Refuted candidates stay documented in the dossier; they are not
  quietly dropped and issue IDs are never reused.
- Do not weaken a check to make an audit "pass", and do not edit harness code
  while a gate run is active.

## Phases

### 0. Recon and identity

1. `git status --short` and `git log -1` in the harness; note uncommitted work.
2. Record the target commit and dirty state (`git -C <target> rev-parse HEAD`).
3. Confirm no gate run is active (per-user lock is free) and note tool versions
   from the latest `report.json`.
4. Confirm the remote visibility before publishing anything (`gh repo view`).

### 1. Inventory map

Read `AGENTS.md`, `README.md`, and `coverage.json`; map every claim to its owner
file and to the case names that prove it. A claim without an observable case is
itself a finding.

### 2. Static review by ownership area

Parallelize by module boundary, not by file count:

| Area | Owner files | Focus |
|---|---|---|
| Verdict and evidence | `release.py` | fail-closed decisions, inventory enforcement, report integrity, signals, cleanup |
| Isolation and primitives | `qa_common.py` | owned paths, environment scrubbing, bounded processes, case execution |
| Interactive mechanics | `qa-pty.py` | PTY synchronization, echo traps, effect assertions, credential lifecycle |
| Command sweeps | `run-safe.zsh`, `run-env.zsh`, `setup-fixtures.zsh` | case construction, pipeline failure, fixtures, skip policy |
| Self-tests | `selftest.py`, `tests/test_gate.py` | whether each test proves what its name claims |
| Contracts | `README.md`, `AGENTS.md`, `coverage.json` | documentation/code drift, inventory completeness, severity of missing coverage |

### 3. Adversarial hypothesis checklist

Every review cycle walks this list against the area under review:

- Can a required case pass without executing its code (startup `exit`, missing
  file, empty output, echoed text, stale probe file)?
- Can a required case be removed, renamed, skipped, or deduplicated without the
  verdict noticing?
- Can a stage exit 0 with zero or partial evidence?
- Can a check match a banner or diagnostic instead of the effect?
- Can a timeout, signal, or interruption turn into a skip or a pass?
- Can cleanup report success while a process, credential, or file survives?
- Can a path, symlink, PID reuse, or inherited environment variable escape the
  owned run or redirect validation to the wrong repository?
- Can the operator's environment silently change the baseline (locale, TERM,
  color, Git overrides, umask, PATH)?
- Does a failure preserve enough evidence to distinguish target vs harness vs
  environment fault?

### 4. Reproduction

For each candidate, build the smallest sandbox that exercises the claim. Record:

- the exact commands (copy-pasteable, no placeholders);
- the observed output, verbatim;
- whether the result confirms or refutes the candidate;
- whether the failure is deterministic or intermittent.

### 5. Triage

Assign severity using this rubric:

| Severity | Meaning |
|---|---|
| High | False `YES`/approval path, vacuous required coverage, false "cleanup verified", credential or isolation breach |
| Medium | False failure, intermittent flake, fidelity/contract deviation, missed teardown, misleading evidence |
| Low | Operator UX, durability under power loss, cosmetics, weak test assertion, dead code |

Each entry also gets a category (`fail-open`, `evidence-integrity`,
`cleanup`, `isolation`, `flaky`, `contract-drift`, `hygiene`, ...), affected
`file:line` references, and a confidence label: **confirmed by execution**,
**confirmed by inspection**, or **hypothesis**.

### 6. Filing

File one document per issue at `docs/issues/ISSUE-NNNN-<slug>.md` using the
template below, and update the index in `docs/issues/README.md`. IDs are
allocated monotonically and never reused, including for refuted candidates,
which are filed with `Status: Refuted`.

```markdown
# ISSUE-NNNN: Title

- **Status:** Open | Refuted | Fixed
- **Severity:** High | Medium | Low
- **Category:** ...
- **Affected:** `file:line`, ...
- **Confidence:** Confirmed by execution | Confirmed by inspection | Hypothesis
- **Filed:** YYYY-MM-DD against harness revision <short sha or working tree>

## Summary
## Impact
## Root cause
## Reproduction      (exact commands + observed output)
## Expected behavior
## Proposed fix
## Test plan
```

### 7. Verification before closure

- A second reviewer re-reads each filed issue against the current source and
  re-runs the reproduction in a clean sandbox.
- A fix is complete only when the reproduction no longer fires, any
  coverage-affecting change updates `coverage.json` and adds a fault-injection
  self-test in the same change (per `AGENTS.md`), and the full gate is re-run
  before any new `YES` is claimed.
- Documentation-only changes are verified with the syntax/syntax-check commands
  in `AGENTS.md` and never inherit a previous run's approval.

## Suggested parallelization

Run phases 2 and 4 as independent workstreams (one per ownership area), each
writing its own dossier. Phase 7 uses at least one reviewer who did not author
the fix or the original finding. Keep discoveries unmerged until triage so
duplicate reports reinforce confidence instead of hiding each other.
