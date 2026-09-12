# ISSUE-0025: Ungated safe-sweep cases turn missing optional tools into NO instead of INCOMPLETE

- **Status:** Open
- **Severity:** Medium
- **Category:** contract-drift
- **Affected:** `run-safe.zsh:140` (`ports` needs `ss`), `run-safe.zsh:109-110` (`extract zip` needs `unzip`), `run-safe.zsh:147` (`zdoctor` needs `curl`, `lsd`, `ss`, `zoxide`), `run-safe.zsh:149` (`zdoctor --network --secrets` needs `curl`, `secret-tool`), `release.py:54-67` (tool metadata omits these)
- **Confidence:** Confirmed by execution for `zdoctor`/`ports`; inspection for `unzip` and `--secrets`
- **Filed:** 2026-09-12 against harness `60f633a` plus the pending audit working tree
- **Reviewed:** 2026-09-12 against harness `7e97581`; confirmed with proposed-fix correction

## Review disposition

Confirmed. Missing host integrations currently become indistinguishable case
failures even though the harness assigns missing live coverage to
`INCOMPLETE`. Direct command cases such as `ports` and archive extraction can
use explicit prerequisite skips. `zdoctor`, however, must not simply be gated
away by every dependency it is designed to diagnose; split its functional and
host-readiness assertions, or record prerequisite incompleteness separately,
so the diagnostic command still receives meaningful coverage.

## Summary

The harness's contract is that a host missing an optional integration records a
skip and the run reports `INCOMPLETE`, not `NO` (`README.md:48`, `AGENTS.md`
missing-integration rule). Several safe-sweep cases are ungated and therefore
turn the same missing tool into a failed case:

| Case | Hard requirement | Today |
|---|---|---|
| `ports` (`run-safe.zsh:140`) | `ss` (iproute2) | `command not found` → rc 127 → fail |
| `extract zip` (`run-safe.zsh:109-110`) | `unzip` | target `extract` returns 1 → fail |
| `zdoctor` (`run-safe.zsh:147`) | `curl`, `lsd`, `ss`, `zoxide` | rc 1 → fail |
| `zdoctor --network --secrets` (`run-safe.zsh:149`) | `curl`, `secret-tool` | rc 1 → fail |

Neighboring cases use `qa_opt`/`SCENARIO_REQUIREMENTS` for exactly these tools
(`weather`/`headers`/`myip` for curl, `cgm *` for secret-tool, `zoxide z jump`
for zoxide), so the skip policy is inconsistent within one sweep. None of
`ss`, `unzip`, `lsd`, or `curl` appears in `tool_metadata()` (`release.py:56`),
so a resulting `NO` report does not even name the missing tool.

## Impact

- A host that satisfies the documented requirements in `README.md:52` but lacks
  iproute2/unzip/lsd gets a `NO` (failed gate) where the documented behavior for
  missing integrations is `INCOMPLETE` coverage. Operators cannot distinguish
  "target regressed" from "tool not installed".
- The failure is conservative (no false approval) but violates the incomplete-
  coverage contract and wastes a full re-run once the tool is installed.

## Root cause

No `qa_opt` gate on cases whose target implementation hard-requires an external
tool, and the report's tool inventory was not updated when those cases were
added.

## Reproduction

```sh
# Isolated HOME pointing at the target, with a PATH that has zsh but not curl/ss/lsd/zoxide.
zsh -d -f -c 'source "$HOME/.config/zsh/init.zsh"; zdoctor; print -r -- "ZDOC-RC=$?"'
zsh -d -f -c 'source "$HOME/.config/zsh/init.zsh"; ports >/dev/null; print -r -- "PORTS-RC=$?"'
```

### Observed

```text
zdoctor: 6 failure(s), 11 warning(s)
ZDOC-RC=1
ports:21: command not found: ss
PORTS-RC=127
```

Target references: `lib/functions-system.zsh:719-720` marks `curl`, `lsd`,
`ss`, `zoxide` required and `:828-831` returns 1 on failure;
`lib/functions-files.zsh:143` returns 1 when `unzip` is absent.

## Expected behavior

A missing optional tool produces a recorded `skip` (or an explicit
`INCOMPLETE` dependency entry), the stage is `incomplete`, and the report's
tool metadata names the tool. No case fails because a tool the README does not
require is absent.

## Proposed fix

- Wrap the affected cases in `qa_opt`: `qa_opt 'ports' ss ...`,
  `qa_opt 'extract zip' unzip ...`, and either split `zdoctor` or gate the
  whole case on `curl lsd ss zoxide` (and `--secrets` additionally on
  `secret-tool`). Keep the tool-completeness portion of `zdoctor` covered by a
  dedicated gated case if desired.
- Add `ss`, `unzip`, `lsd`, and `curl` to `tool_metadata()` (`release.py:56`)
  so any future hard failure names the binaries in the report.
- Update `coverage.json` only if case names change; the skip path records the
  same names today, so no inventory change is needed for the gate wrappers.

## Test plan

- Fault-injection/CLI check with a PATH lacking `ss`: `ports` records skip and
  the stage is `incomplete`, not `fail`.
- Repeat for `unzip` against the `extract zip` case.
- Full gate re-run on this host must stay `YES` (all tools present).
