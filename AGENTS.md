# AGENTS.md

## Purpose and private-project boundary

This is a private, local release-verification harness for a separate Zsh configuration repository. Reliability and inspectable evidence take priority over runtime or code size. Read [README.md](README.md) for operator usage; this file defines the maintenance contract for coding agents.

- Keep all harness code, documentation, reports, and references in this private project. Do not add harness references, workflows, dependencies, links, or status notes to the configuration project or its public issues/PRs. Do not introduce GitHub Actions for these live tests.
- Treat the selected configuration checkout as read-only. Running or maintaining the harness does not authorize editing it, changing its branch, committing to it, or publishing a release. Diagnose target defects with evidence and report them separately.
- Preserve existing user changes. Inspect this repository's `git status --short` before editing. Do not commit, push, publish logs, or create PRs unless requested.
- Work from this repository's root. The entrypoint is `./run-all.zsh`; do not confuse it with the target's `scripts/run-tests.zsh`.
- Do not weaken a test or verdict to make a run green. A slow, clearly explained failure is preferable to an unsupported release approval.

## Repository shape and runtime

The implementation uses Python's standard library and Zsh. There is no Python package environment, package manager, web application, or root build system. Avoid adding frameworks or third-party dependencies without a concrete need.

The platform is GNU/Linux: PTYs, `/proc`, process groups, `fcntl` locks, and Linux `prctl` are architectural dependencies. Python 3.9+ and the installed command-line tools are described in the README. Keep Zsh scripts as Zsh; they use Zsh-specific expansion and options.

| File | Responsibility and important interfaces |
|---|---|
| `run-all.zsh` | Minimal wrapper; executes `release.py` with the caller's arguments and preserves its exit code. |
| `release.py` | Stage selection/order, per-user lock, run allocation, target/harness fingerprints, tool metadata, stage execution, exact evidence validation, verdicts, reports, signal handling, and recovery. Key seams: `snapshot`, `harness_identity`, `read_results`, `decide`, `run_stage`, `cleanup`. |
| `qa_common.py` | Shared primitives: `verify_work`, `make_home`, `clean_env`, `bounded`, `register_process`, `credential_name`, `record`, and `run_case`. Its CLI serves the shell sweeps. |
| `setup-fixtures.zsh` | Creates owned scratch files, archives, disposable Git repositories, a local pull/rebase remote, and zoxide navigation data. |
| `run-safe.zsh` | Live command contracts through `qa`, `qa_nz`, `qa_opt`, `qa_fixture`, and `qa_skip`. Each command case delegates execution and evidence to `qa_common.py`. |
| `run-env.zsh` | Theme, locale, glyph, custom-palette, and plain-output contracts through shared case execution. |
| `qa-pty.py` | Real interactive sessions. `Session` owns terminal synchronization and process lifetime; `SCENARIOS` and `SCENARIO_REQUIREMENTS` define interactive coverage. Includes ZLE buffer probes, Git/process/navigation effects, isolated Nix profiles, and synthetic Secret Service round trips. |
| `coverage.json` | Independently maintained required case identities for self-tests, safe/env sweeps, fzf, and PTY scenarios. This is an acceptance contract, not an observed-results cache. |
| `selftest.py` | Discovers unit tests and adapts unittest outcomes into one evidence record per test via `EvidenceResult`. Rejects an empty suite and disabled assertions. |
| `tests/test_gate.py` | Fault-injection tests for the harness itself: false successes, timeout/interrupt behavior, identity/path boundaries, cleanup, evidence inventories, profile errors, and terminal echo/buffer traps. |
| `README.md` | Operator entrypoint: installation assumptions, running, interpreting results, recovery, maintenance overview, and limitations. |
| `.gitignore` | Keeps `.runs/`, legacy `.work/`, Python bytecode, and caches local-only. |

Do not pin assertion totals or a historical commit as the architecture. Consult the current inventory and source when adding coverage. Old reports describe old runs, not current acceptance.

## Execution architecture

```text
run-all.zsh
  -> release.py
     -> lock + Linux subreaper
     -> selected target identity + owned run directory
     -> isolated HOME/environment + initial INCOMPLETE report
     -> ordered stages + case evidence
     -> resource cleanup + final identity checks
     -> report.json + report.md + latest.json + exit status
```

`ALL_STAGES` in `release.py` is the executable ordering contract:

1. `selftest`: prove the harness can detect broken behavior before trusting it.
2. `regression`: run the selected target's `scripts/run-tests.zsh` locally. Success requires exit zero and nonzero `ok:` assertion evidence.
3. `fixtures`: establish disposable inputs for subsequent live cases.
4. `safe`: execute command contracts in fresh child Zsh processes.
5. `env`: execute explicitly controlled environment cases.
6. `fzf`: run the target's `scripts/test-fzf-pty.py` against the installed binary and validate its named successes against the inventory.
7. `pty`: run the full interactive matrix, twice by default. Each repetition has a distinct stage log and ledger.

A failed self-test or fixture stage blocks dependent work. Other failed stages remain failures even if subsequent stages pass; cleanup is attempted before continuing. Focused selections are ordered by `ALL_STAGES`, and fixtures are automatically added for `safe`, `env`, or `pty`.

Do not add retries that erase a failed attempt. If a retry feature is ever needed, preserve every attempt and explicitly define how it affects the verdict. Never translate a timeout into a skip or a pass.

## Verdict contract

Keep `decide()` and the full-run checks consistent with the CLI and reports:

| Verdict | Exit | Required interpretation |
|---|---|---|
| `YES` | 0 | All stages and required cases passed, no required case skipped, at least two complete PTY passes, clean target, matching target before/after identities, unchanged harness identity, verified cleanup. |
| `NO` | 1 | Failed test/stage, timeout, invalid or missing evidence, target/harness change during the run, or cleanup that cannot be verified. |
| `INCOMPLETE` | 2 | No failure establishes `NO`, but coverage is partial/offline/interrupted, required cases skipped, fewer than two PTY repetitions ran, or the target was initially dirty. |

Argument/setup errors may also return nonzero before a report exists. An absent report, missing terminal result, or nonzero command is never release approval.

A `YES` is scoped to the recorded target content, harness content, machine, and installed tools. It is not a promise for every operating system, font, future package version, or hardware device. The target's cleanliness is required; harness edits may be tested before committing, but its fingerprint must remain unchanged during the run.

## Isolation and owned state

All test mutation must remain within an owned run, except the explicitly registered synthetic credentials and disposable processes:

- The default result root is this repository's `.runs/`. Each invocation allocates a new timestamp/UUID directory with mode `0700`; do not reuse old runs for acceptance.
- `.qa-owned.json` binds the run ID, absolute work path, and resolved target. Call `verify_work()` before fixture creation or deletion. Preserve its owner, symlink, path, target-match, and overlap checks.
- Resolve and validate output paths before writing. Never allow output inside the target or recursive cleanup against a caller-supplied arbitrary directory. Preserve the scratch-symlink rejection.
- `make_home()` creates `.config/zsh` pointing at the selected target and a controlled `.zshrc`. It rejects a conflicting existing config path. All PTYs must use owned HOME/ZDOTDIR; do not restore the former reliance on the user's real `.zshrc`.
- `clean_env()` owns the baseline. HOME and XDG config/cache/data/state paths must be private to the run. Finder/theme/color overrides, shell startup overrides, Git overrides, and Python optimization settings are removed or replaced as implemented. Retain PATH and the session bus/runtime settings needed for live installed tools.
- Alternate environments are explicit case inputs. Inherited `NO_COLOR=1` or `TERM=dumb` must not silently invalidate rich-output cases. Test plain mode separately.
- Fixture Git commands operate only in disposable repositories; Nix operations use the run's isolated profile. Do not introduce user-profile package changes, system upgrades, sudo, keyring unlocking, or automatic dependency installation.

Internal shell scripts and direct scenario execution require launcher-provided state. Do not fabricate ownership markers to bypass their checks. Unit tests may create markers only for freshly allocated test directories they own.

## Process and credential lifecycle

Use `bounded()` for external commands that need captured output and a deadline. Preserve real return codes, whole-process-group termination on timeout, and process registration. Long-running stage/PTY execution has its own ownership logic; do not replace it with unbounded shell calls.

The runner holds a per-user lock at `/tmp/zsh-config-qa-<uid>.lock` for normal runs and recovery. It uses `PR_SET_CHILD_SUBREAPER` so orphaned descendants can still be reaped. Cleanup considers actual owned descendants and registered PID/start-time identities. Never replace those checks with broad `pkill`, `killall`, or PID-only recovery that might target a reused PID.

Credential cases must:

1. Generate a unique uppercase `QA_<RUN_ID>_<SUFFIX>` name with `credential_name()`; never reuse a constant such as `QA_PTY_TEST`.
2. Register the name before storage, without writing the value into the registry.
3. Use only generated synthetic values. Wait for the backend input prompt and disabled terminal echo before sending input; never resend a value blindly after a timeout.
4. Verify actual load/unset effects with executed command status, then delete and independently verify backend absence.
5. Attempt scoped cleanup in `finally`; let the outer runner recover registered resources after interrupts.

Never retrieve real user credentials for testing, log values, or interpret an unavailable backend as successful cleanup. Catalogue state belongs to the isolated HOME. Backend values are the deliberate real-service boundary and must be removed.

Nix profile reads must validate command status and JSON structure. A daemon error or malformed JSON must not become `{}` and falsely prove package removal. Validate seed/add/remove effects and check cleanup commands. Retaining run artifacts and downloaded Nix store paths is different from modifying the user's profile; keep that distinction explicit.

For hard interruption recovery:

```sh
./run-all.zsh --cleanup /absolute/path/to/.runs/<run-id>
```

SIGKILL and power loss cannot execute handlers. Preserve the registry/evidence for later recovery. Cleanup failure is actionable failure; do not delete the evidence to hide it.

## Evidence and assertion rules

- A successful exit alone is insufficient for stages with an inventory. `read_results()` requires the exact expected set, with no missing, duplicate, or unexpected names and only recognized statuses.
- `record()` appends and flushes/fsyncs JSONL case records. Preserve name/status identity, useful failure detail, duration, and relevant exit/evidence fields. `pass`, `fail`, and `skip` are case statuses; stage aggregation may produce `incomplete`.
- Reports begin as `INCOMPLETE`, are updated atomically, and retain stage logs/case evidence. Keep `.runs/latest.json` as a pointer to a completed run, not a second editable verdict source.
- Command cases preserve the command exit code before cleanup. Startup stderr or a nonzero source status fails the case; do not hide startup diagnostics with `2>/dev/null`.
- Keep pipeline failure detection. When a producer must finish successfully, avoid `grep -q`/`head` as early-exiting consumers that can create incidental SIGPIPE; consume the output or capture it before inspecting it.
- Prefer observable effects and content assertions over banners or nonempty output. A diagnostic is not a successful result. `qa_nz` must check stdout, not combined stderr.
- PTY commands use `Session.check()` and unique executed markers; echoed input must not satisfy success. Preserve offset handling and fresh probe files.
- Verify file insertion and help queueing through `prepare_buffer_probe()`/`capture_buffer()`, which inspect the actual ZLE buffer. A final picker redraw is not proof of insertion.
- Verify Git HEAD, working-directory changes, exact dummy-process termination, profile contents, and credential state directly where available.
- Preserve recursive child detection, controlling-terminal setup, terminal query responses, and ZLE readiness checks. A fixed sleep alone is not proof that a prompt or picker is ready.
- Do not run Python with `-O`, bypass `__debug__` guards, suppress assertion failures, or substitute an empty test suite.

## Change workflow

Before implementing a change:

1. Read the relevant owner module, current README, `coverage.json`, and existing fault-injection tests.
2. Identify whether the defect belongs to the target, the harness, or the environment. Keep all three outcomes distinguishable.
3. Decide the observable assertion and failure evidence before extending the scenario.
4. Preserve narrow ownership boundaries. Share process/environment/evidence primitives through `qa_common.py`; keep orchestration in `release.py` and terminal mechanics in `Session`.

When adding or changing a case:

- Add/update the command or scenario in its owning file and the corresponding names in `coverage.json` in the same change.
- Add a fault-injection self-test when changing verdicts, evidence parsing, paths, process handling, cleanup, or synchronization. Update the self-test identities in the inventory as well.
- Update prerequisites and `SCENARIO_REQUIREMENTS` where relevant. A missing required live integration must remain visible as incomplete coverage.
- Review inventory changes independently. Never generate required names from a failing run's observed successes or shrink coverage to silence a mismatch.
- Keep shell fragments correctly quoted; use argv lists and `shlex.quote` at Python/shell boundaries. Treat shell text as executable code, not ordinary string interpolation.
- Use standard-library primitives and existing helpers unless a new abstraction has a specific tested responsibility. Avoid unrelated refactors or cosmetic churn while repairing a failure path.
- Keep README usage and this architecture guide synchronized with actual CLI/report/safety changes. Keep release histories and run-specific evidence in reports, not in these instructions.

## Verification commands

Run commands from the private harness root. Use targeted runs while developing, then the full gate for executable changes:

```sh
./run-all.zsh selftest
./run-all.zsh safe env
./run-all.zsh pty
./run-all.zsh
```

A passing focused run deliberately returns exit 2 with `RELEASE: INCOMPLETE`. Inspect its stage/case outcomes; do not change the exit contract or append `|| true` to mislabel it as release-ready. Full acceptance requires exit 0 and a final `YES` for the exact delivered files.

For slow local environments, `--stage-timeout` adjusts the outer stage limit; individual commands and PTY operations also have bounded waits. `QA_CASE_TIMEOUT` controls command-case deadlines. Do not claim the outer flag changes every inner timeout. The current `--offline` flag sets `QA_SKIP_NETWORK` for the safe sweep; other selected stages can still use the network (including Nix). It is not a network sandbox. Choose focused stages when external traffic must be avoided.

Check every shell file separately and validate Python syntax without disabling assertions:

```sh
for file in ./*.zsh; do
  zsh -n "$file" || exit "$?"
done
python3 -m py_compile release.py qa_common.py qa-pty.py selftest.py tests/test_gate.py
git diff --check
```

Do not use a single `zsh -n a.zsh b.zsh ...` invocation as proof that every file was parsed. Syntax checks do not replace fault-injection or live verification.

For documentation-only changes, verify referenced files, commands, contracts, and Markdown links against the source and run `git diff --check`. Do not trigger live package/credential operations solely to validate prose. Do not claim a new release signoff from documentation checks.

Do not edit either codebase while a full run is active; identity checks invalidate that run. After validation, report the actual stage/case results, skips, cleanup failures, and report path. Never reuse a previous `YES` as evidence for new executable changes.

## Evidence handling and operator handoff

`.runs/` and legacy `.work/` are disposable local artifacts, not source. Keep them and Python caches ignored. Reports can contain local paths, process metadata, and environment diagnostics; do not attach them to public services or commit them. Preserve useful failed runs until diagnosis and cleanup are complete.

When reporting a problem, include the exact target/harness identity, failing case or stage, observed exit/status, relevant local log path, and whether cleanup succeeded. Distinguish a confirmed configuration defect from missing services or a harness defect. A scoped, evidence-backed `NO` or `INCOMPLETE` is a valid outcome.

A local `YES` covers the defined automated scope. It does not evaluate perceived font/contrast quality, all distributions, hardware-specific fan behavior, or real system-wide destructive package operations. Do not broaden those claims when summarizing results.
