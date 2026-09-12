# ISSUE-0027: Pre-stage setup failures escape the report/recovery envelope

- **Status:** Fixed
- **Severity:** Low
- **Category:** fail-closed
- **Affected:** `release.py:254` (`before = snapshot(repo)` before the run directory and before the `try`), `release.py:26-41` (`snapshot` with `check=True`), `release.py:246` (only `init.zsh` is validated), `release.py:274` (`coverage.json` parse), `release.py:277-279` (`tool_metadata`)
- **Confidence:** Confirmed by execution (non-git target); inspection for the other paths
- **Filed:** 2026-09-12 against harness `60f633a` plus the pending audit working tree
- **Reviewed:** 2026-09-12 against harness `7e97581`; confirmed with scope clarification

## Review disposition

Confirmed at Low severity. Invalid target/setup arguments are explicitly
allowed to fail before a report, so a non-Git target does not itself violate
the verdict contract; it should receive a concise validation error rather than
a traceback. Once an owned run directory exists, initialization failures
should either be captured in the initial `INCOMPLETE` report or remove/identify
the reportless allocation. This is diagnosability debt, not a false-approval
path.

## Summary

`main()` validates only that `--repo/init.zsh` exists, then calls
`snapshot(repo)` and builds the report metadata before entering the `try` block
that owns the report and cleanup paths. A target that is not a Git repository,
an empty `git init`, a repository without commits, or unreadable target files
raises `CalledProcessError`/`PermissionError` from `snapshot()` and aborts with
a raw traceback: no run directory, no marker, no `report.json`, no `INCOMPLETE`
report, and no `--cleanup` breadcrumb.

`tool_metadata()` has the same shape after the work directory exists: a tool
that disappears between `shutil.which()` and `read_bytes()` (`release.py:61-62`)
crashes before the initial report is written, leaving a marked run directory
without a report.

The verdict contract explicitly allows argument/setup errors to return nonzero
before a report exists (`AGENTS.md`), so the defect is not the nonzero exit; it
is the undiagnosable traceback and the reportless run directory left behind.

## Impact

- Operator UX: a mistyped `--repo` or a checked-out snapshot without Git gets a
  Python traceback instead of a clear "target is not a Git checkout" message.
- A reportless run directory does not carry the initial `INCOMPLETE` verdict the
  README promises for half-written runs, so a later inspection of `.runs/` cannot
  tell what happened without the original terminal output.
- No false approval path: all failures are nonzero.

## Root cause

Target validation stops at `init.zsh`; repository/identity probing and tool
metadata collection run outside the report envelope and outside the
`try/except/finally` that guarantees report finalization and cleanup.

## Reproduction

```sh
python3 - <<'PY'
import sys, pathlib, tempfile
sys.path.insert(0, '/home/nirmal/projects/zsh-config-qa')
import release
plain = pathlib.Path(tempfile.mkdtemp(prefix='plain-', dir='/tmp/opencode'))
(plain / 'init.zsh').write_text(':\n')
try:
    release.snapshot(plain)
except Exception as error:
    print(type(error).__name__, error)
PY
```

### Observed

```text
CalledProcessError Command '['git', '-C', '/tmp/opencode/plain-...', 'rev-parse', 'HEAD']' returned non-zero exit status 128.
```

`main()` reaches this same call at `release.py:254` before creating the work
directory at `:262`, so the process exits with a traceback and writes no report.

## Expected behavior

- The target is validated as a usable Git checkout by a bounded, explicit check
  before any run state is created; failures produce a concise argument error
  (`parser.error`, exit 2) rather than a traceback.
- Alternatively, non-Git targets are fingerprinted with the existing fallback
  and the run is marked `INCOMPLETE`/unsupported with a report.
- `tool_metadata()` failures are converted into report data (a tool entry with
  an error note) or caught so the initial `INCOMPLETE` report and cleanup path
  always exist.

## Proposed fix

```python
if not (repo/'init.zsh').is_file():
    parser.error('target does not contain init.zsh')
probe = bounded(['git','-C',str(repo),'rev-parse','--verify','HEAD'], timeout=30)
if probe.returncode != 0:
    parser.error(f'target is not a Git checkout: {repo} ({probe.stderr.strip()})')
```

and move `coverage` loading, `tool_metadata`, and the initial report write inside
the `try` (or wrap each in its own error handling) so later failures still land
in `report.json`.

## Test plan

- CLI test: `--repo <plain dir with init.zsh>` exits 2 with a message and no
  traceback; no run directory is created.
- Variants: empty `git init`, no-commit repository, unreadable `init.zsh`.
- Unit test with a tool path that disappears before `read_bytes` asserts the
  report is still written with an error entry, not a traceback.
- Add named self-test identities to `coverage.json` in the same change.

## Fix (2026-09-12, batch 2)

- **Status change:** Open → Fixed.
- `release.require_git_checkout()` validates the target with a bounded
  `git rev-parse --verify HEAD` (using the redirect-proof `git_env()`, and
  converting a missing `git` binary into a `ValueError`) before any run
  directory, marker, or snapshot exists; `main()` turns failures into
  `parser.error` (exit 2, no traceback).
- `tool_metadata()` was rewritten to never raise: each tool records
  `{'available': True, 'error': ...}` when its path cannot be resolved/read, so
  the initial `INCOMPLETE` report and cleanup path always exist.
- `coverage.json` parsing/validation now happens before the results root is
  created, so an invalid inventory can no longer leave a marked, reportless
  run directory; a stale inherited `QA_WORK_DIR` is popped before any probe so
  `bounded()` cannot register into a foreign run's ledger.
- Fault-injection tests: `test_non_git_target_is_rejected_before_run_state`
  (plain directory and empty/no-commit repository raise; initialized checkout
  passes) and `test_tool_metadata_reports_tool_errors_instead_of_raising`.
- Verified: full gate `20260912-205801-76cafce0` = `YES`, exit 0.

## Post-remediation audit (2026-09-12)

- **Status change:** Fixed → Open. The non-Git target path is closed: a bounded
  `require_git_checkout()` runs before any run state and converts failures into
  `parser.error` (exit 2, no traceback), verified live for a plain directory and
  an empty `git init`; `tool_metadata()` never raises. Three residues remain, all
  reproduced:
  1. **Unreadable target files still trace back before any report.** The
     Summary explicitly names "unreadable target files". `snapshot()` reads each
     tracked file with `path.read_bytes()` (`release.py:55`) and runs before the
     work directory/report envelope (`release.py:388`). A tracked file with mode
     `000` produced `PermissionError`, a raw traceback, exit 1, and no run state
     (executed against a sandbox repository). See ISSUE-0032: `snapshot()` is
     also unbounded, so the same call can hang instead.
  2. **Failures after the run directory exists still leave a marked, reportless
     run.** `make_home()` (`release.py:405`), `clean_env()`, the coverage
     re-parse (`release.py:415`), the initial report write (`release.py:421`),
     and `harness_identity()` (`release.py:419`) all run before the `try` block
     at `release.py:433`. A fault-injected `make_home` failure produced a
     traceback and left `.runs/<run>/.qa-owned.json` with no `report.json` and
     no cleanup - exactly the state the review disposition required to be
     "captured in the initial `INCOMPLETE` report or remove/identify the
     reportless allocation".
  3. **The "stale `QA_WORK_DIR` is popped before any probe" claim is
     inaccurate.** `os.environ.pop('QA_WORK_DIR')` runs at `release.py:387`,
     after `require_git_checkout()` at `release.py:370`. With an inherited
     `QA_WORK_DIR`, the bounded probe registers a row into that foreign run's
     `processes.jsonl` (observed); if the stale path does not exist, the probe
     misreports a usable checkout as "git is not available"
     (`release.py:35-36`) and exits 2.
- **Required remediation:** make `snapshot()` failures concise
  (`parser.error`/recorded `snapshot_error`) and bounded per ISSUE-0032, move the
  run-directory/report envelope so post-allocation failures always produce an
  `INCOMPLETE` report and cleanup, and pop `QA_WORK_DIR` before
  `require_git_checkout()`.

## Re-fix (2026-09-12, batch 7)

- **Status change:** Open → Fixed.
- All three residues addressed:
  1. **Unreadable targets:** `snapshot()` failures now surface through
     `parser.error` (exit 2, no traceback) — and with the ISSUE-0032 fix the
     calls are bounded, so a hang becomes a deadline failure instead.
  2. **Reportless allocations:** every post-marker setup step
     (`verify_work`, harness identity capture, `make_home`, `clean_env`,
     `tool_metadata`, the initial report write, and signal-handler install)
     now runs inside the `try`; the report dict is pre-initialized so the
     `finally` always writes the report and runs cleanup. Harness capture
     errors are recorded with before/after-distinguishing error strings so the
     identity comparison fails closed on any one-sided capture failure.
  3. **Stale `QA_WORK_DIR`:** the pop now happens immediately after the lock is
     taken, before `require_git_checkout` and any `bounded()` call, so no probe
     can register into a foreign run's ledger or misreport a usable checkout.
- Verified: full gate `20260912-223424-da8ad654` = `YES`, exit 0.

## Reopen (2026-09-12, third independent verification)

- **Status change:** Fixed → Open (narrow envelope residues).
- Re-verified at harness HEAD `7aafe90` (clean tree). Closed and confirmed:
  non-git/empty/no-commit targets fail before run state with `parser.error`
  (exit 2, no traceback); unreadable tracked files now surface through
  `parser.error`; a stale inherited `QA_WORK_DIR` is popped before
  `require_git_checkout`; an in-try `make_home` failure produces a `NO` report
  with `cleanup_errors=[]` and a successful `--cleanup`.
- The second-opinion requirement — post-allocation failures always produce an
  `INCOMPLETE` report and cleanup — is still not met for the allocation
  envelope that runs before `try:` at `release.py:436`:
  1. **A marker-write failure strands an unidentified, unrecoverable run
     directory.** `work.mkdir(mode=0o700)` (`release.py:418`),
     `atomic_json(work/MARKER, ...)` (`release.py:419`), and the report-dict
     initialization (`release.py:423`) precede the `try`. In a sandbox copy
     with the real lock path (`/tmp/opencode/verify-0027`), an injected
     `atomic_json` failure produced a raw `OSError` traceback at
     `release.py:419` and left `.runs/<run>` (mode 0700) with no marker and no
     report; `./run-all.zsh --cleanup <dir>` then refuses with
     `cannot clean up ...: [Errno 2] No such file or directory:
     '.../.qa-owned.json'` (`release.py:349-353`). Only manual `rmdir`
     recovers it.
  2. **A results root that exists but is not writable passes validation and
     then traces back.** `prepare_results_root()` (`release.py:342-346`)
     checks only mode bits; a mode-0500 root passes, then `work.mkdir`
     (`release.py:418`) raises an uncaught `PermissionError` — raw traceback,
     exit 1, no report.
  3. **Unreadable/missing/malformed `coverage.json` still traces back.**
     `load_validated_coverage(...)` at `release.py:401` is outside every
     handler, so a missing or unreadable inventory produces a raw `ValueError`
     traceback and exit 1 rather than the documented setup-error path. The
     ISSUE-0001 re-fix text calls this "the documented `ValueError`/exit-2
     path"; the exit-2 conversion is not present in the tree.
- The test plan ("Add named self-test identities to `coverage.json` in the same
  change") remains unmet for the envelope: no test calls `release.main()` or
  injects an allocation-time failure; the existing tests cover only the
  pre-allocation target checks and `tool_metadata`.
- **Required remediation:** put `work.mkdir`/marker/report initialization inside
  the report envelope (or clean up and identify a reportless allocation),
  pre-check results-root writability, convert inventory-load failures to
  `parser.error`, and add a fault-injection test that injects a marker-write
  failure and asserts either a report exists or no unmarked allocation remains.

## Re-fix (2026-09-12, batch 8)

- **Status change:** Open → Fixed.
- All three residues closed:
  1. The whole allocation envelope (`work.mkdir`, marker write, `QA_WORK_DIR`,
     report initialization, the banner print, signal handlers, `verify_work`,
     harness capture, `make_home`, `clean_env`, `tool_metadata`, and the
     initial report write) now runs inside the report `try`. The report dict
     is pre-initialized and the `finally` always runs cleanup and finalizes:
     a marker-write failure yields a `NO` report and the unmarked allocation
     is removed (`shutil.rmtree` only when the marker never landed; a pointer
     is never written for a removed run). Fault-injection test:
     `test_allocation_failure_leaves_a_report_or_nothing` drives `release.main()`
     in a subprocess with the lock neutralized and a sandbox results root,
     injecting the failure at the marker write (verified in review's sandbox
     and by the delivered test).
  2. `prepare_results_root()` rejects a root the user cannot write via
     `os.access`, so the mode-0500 root now fails with `parser.error` (exit 2)
     before any allocation.
  3. `coverage.json` load failures convert to `parser.error` (exit 2, no
     traceback).
- Verified: full gate `20260912-235724-019e9c8b` (`YES`, exit 0); review sandbox verified all three residues plus
  the injected marker failure path end to end.
