# ISSUE-0027: Pre-stage setup failures escape the report/recovery envelope

- **Status:** Open
- **Severity:** Low
- **Category:** fail-closed
- **Affected:** `release.py:254` (`before = snapshot(repo)` before the run directory and before the `try`), `release.py:26-41` (`snapshot` with `check=True`), `release.py:246` (only `init.zsh` is validated), `release.py:274` (`coverage.json` parse), `release.py:277-279` (`tool_metadata`)
- **Confidence:** Confirmed by execution (non-git target); inspection for the other paths
- **Filed:** 2026-09-12 against harness `60f633a` plus the pending audit working tree

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
