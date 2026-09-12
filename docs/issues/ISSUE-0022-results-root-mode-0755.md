# ISSUE-0022: Results root `.runs/` is world-readable (0755) while run directories are 0700

- **Status:** Fixed
- **Severity:** Low
- **Category:** isolation
- **Affected:** `release.py:259-262`
- **Confidence:** Confirmed by execution
- **Filed:** 2026-09-12 against harness `60f633a` plus the pending audit working tree
- **Reviewed:** 2026-09-12 against harness `7e97581`; partially confirmed with fix constraint

## Review disposition

Confirmed as Low privacy hardening: the default/new results root and pointer do
inherit umask-dependent broad modes, although the actual run evidence remains
protected by its mode-0700 parent. The proposed unconditional `chmod` must not
be applied to an existing caller-selected directory, because changing the
permissions of an operator-owned shared location is outside the harness's
ownership. Create private new roots and validate, reject, or clearly document
existing custom roots instead.

## Summary

The run directory is created private:

```python
root.mkdir(parents=True, exist_ok=True)          # default mode, filtered by umask
ident = uuid.uuid4().hex
work = root / (...)
work.mkdir(mode=0o700)
```

(`release.py:259-262`). With the default umask `022`, `root` (`.runs/`) becomes `0755`
while each `work` directory is `0700`. `.runs/latest.json` is then created inside the
world-readable root with default mode `0644`.

`--results-dir` accepts any directory, so custom result roots get the same default-mode
treatment.

## Impact

- Other local users can list `.runs/` and read `latest.json`, which contains the absolute
  path of the most recent run and its verdict. Run names (timestamp + ID prefix) and the
  verdict history are exposed even though the run directories themselves are private.
- Contents of run directories (`report.json`, `report.md`, logs, case evidence) remain
  protected by the `0700` mode, so this is an information-disclosure/hygiene issue, not a
  direct evidence breach.
- The mode depends on the operator's umask; with umask `000` the root would be `0777`.
  The harness documents run state as "private to your user" (README:34), which is not
  fully true for the root pointer.

## Root cause

`root.mkdir()` does not pass `mode=0o700`, and no later `chmod`/check tightens an
existing results root. `atomic_json` writes `latest.json` with default file mode.

## Reproduction

```sh
cd /home/nirmal/projects/zsh-config-qa
stat -c '%a %U:%G %n' .runs .runs/latest.json .runs/20260912-193557-75eac6f5 .runs/20260912-193557-75eac6f5/report.json
```

### Observed

```text
755 nirmal:nirmal .runs
644 nirmal:nirmal .runs/latest.json
700 nirmal:nirmal .runs/20260912-193557-75eac6f5
644 nirmal:nirmal .runs/20260912-193557-75eac6f5/report.json
```

(`report.json` is readable only because the parent directory is `0700`; its own mode is
not the protection.) The run directory was created by a real invocation on
2026-09-12 with the default umask `022`.

## Expected behavior

Run state should be private to the user at every level: the results root should be
`0700` (or an existing root should be checked and rejected/warned if broader), and
`latest.json` should be `0600`. The mode should not depend on the operator's umask.

## Proposed fix

```python
root.mkdir(parents=True, exist_ok=True, mode=0o700)
os.chmod(root, 0o700)  # tighten an existing results root
```

and write `latest.json` with an explicit `0600` mode (extend `atomic_json` with a `mode`
parameter, or chmod after write before rename). Add a startup check that an existing root
is not a symlink and is owned by the current user.

## Test plan

- Unit/CLI test: create a results root with umask `022`, run allocation, assert `stat` mode
  of the root is `0700` and of `latest.json` is `0600`.
- Test with an existing `0755` root: assert the harness tightens it or refuses, rather than
  silently inheriting it.
- No `coverage.json` change unless a new named selftest is added.

## Fix (2026-09-12, batch 3)

- **Status change:** Open → Fixed.
- New `release.prepare_results_root(root)` creates missing roots with mode
  `0700` (umask-independent for owner bits) and **rejects** an existing root
  with group/other bits set (`parser.error`, actionable chmod 700 message) —
  it never chmods a caller-owned directory, per the review constraint.
  `latest.json` is written `0600`.
- README now documents the one-time requirement for an existing broad root.
- Fault-injection test: `test_results_root_must_be_private` (broad existing
  root → `ValueError` after explicit `chmod 755`, so the test is umask-proof;
  fresh root → `0700`).
- Verified: full gate `20260912-210646-ff36a6ad` = `YES`, exit 0 (the local
  `.runs/` root was tightened to `0700` as part of this fix).
