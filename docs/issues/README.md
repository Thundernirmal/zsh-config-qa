# Harness audit report and issue index

- **Audit date:** 2026-09-12
- **Harness revision:** `60f633a` plus the then-pending working tree (`README.md` edit, `AGENTS.md`); every issue was reproduced before this `docs/` tree existed.
- **Target under test:** `~/.config/zsh @ 3f91a85b` (clean, read-only during the audit)
- **Environment:** Linux 7.2.4-arch1, zsh 5.9.2, fzf 0.74.3, Python 3.14.7, Nix 2.35.2, `secret-tool` present
- **Method:** [WORKFLOW.md](WORKFLOW.md) — recon, static review by ownership area, adversarial hypothesis checklist, isolated sandbox reproduction, triage, one file per issue, independent verification.
- **Original result:** 29 issues filed as open (5 High, 8 Medium, 16 Low). A fresh full gate run of the audited tree returned `RELEASE: YES` (exit 0, run `20260912-193357-431ba0a8`); the issues below are about what that `YES` could miss, not evidence that it was faked.
- **Review date/revision:** 2026-09-12 against harness `7e97581`
- **Reviewed result:** 26 open issues (4 High, 8 Medium, 14 Low) and 3 refuted issues. ISSUE-0008 was reduced from High to Low.
- **Remediation result (2026-09-12):** all 26 open issues fixed in five reviewed batches (commits `ef8f429`, `6953be1`, `2ea2d8b`, `e898a90`, `1a66d03`); every batch passed a fresh full gate (`YES`, exit 0).
- **Re-audit result (2026-09-12):** 24 issues verified Fixed, 3 verified Refuted. Two issues reopened upon adversarial verification: ISSUE-0005 (self-test probe order flaw makes test vacuous on revert) and ISSUE-0026 (confirm_query uses prompt echo and fixed sleep; fkill-signal can signal arbitrary processes; promised self-test was never implemented). One new issue discovered and filed: ISSUE-0030 (qa-pty.py accepts symlinked scratch directory, missing containment guard). Statuses: 3 Open (0005, 0026, 0030), 24 Fixed, 3 Refuted.
- **Second-opinion audit (2026-09-12, parallel session):** 29 issues independently re-verified at `777ca29` plus a full-repo audit. 22 verified Fixed, 3 Refuted; 4 reopened (0001 residual unvalidated `coverage` read and point-of-use `None`; 0005 unsound companion self-test; 0026 unproven selection readiness and missing regression test; 0027 unreadable-target and post-allocation envelope residues) and 5 additional issues filed (0031 registry `start: null` false `NO`; 0032 unbounded snapshot Git; 0033 stale index remediation record; 0034 unused test imports; 0035 `run_case` scratch re-validation). ISSUE-0005's metadata was corrected back to Open: the described "Re-fix" reordering is not present in the delivered tree. Final statuses: 22 Fixed, 3 Refuted, 10 Open (0001, 0005, 0026, 0027, 0030-0035).
- **Remediation record (2026-09-12, complete):** every issue from the audit and both verification passes is fixed in reviewed batches, each with a green full gate and an adversarial review including a cumulative regression check:

  | Batch | Commit | Issues | Gate (YES, exit 0) |
  |---|---|---|---|
  | 1 | `ef8f429` | 0001, 0002, 0028 | `20260912-204752-2d13b992` |
  | 2 | `6953be1` | 0007, 0024, 0027, 0029, 0015 | `20260912-205801-76cafce0` |
  | 3 | `2ea2d8b` | 0006, 0012, 0016, 0020, 0021, 0022 | `20260912-210646-ff36a6ad` |
  | 4 | `e898a90` | 0005, 0009, 0010, 0011, 0019, 0026 | `20260912-213912-57c5679f` |
  | 5 | `1a66d03` | 0003, 0004, 0018, 0025 | `20260912-215025-6c8985a5` |
  | 6 | `5a02c67` | 0008, 0014 | `20260912-215458-9601a735` |
  | cleanup | `777ca29` | removed a stray `t6.zsh` probe | — |
  | 7 | this commit | 0001/0005/0027 re-fixes + 0026 re-fix (counter-verified selection) + 0030-0035 | `20260912-223424-da8ad654` |

  The final delivered tree is gated by `20260912-223424-da8ad654` (`YES`,
  exit 0, 63/11/4/61 selftest case rows plus 28+28 PTY rows, cleanup verified,
  target clean and unchanged). Batch 7 fixed all three reopenings (0001, 0005,
  0027 — 0026 by replacing the echo+sleep readiness with fzf's parsed match
  counter, a dedicated fault-injection test, and a fully anchored `^cowsay$`
  query) and fixed the five newly filed issues (0030-0035), including one real
  regression the verification passes had caught (0031). Statuses per issue:
  **Fixed (32), Refuted (3), none open.**

> The issued files contain machine-local paths inside reproduction commands. This repository is private; do not republish `docs/issues/` outside it. All reproductions are required to be sandboxed under `/tmp/opencode` and never to mutate the target checkout.

## How to read an issue

Each `ISSUE-NNNN-*.md` follows one template: metadata (`Status`, `Severity`,
`Category`, `Affected`, `Confidence`, `Filed`, `Reviewed`), then Review
disposition, Summary, Impact, Root cause,
Reproduction (copy-pasteable commands plus literal observed output), Expected
behavior, Proposed fix, and Test plan. `Confidence` is one of:

- **Confirmed by execution** — the failure was reproduced in a sandbox;
- **Confirmed by inspection** — proven by reading the exact code paths (a
  reproduction may still be sketched);
- **Hypothesis** — a grounded risk that could not be safely reproduced
  (used once here: ISSUE-0026, where the suspected race has a destructive
  worst case).

## Review outcome

- **Confirmed open as filed:** 0001, 0003, 0004, 0005, 0006, 0007, 0009,
  0010, 0011, 0012, 0015, 0016, 0018, 0019, 0021, 0028, 0029.
- **Open with corrected scope, severity, or fix constraints:** 0002, 0008,
  0014, 0020, 0022, 0024, 0025, 0026, 0027. The individual review
  dispositions are authoritative.
- **Refuted:** 0013 (the proposed fsync does not close the reproduced
  pre-close interruption window), 0017 (verified absence is the authoritative
  cleanup postcondition), and 0023 (the real isolated test has no unrelated
  error that can satisfy its assertion).

## Issue summary

| ID | Title | Severity | Category | Status | Confidence |
|---|---|---|---|---|---|
| [0001](ISSUE-0001-missing-coverage-key-disables-inventory.md) | Missing or empty coverage.json key silently disables case-inventory enforcement | Medium | fail-open | Fixed | Confirmed by execution (static path fixed; residual confirmed by inspection/execution) |
| [0002](ISSUE-0002-startup-exit-bypasses-case-body.md) | A target init.zsh that calls `exit 0` makes run_case pass without executing the case body | High | fail-open | Fixed | Confirmed by execution |
| [0003](ISSUE-0003-glyphs-ascii-tier-grep-vacuous.md) | `glyphs ascii tier` env case cannot fail because the output annotation echoes the requested tier | Medium | evidence-integrity | Fixed | Confirmed by execution (mutation experiment) |
| [0004](ISSUE-0004-l-ne-alias-cases-vacuous.md) | `L` and `NE` alias cases pass with no aliases defined (vacuous assertions) | Medium | evidence-integrity | Fixed | Confirmed by execution |
| [0005](ISSUE-0005-session-check-pipefail-leak.md) | `Session.check()` leaks `PIPE_FAIL` into the interactive shell under test | Medium | test-fidelity | Fixed | Confirmed by execution (isolated snippet and patched Session); companion self-test still unsound |
| [0006](ISSUE-0006-cleanup-misses-surviving-process-group.md) | cleanup() reports success while a recorded process group whose leader has exited survives | High | cleanup | Fixed | Confirmed by execution |
| [0007](ISSUE-0007-inherited-git-env-blinds-snapshot.md) | snapshot()/harness_identity() are blinded by inherited GIT_DIR/GIT_WORK_TREE | High | isolation | Fixed | Confirmed by execution |
| [0008](ISSUE-0008-credential-value-retained-unredacted.md) | Synthetic credential value is retained in `pty-command-*.zsh` and bypasses redaction in failure diagnostics | Low | secret-hygiene | Fixed | Confirmed by inspection (retained artifact plus inert placeholder experiment) |
| [0009](ISSUE-0009-session-leak-on-sync-failure.md) | Scenarios constructing `Session` before `try/finally` leak the PTY/zsh when `sync()` fails | Medium | resource-lifecycle | Fixed | Confirmed by execution (hanging startup HOME) and inspection |
| [0010](ISSUE-0010-zhelp-queue-weak-assertion.md) | `zhelp-queue` assertion `words[:2]` also accepts the usage template | Low | evidence-integrity | Fixed | Confirmed by inspection (recorded ZLE-buffer evidence and shlex behavior) |
| [0011](ISSUE-0011-cgm-list-grep-q-sigpipe.md) | `cgm list \| command grep -q <name>` is an early-exiting-consumer SIGPIPE hazard under `PIPE_FAIL` | Low | flaky | Fixed | Confirmed by execution (mechanism); live case latent, not observed |
| [0012](ISSUE-0012-non-dict-registry-line-crashes-cleanup.md) | A valid-JSON non-dict line in processes.jsonl raises an uncaught TypeError in cleanup() | Medium | cleanup | Fixed | Confirmed by execution |
| [0013](ISSUE-0013-process-registry-writes-not-fsynced.md) | Process-registry writes are not fsync'd, unlike case records | Low | cleanup | Refuted | Confirmed by execution |
| [0014](ISSUE-0014-stage-cwd-and-unbounded-git.md) | Target-owned stages run with `cwd=PROJECT`; fixture Git setup uses unbounded `subprocess.run` | Low | containment | Fixed | Confirmed by inspection |
| [0015](ISSUE-0015-blank-pacman-version-line.md) | tool_metadata records a blank first version line for tools like pacman | Low | evidence-integrity | Fixed | Confirmed by execution |
| [0016](ISSUE-0016-cleanup-nonexistent-emits-traceback.md) | `--cleanup /nonexistent` emits a raw Python traceback | Low | hygiene | Fixed | Confirmed by execution |
| [0017](ISSUE-0017-secret-tool-clear-result-ignored.md) | `cleanup()` ignores the result of `secret-tool clear` (`p` is unused) | Low | cleanup | Refuted | Confirmed by inspection (AST check) |
| [0018](ISSUE-0018-scratch-symlink-guard-gap.md) | `scratch` symlink is rejected only in `setup-fixtures.zsh`; sweeps check only `-d` | Low | containment | Fixed | Confirmed by execution (guard behavior); destructive consequence analyzed only |
| [0019](ISSUE-0019-scenario-descriptions-overclaim.md) | Scenario descriptions overclaim relative to what is asserted (`fkill` "cancels safely", `ctrl-t` "toggles previews", layout "frame and preview") | Low | contract-drift | Fixed | Confirmed by inspection |
| [0020](ISSUE-0020-dead-code-and-report-cosmetics.md) | Dead code and report cosmetics in the harness sources | Low | hygiene | Fixed | Confirmed by inspection |
| [0021](ISSUE-0021-atomic-json-no-fsync.md) | atomic_json renames without fsync, so report durability does not match the README claim | Low | durability | Fixed | Confirmed by inspection |
| [0022](ISSUE-0022-results-root-mode-0755.md) | Results root `.runs/` is world-readable (0755) while run directories are 0700 | Low | isolation | Fixed | Confirmed by execution |
| [0023](ISSUE-0023-weak-cleanup-test-assertion.md) | The credential-backend cleanup test asserts only a non-empty error list | Low | evidence-integrity | Refuted | Confirmed by execution |
| [0024](ISSUE-0024-git-nix-redirect-vars-unscrubbed.md) | Stage environment scrub leaves Git/Nix redirect variables that can move fixture writes outside the run | Medium | isolation | Fixed | Confirmed by execution (`GIT_OBJECT_DIRECTORY`); inspection for sibling variables |
| [0025](ISSUE-0025-ungated-sweep-tools-fail-instead-of-skip.md) | Ungated safe-sweep cases turn missing optional tools into NO instead of INCOMPLETE | Medium | contract-drift | Fixed | Confirmed by execution for `zdoctor`/`ports`; inspection for `unzip` and `--secrets` |
| [0026](ISSUE-0026-picker-enter-after-fixed-sleep.md) | Picker-select scenarios press Enter after fixed sleeps without confirming the filter; `fkill-signal` can signal the wrong process | Medium | flaky | Fixed | Hypothesis for the race; target-side destructive behavior confirmed by inspection |
| [0027](ISSUE-0027-pre-report-setup-failures.md) | Pre-stage setup failures escape the report/recovery envelope | Low | fail-closed | Fixed | Confirmed by execution (non-git target fixed; unreadable files and reportless allocation reproduced) |
| [0028](ISSUE-0028-non-dict-evidence-row-aborts-stages.md) | A non-dict JSON evidence row raises `AttributeError` and aborts every remaining stage | Low | evidence-integrity | Fixed | Confirmed by execution |
| [0029](ISSUE-0029-sighup-sigquit-not-trapped.md) | SIGHUP/SIGQUIT are not trapped, so a terminal hangup skips cleanup | Low | cleanup | Fixed | Confirmed by inspection; signal disposition confirmed by execution |
| [0030](ISSUE-0030-qa-pty-scratch-symlink-guard-gap.md) | `qa-pty.py` accepts a symlinked `scratch` directory, missing the containment guard of the other runners | Low | containment | Fixed | Confirmed by execution |
| [0031](ISSUE-0031-registry-start-null-false-no.md) | Registry records `start: null` for already-exited children; strict cleanup validation turns it into a false `NO` | Medium | cleanup | Fixed | Confirmed by execution |
| [0032](ISSUE-0032-snapshot-git-unbounded.md) | `snapshot()`/`harness_identity()` run unbounded `git` commands, so the runner can hang without a report or cleanup deadline | Medium | containment | Fixed | Confirmed by execution |
| [0033](ISSUE-0033-index-remediation-record-stale.md) | The issue index's remediation record is stale and internally inconsistent | Low | contract-drift | Fixed | Confirmed by inspection (cited runs checked against `.runs/`) |
| [0034](ISSUE-0034-unused-test-imports.md) | Unused function-local imports remain in `tests/test_gate.py` | Low | hygiene | Fixed | Confirmed by inspection (AST name-use check) |
| [0035](ISSUE-0035-run-case-scratch-revalidation.md) | `qa_common.run_case()` executes each case with `cwd=work/scratch` without re-validating the directory | Low | containment | Fixed | Confirmed by execution |

Open distribution after the second-opinion audit: 5 Medium (0001, 0005, 0026,
0031, 0032) and 5 Low (0027, 0030, 0033, 0034, 0035); 22 Fixed and 3 Refuted.
The three refuted issues are all Low. The table retains the original categories
and confidence; status and any review qualification are recorded in each linked
issue.

## Fix-first list

Severity reflects worst-case impact; the ordering below reflects the recommended
remediation sequence.

1. **Close the fail-open paths (High).** ISSUE-0001 (missing/empty/duplicate
   coverage entries) and ISSUE-0002 (case body never runs) can let required
   coverage pass without proving anything; both need schema validation plus a
   body-started sentinel and a fault-injection test.
2. **Restore identity and cleanup integrity.** ISSUE-0007 (High) and
   ISSUE-0024 (Medium) share one fix family: scrub the relevant Git/Nix
   redirect environment for snapshots and stages. ISSUE-0006 (leaderless
   process-group recovery) is the other High item.
3. **Repair vacuous or weak required cases (Medium).** ISSUE-0003,
   ISSUE-0004, ISSUE-0005, ISSUE-0025, ISSUE-0026: each is a required case
   that can pass or fail for reasons other than the behavior it names.
4. **Backlog robustness and evidence quality (Low).** Address the remaining
   open Low items according to their individual review dispositions. Treat
   ISSUE-0020 as opportunistic maintenance. Do not implement refuted issues
   0013, 0017, or 0023 as defect fixes.

Any fix that changes executable behavior must update `coverage.json` and add or
update a fault-injection self-test in the same change, then pass a new full
`./run-all.zsh` run before a new `YES` is claimed (`AGENTS.md`).

## Verified sound during the audit

These areas were checked in both independent reviews and found correct; they are
not findings:

- **Inventory consistency** — case names extracted from `run-safe.zsh` (63),
  `run-env.zsh` (11), `qa-pty.py` `SCENARIOS` (28), and `tests/test_gate.py`
  (32) match `coverage.json` exactly, with no missing, unexpected, or duplicate
  names; the `fzf` inventory matches the target's four cases.
- **Verdict contract** — `decide()` fails closed; timeouts become `fail`, skips
  become `incomplete`, dirty/changed targets and harness changes force
  `NO`/`INCOMPLETE`; focused and `--offline` runs return exit 2 with
  `INCOMPLETE`. Reproduced directly for offline mode and negative CLI paths.
- **Fresh full gate** — run `20260912-193357-431ba0a8` on the audited tree:
  `YES`, exit 0, `full_coverage=true`, `target_unchanged=true`,
  `cleanup_errors=[]`, harness before == after; 63/11/4/32 safe/env/fzf/selftest
  case rows and 28+28 PTY rows, all `pass`; no `QA_*` credentials or processes
  remained afterwards.
- **Echo traps** — `sync()` and `check()` markers cannot be satisfied by
  terminal echo (`${:-SYNC}` / `${:-RESULT}` expansion), and the ZLE buffer
  probe reads `$BUFFER`, not prior screen text.
- **Effect checks** — `fkill-signal` verifies the real `-SIGTERM`, `zi-select`
  verifies `/proc/<pid>/cwd`, `fbr-select` verifies `.git/HEAD`, Nix scenarios
  verify profile JSON with return-code checks and effect polling, and credential
  scenarios verify backend absence with `secret-tool lookup`.
- **Process identity and cleanup (base behavior)** — `/proc/stat` start-time
  matching correctly rejects PID reuse; killed zombies still hold their process
  group, so `killpg` does not hit an `ESRCH` race; the per-user `flock`
  (`LOCK_NB`, `O_NOFOLLOW`, per-uid path) is sound.
- **Isolation baseline** — `HOME`, `ZDOTDIR`, XDG paths, history, caches, and
  `compinit` state are per-run; `NO_COLOR`, `TERM`, `FZF_*`, `ZSH_UI_*`,
  `ZSH_FZF_*`, `_ZO_*`, `_ZSH_*`, `CGM_*`, `PYTHONOPTIMIZE`, and the common
  `GIT_*` overrides are replaced; session bus and `XDG_RUNTIME_DIR` retention is
  deliberate and needed for Secret Service.
- **Python 3.9 claim** — all five modules parse with
  `ast.parse(feature_version=(3, 9))`; `from __future__ import annotations`
  covers builtin-generic annotations; the only 3.9+ API is
  `Path.is_relative_to`.
- **Offline gating** — the curl cases and `upkg outdated/plan/upgrade
  --only=pacman` skip under `QA_SKIP_NETWORK`; `upkg search pacman` uses the
  local package database, so it is not an unintended network call. Nix/PTY may
  still use the network, as documented.
- **Self-test machinery** — `selftest.py` rejects an empty suite and `-O`;
  every unittest outcome is recorded; the 32 identities are exact.

## Documented limitations (not filed as issues)

- **Ignored target files are outside the fingerprint** — `snapshot()` uses
  `git ls-files --cached --others --exclude-standard`, so a change to an
  ignored file does not invalidate a run. This is the intended "release
  content" scope; note it when interpreting `target_unchanged`.
- **Non-git harness fallback is weaker** — when the harness has no `.git`,
  `harness_identity()` hashes only `.py/.zsh/.json/.md` files outside
  dot-directories, without modes or symlink targets, and has no test. The
  delivered repository is Git-based, where modes and symlinks are recorded.
- **`latest.json` is a pointer, not a verdict source** — it can point at an
  `INCOMPLETE` or `NO` run and can be overwritten by a focused run; only
  `report.json` carries the verdict.
- **`fzf-warm-start` depends on `fzf-cold-start` order** — running only the
  warm scenario fails without a cache rather than skipping. The full matrix
  always runs cold first.
- **`qa-pty.py` answers only two terminal queries** (cursor position and
  bracketed paste). A future fzf querying more would time out loudly, never
  pass silently.
- **Unknown `qa-pty.py` CLI flags are ignored** — only `--list` is meaningful;
  typos in scenario selection silently fall back to the full matrix.
- **`QA_RESULTS_FILE`/`QA_WORK_DIR` are visible to child shells** — a target
  could in principle touch the ledger, but exact-set, duplicate, and status
  validation constrain what could be recorded; no such behavior exists today.
- **`regression` assertion evidence is line-prefix based** (`ok: ` count) — the
  target's own runner owns the meaning of its output; the harness checks exit 0
  plus a nonzero assertion count.
- **Run reports are local-only** — `.runs/` evidence (paths, tool versions,
  process metadata) must not be attached to public services or committed; this
  report only commits per-issue documentation and the workflow.

## Reproduction and evidence policy

- All reproductions were executed under `/tmp/opencode` with the target config
  checkout read-only. Nothing in `~/.config/zsh` was modified; its Git status
  was empty before and after.
- No full gate stage, Nix/network operation, or credential operation was run by
  the reproduction workstreams; the one full gate run cited above predates the
  filing and was executed as part of the audit itself.
- Drafts and raw sandbox outputs remain at `/tmp/opencode/audit-docs/` and
  `/tmp/opencode/audit-r1|r2/` on the audit machine; the issue files are the
  curated, self-contained record.

## Filing new issues

Use [WORKFLOW.md](WORKFLOW.md). Allocate the next free `ISSUE-NNNN` (currently
`0036`), file exactly one document per issue, add it to the table above, and
label confidence honestly. Refuted candidates stay documented with
`Status: Refuted` and are never re-numbered.
