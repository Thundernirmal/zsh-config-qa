# Local Zsh release gate

A private, local test project for deciding whether a Zsh configuration revision is ready to release. It runs independently of GitHub Actions and writes nothing to the configuration repository.

Run the full gate from a normal logged-in terminal:

```sh
cd /home/nirmal/projects/zsh-config-qa
./run-all.zsh
```

The default target is `~/.config/zsh`. To test another checkout:

```sh
./run-all.zsh --repo /path/to/zsh-config
```

Every isolated shell sources that exact checkout, including all PTY scenarios. Your `.zshrc`, aliases, color settings, history, credential catalogue, and Nix profile are not used as test state. The runner retains your PATH and session bus to reach installed tools and Secret Service.

## Read the verdict

The last lines name the verdict and a saved report. Exit codes are suitable for scripts:

| Verdict | Exit | Meaning |
|---|---|---|
| `YES` | 0 | Full required coverage passed, both interactive passes succeeded, cleanup was verified, and the target was clean and its before/after fingerprints matched. |
| `NO` | 1 | A check failed, timed out, produced missing/duplicate/malformed evidence, changed the target, or could not clean up. Inspect the report. |
| `INCOMPLETE` | 2 | The run was partial, offline, interrupted, skipped a required scenario, used fewer than two PTY repetitions, or tested a dirty target. It does not approve a release. |

Configuration/argument errors also return nonzero. No exit code other than 0 approves release. A prior `YES` applies only to the recorded commit, content fingerprint, machine, and tool versions; run again after changes. A failing run is never automatically retried into a green result.

The report records the harness fingerprint, target identity, machine, executable hashes, and tool versions. Changing the harness during a run invalidates the result too.

The report lives in `.runs/<timestamp>-<id>/report.md`, with a machine-readable `report.json`. `.runs/latest.json` points to the most recently completed run. Logs and per-case stdout/stderr are retained beside the report. Evidence is local-only and gitignored; run directories are private to your user. A half-written/interrupted run starts with an `INCOMPLETE` report, never a stale success.

## What runs

1. **Harness self-tests:** fault injection proves failed exit codes, broken pipelines, startup diagnostics, empty output, timeouts, malformed inventories, invalid profiles, and echoed command text cannot pass. These tests deliberately run failing fixtures; their own expected outcomes are recorded separately.
2. **Configuration regressions:** the selected checkout's ordered `scripts/run-tests.zsh` runs locally.
3. **Fixtures:** fresh files, archives, Git repositories, navigation targets, and a local pull/rebase remote.
4. **Command sweep:** 63 live checks with preserved exit status, pipeline failure propagation, startup diagnostics, and retained per-case evidence.
5. **Environment matrix:** built-in and custom themes, glyph tiers, locale fallback, and plain output. Inherited `NO_COLOR`, `TERM=dumb`, finder options, and theme settings cannot silently change the baseline; cases opt into alternate settings explicitly.
6. **Real fzf PTY suite:** the selected checkout's terminal test runs against the installed fzf.
7. **Interactive matrix, twice:** startup and NO_UNSET, cold/warm caches, unsupported-fzf gating, layouts, inherited options, widgets, completion, help queueing, branch selection, navigation, process selection, Nix add/remove, and both rich/plain credential round trips.

Insertion and queueing checks read the actual ZLE edit buffer through a test-only widget; they do not trust the final picker redraw.

`coverage.json` is the required case inventory. Missing, duplicate, or unexpected results fail the gate even if a stage exits 0. Skipped optional integrations make full release verification incomplete; installing a binary without a working backend does not count as coverage.

## Requirements and environment

Use GNU/Linux with Python 3.9+, Zsh, Git, fzf 0.68.0+, and the configuration's normal dependencies. The live suite uses installed tools, coreutils, a reachable network, a working Nix daemon plus `jq`, and an unlocked Linux Secret Service plus `secret-tool`. The Pacman checks require Pacman on this host; another host without it reports incomplete coverage until an explicit equivalent is added to the private harness. No dependencies are downloaded or installed automatically by the runner.

Run as your normal user, not root. The gate does not invoke sudo or unlock a keyring. Nix may download/build packages into its store during isolated profile testing. Network, daemon, or keyring failures are reported with logs instead of silently skipped. Install/check the necessary local services before expecting `YES`.

The terminal baseline is UTF-8, `xterm-256color`, and truecolor, with inherited finder/theme/color settings removed. Every test HOME, XDG config/cache/data/state directory, history file, and compinit cache belongs to the run. The real session bus and runtime directory remain available for Secret Service.

## Isolation and cleanup

- Each invocation creates a new uniquely named directory. Old state is never reused as passing evidence.
- Fixture scripts require a matching ownership marker and reject unsafe paths. They cannot be pointed at an arbitrary directory for recursive deletion.
- A per-user lock prevents concurrent gates from racing shared services.
- All PTYs and bounded command groups are registered. The Linux runner adopts orphaned descendants so a crashed shell cannot leave them outside normal cleanup. Timeouts and ordinary termination signals trigger cleanup.
- Credential names are unique, valid uppercase names scoped to the run. Only synthetic values are stored; value checks use executed command status rather than echoed text. The name is registered before storage so the runner can remove it even if a prompt is interrupted. Both rich and plain prompts are tested. No real credential name or value is reused.
- Nix changes only a profile beneath the run's isolated HOME. The test never modifies your profile or performs system-wide garbage collection. Downloaded store paths may remain for normal Nix garbage collection.
- The process test starts and targets its own dummy process; it checks the actual termination signal.
- Run artifacts are intentionally retained for debugging. Delete old `.runs/` entries when you no longer need the evidence.

SIGKILL, power loss, and machine crashes cannot execute cleanup code. After an interrupted run, retry its registered cleanup before deleting evidence:

```sh
./run-all.zsh --cleanup /absolute/path/to/.runs/<run-id>
```

Cleanup checks only processes whose PID and creation identity still match and only credentials registered in that run's unique namespace. Failure to verify cleanup returns nonzero. Keep the report if a service is unavailable and retry when it recovers.

## Focused development runs

```sh
./run-all.zsh selftest
./run-all.zsh safe env       # fixtures added automatically
./run-all.zsh pty            # fixtures added automatically; all PTY cases twice
./run-all.zsh --offline      # useful diagnostics; never a release approval
./run-all.zsh --repeat 3     # extra complete interactive repetitions
./run-all.zsh --stage-timeout 3600
./run-all.zsh --results-dir /some/private/local/directory
```

Focused and offline runs deliberately return `INCOMPLETE` when their selected checks pass. Stage timeout defaults to 30 minutes; individual commands are bounded too. Slow progress is printed every 30 seconds. Do not run the internal fixture/sweep scripts directly: they require the launcher-provided owned directory and evidence paths.

## Maintaining the harness

Add assertions about observable effects, not just banners or nonempty diagnostic output. Verify negative cases before trusting a new test. When adding/removing scenarios, update `coverage.json` intentionally and run the full gate. Keep mutating cases limited to owned state and register resources before creating them. Do not mask failures with `|| true`, trust cached probe files, turn backend errors into empty data, or match success markers that appear in echoed commands.

Source files:

- [AGENTS.md](AGENTS.md): architecture and maintenance rules for coding agents.
- `release.py`: orchestration, locking, target identity, verdicts, and recovery.
- `qa_common.py`: owned paths, isolated environments, bounded commands, and case evidence.
- `selftest.py`, `tests/`: fault-injection checks for the test machinery.
- `setup-fixtures.zsh`, `run-safe.zsh`, `run-env.zsh`: fixtures and command/environment cases.
- `qa-pty.py`: real interactive scenarios and effect checks.
- `coverage.json`: required case identities.

## Limits of a YES

`YES` means this local automated release gate passed; it is not a guarantee for every distribution, terminal emulator, future tool version, or hardware device. Font shaping and perceived contrast require human judgment and are not claimed. System-wide package upgrade/cleanup, real credential operations, and bulk/SIGKILL against user processes are never performed. Their guarded behavior is covered by the configuration's isolated regression fixtures. The `fanprofile` live sweep tests its help contract; platform-specific hardware reads remain environment-dependent.

This repository does not publish anything, create releases, or change the target's Git state.
