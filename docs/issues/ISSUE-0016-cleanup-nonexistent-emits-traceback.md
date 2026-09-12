# ISSUE-0016: `--cleanup /nonexistent` emits a raw Python traceback

- **Status:** Fixed
- **Severity:** Low
- **Category:** hygiene
- **Affected:** `release.py:236-241`, `release.py:238`
- **Confidence:** Confirmed by execution
- **Filed:** 2026-09-12 against harness `60f633a` plus the pending audit working tree
- **Reviewed:** 2026-09-12 against harness `7e97581`; confirmed actionable as filed

## Review disposition

Confirmed. The recovery entry point still lets marker/path exceptions escape
as tracebacks. This is strictly operator-facing error handling and has no
approval impact, so Low remains correct.

## Summary

The `--cleanup` path trusts the caller-supplied directory:

```python
if args.cleanup:
    work = args.cleanup.absolute()
    marker = json.loads((work/MARKER).read_text())
    verify_work(work, Path(marker['repo']))
```

(`release.py:236-239`). A nonexistent path, or one whose `.qa-owned.json` is missing,
unreadable, or malformed JSON, raises `FileNotFoundError`, `PermissionError`, or
`json.JSONDecodeError` out of `main()`. Python prints a full traceback to stderr and exits
`1`, with no operator-facing message and no report/verdict line.

## Impact

- Operator UX: the documented recovery command (`README.md:69-75`) fails with an
  implementation traceback instead of a clear diagnostic such as "not an owned run
  directory".
- The same path also skips `verify_work()`'s symlink/marker/ownership checks because the
  crash happens while reading the marker (the error is raised before validation).
- Exit code `1` happens to match the `NO` code, but nothing in the output says whether this
  was a cleanup result or a crash. Scripting around the gate sees an undiagnosable failure.

## Root cause

No exception handling around marker read/JSON decode for the recovery entry point.
`argparse` cannot validate the filesystem state, and `main()` treats the argument as
trusted.

## Reproduction

Run from the harness root (the wrapper only `exec`s `release.py`, `run-all.zsh:4`; the
gate's own stages were not started by `--cleanup`):

```sh
cd /home/nirmal/projects/zsh-config-qa
python3 release.py --cleanup /nonexistent; echo "exit=$?"
```

The equivalent user-facing command is `./run-all.zsh --cleanup /nonexistent`; it produces
the identical traceback because of the `exec` in `run-all.zsh:4`.

### Observed

stderr:

```text
Traceback (most recent call last):
  File "/home/nirmal/projects/zsh-config-qa/release.py", line 349, in <module>
    sys.exit(main())
             ~~~~^^
  File "/home/nirmal/projects/zsh-config-qa/release.py", line 238, in main
    marker = json.loads((work/MARKER).read_text())
                        ~~~~~~~~~~~~~~~~~~~~~~~^^
  File "/usr/lib/python3.14/pathlib/__init__.py", line 787, in read_text
    with self.open(mode='r', encoding=encoding, errors=errors, newline=newline) as f:
         ~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.14/pathlib/__init__.py", line 771, in open
    return io.open(self, mode, buffering, encoding, errors, newline)
           ~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
FileNotFoundError: [Errno 2] No such file or directory: '/nonexistent/.qa-owned.json'
```

Exit status:

```text
exit=1
```

## Expected behavior

`--cleanup` with an unusable path reports a concise error (for example,
`not an owned run directory: /nonexistent`) and returns a nonzero usage/configuration
code without a traceback. The same applies to a malformed marker JSON and to
`verify_work()` rejections, which should surface as messages rather than exceptions.

## Proposed fix

Wrap marker loading and validation in a `try/except` in `main()`:

```python
if args.cleanup:
    work = args.cleanup.absolute()
    try:
        marker = json.loads((work/MARKER).read_text())
        verify_work(work, Path(marker['repo']))
    except (OSError, ValueError, KeyError) as error:
        parser.error(f'cannot clean up {work}: {error}')
```

`parser.error` prints `usage: ...` + message to stderr and exits `2`, matching the
"argument/setup errors" contract (AGENTS.md verdict table: exit 2 before a report).

## Test plan

- CLI test: invoke `release.py --cleanup <missing path>` and assert no `Traceback`, a
  message containing the path, and exit code `2`.
- Variants: directory present without marker; marker containing `not json`;
  `verify_work` rejection due to wrong repo.
- No live stage or cleanup operation is triggered by these tests.

## Fix (2026-09-12, batch 3)

- **Status change:** Open → Fixed.
- New `release.validate_cleanup_target(work)` performs the marker read, JSON
  parse, and `verify_work`; `main()`'s `--cleanup` branch wraps it and converts
  every failure (`FileNotFoundError`, `JSONDecodeError`, `KeyError`,
  `TypeError`, `verify_work` rejections) into `parser.error(f'cannot clean up
  {work}: ...')` — exit 2 with a concise message and no traceback.
- Fault-injection test: `test_cleanup_rejects_unusable_path_cleanly` (missing
  path → `OSError`; malformed marker → `ValueError`). The originally planned
  subprocess variant cannot run under a live gate because `main()` holds the
  per-user lock before the cleanup branch, so the exception types are asserted
  on the helper and the `parser.error` wiring is verified by review.
- Verified: full gate `20260912-210646-ff36a6ad` = `YES`, exit 0.
