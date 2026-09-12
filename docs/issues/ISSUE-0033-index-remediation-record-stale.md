# ISSUE-0033: The issue index's remediation record is stale and internally inconsistent

- **Status:** Fixed
- **Severity:** Low
- **Category:** contract-drift
- **Affected:** `docs/issues/README.md:11` (`Remediation result`), `:12` (`Re-audit result`), `:114` and `:122` (selftest counts), `:197` (next free ID)
- **Confidence:** Confirmed by inspection (cited runs checked against `.runs/`)
- **Filed:** 2026-09-12 against harness revision `777ca29`

## Summary

The index's remediation record describes a different history from the one that
exists in Git and in `.runs/`:

1. `docs/issues/README.md:11` says "all 26 open issues fixed in five reviewed
   batches (commits `ef8f429`, `6953be1`, `2ea2d8b`, `e898a90`, `1a66d03`)".
   The actual remediation has **six** fix batches plus one cleanup commit:
   - `ef8f429` (0001, 0002, 0028) — gate `20260912-204752-2d13b992`
   - `6953be1` (0007, 0024, 0027, 0029, 0015) — gate `20260912-205801-76cafce0`
   - `2ea2d8b` (0006, 0012, 0016, 0020, 0021, 0022) — gate `20260912-210646-ff36a6ad`
   - `e898a90` (0005, 0009, 0010, 0011, 0019, 0026) — gate `20260912-213912-57c5679f`
   - `1a66d03` (0003, 0004, 0018, 0025) — gate `20260912-215025-6c8985a5`
   - `5a02c67` (0008, 0014) — gate `20260912-215458-9601a735`
   - `777ca29` removed the stray `t6.zsh` probe.
   `5a02c67` fixes two of the 26 issues (`ISSUE-0008`, `ISSUE-0014`) and is
   omitted from the record, even though both issue files cite its gate run.
2. The earlier text "Final full gate: `20260912-215025-6c8985a5` ...
   63/11/4/51 case rows" described the pre-`5a02c67` tree; the final
   remediation verification is `20260912-215458-9601a735` (YES, 55 selftest
   rows). Any "final" claim should cite that run.
3. `:114` and `:122` still say `tests/test_gate.py (32)` and
   `63/11/4/32 ... selftest`; the current inventory is 55 selftest identities in
   `coverage.json`.
4. `:12`'s status summary ("3 Open (0005, 0026, 0030), 24 Fixed, 3 Refuted")
   contradicts the per-issue files it indexes: `ISSUE-0005`'s own header says
   `Fixed` (a "Re-fix" not present in the tree — see that issue), while the
   table row says `Open`; this audit additionally reopens `ISSUE-0001` and
   `ISSUE-0027` and files `ISSUE-0031` and `ISSUE-0032`. Counts must be derived
   from the files, not maintained by hand.
5. `:197` still says the next free ID is `0031`; it must be advanced after each
   filing.

## Impact

- The index is the audit's acceptance record. A reader cannot reconstruct which
  commit and which gate proves each fix; two of the 26 fixes are attributed to a
  commit set that does not contain them, and the "final" gate cited predates the
  last fix commit.
- Status counts contradict the linked issue files, undermining the record's
  trustworthiness even for readers who follow the links.

## Root cause

The remediation and re-audit summaries were hand-edited incrementally without a
final reconciliation against `git log`, `coverage.json`, and the per-issue
`Status:` headers. The index was written before the last batch landed and was
not regenerated afterwards.

## Reproduction

```sh
cd /home/nirmal/projects/zsh-config-qa
git log --oneline -7
grep -n 'five reviewed batches\|63/11/4/32\|(32)\|currently' docs/issues/README.md
python3 - <<'PY'
import json, subprocess
from pathlib import Path
cov = json.loads(Path('coverage.json').read_text())
print('selftest identities:', len(cov['selftest']))
print('commit set cited:', subprocess.run(['git','log','--format=%h','-7'],capture_output=True,text=True).stdout.split())
PY
```

### Observed

```text
5a02c67 Fix remaining issues 0008 and 0014; complete remediation
...
777ca29 Remove leftover zdoctor scratch probe
11:- **Remediation result (2026-09-12):** all 26 open issues fixed in five reviewed batches (commits `ef8f429`, `6953be1`, `2ea2d8b`, `e898a90`, `1a66d03`); ...
selftest identities: 55
commit set cited: ['1a66d03', 'e898a90', '2ea2d8b', '6953be1', 'ef8f429', '5a02c67', '777ca29']
```

## Expected behavior

The index lists every remediation commit with the gate run that verified it,
states the selftest count belonging to the cited run, and keeps its status
counts consistent with the individual issue files at all times.

## Proposed fix

- Replace the "five reviewed batches" sentence with the six commit/gate pairs
  above (plus `777ca29` as cleanup), or point to `git log` and each run's
  `report.json`.
- Update the selftest counts if the "Verified sound" section is kept as current
  (or label that section as describing the original audit revision).
- Regenerate the status summary from the issue files after the audit cycle
  concludes.
- Advance the "currently `NNNN`" note in the same change as each new filing.

## Test plan

- A small check (manual or scripted) that for every row in the table the linked
  file's `Status:` matches the row, and that every cited run exists in `.runs/`
  with the stated verdict.
- No executable harness change; no `coverage.json` change.

## Fix (2026-09-12, batch 7)

- **Status change:** Open → Fixed.
- The index now carries the complete, accurate remediation record: all seven
  fix batches plus the `t6.zsh` cleanup commit, each with the gate run that
  verified it, the final delivered-tree gate, and the selftest count
  belonging to that run; every issue file's `Status:` was reconciled with its
  table row in the same change.
- The test plan was executed as part of this fix: a script cross-checked every
  table row against the linked file's metadata (zero mismatches) and every
  cited run against `.runs/` (all present with the stated verdicts).
- No executable harness change; no `coverage.json` change.
