# ISSUE-0034: Unused function-local imports remain in `tests/test_gate.py`

- **Status:** Fixed
- **Severity:** Low
- **Category:** hygiene
- **Affected:** `tests/test_gate.py:191` (`import hashlib`), `tests/test_gate.py:236` (`import re`)
- **Confidence:** Confirmed by inspection (AST name-use check)
- **Filed:** 2026-09-12 against harness revision `777ca29`

## Summary

Two function-local imports are never used:

- `test_cgm_value_check_uses_a_hash_not_the_plaintext` (`tests/test_gate.py:190-194`)
  imports `hashlib` at line 191 but only inspects `qa-pty.py` source text with
  `self.assertIn`/`self.assertNotRegex`; it was introduced by `5a02c67` while
  fixing ISSUE-0008.
- `test_generated_credentials_are_valid_and_unique` (`tests/test_gate.py:235-240`)
  imports `re` at line 236 but uses only `self.assertRegex` and
  `self.assertTrue`; it predates the audit and was missed by ISSUE-0020's
  hygiene sweep.

An AST check over every function in the file reports exactly these two:

```sh
python3 - <<'PY'
import ast
from pathlib import Path
tree = ast.parse(Path('tests/test_gate.py').read_text())
for fn in ast.walk(tree):
    if isinstance(fn, ast.FunctionDef):
        for node in ast.walk(fn):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.asname or a.name.split('.')[0] for a in node.names]
                used = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)} | \
                       {n.attr for n in ast.walk(fn) if isinstance(n, ast.Attribute)}
                for name in names:
                    if name not in used:
                        print(f'{fn.name}:{node.lineno} unused local import {name}')
PY
```

### Observed

```text
test_cgm_value_check_uses_a_hash_not_the_plaintext:191 unused local import hashlib
test_generated_credentials_are_valid_and_unique:236 unused local import re
```

## Impact

Maintainer noise and drift only: the imports can mislead a reader into thinking
the tests use those modules, and they are residue of the same class ISSUE-0020
removed elsewhere. No runtime or verdict impact.

## Root cause

Copies of earlier drafts left the imports behind after the assertions were
rewritten to string/regex helpers; ISSUE-0020's sweep covered module-level
imports and dead methods but did not scan function-local imports.

## Expected behavior

No unused imports in the fault-injection suite; `pyflakes`-clean test module.

## Proposed fix

Delete the two import lines. No behavior change and no new test identity is
required.

## Test plan

- `python3 -m unittest`/`./run-all.zsh selftest` stays green; `coverage.json`
  identities unchanged.
- Re-run the AST check above and confirm no output.

## Fix (2026-09-12, batch 7)

- **Status change:** Open → Fixed.
- Removed the unused function-local imports (`hashlib` in
  `test_cgm_value_check_uses_a_hash_not_the_plaintext`, `re` in
  `test_generated_credentials_are_valid_and_unique`); an AST name-use scan
  over the whole file reports zero unused imports.
