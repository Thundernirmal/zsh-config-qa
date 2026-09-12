# ISSUE-0017: `cleanup()` ignores the result of `secret-tool clear` (`p` is unused)

- **Status:** Open
- **Severity:** Low
- **Category:** cleanup
- **Affected:** `release.py:141`
- **Confidence:** Confirmed by inspection (AST check)
- **Filed:** 2026-09-12 against harness `60f633a` plus the pending audit working tree

## Summary

In credential cleanup:

```python
# Never log values. Only the run's synthetic credential is queried.
p = bounded(['secret-tool', 'clear', 'application', 'cgm', 'variable', name], timeout=20)      # release.py:141
check = bounded(['secret-tool', 'lookup', 'application', 'cgm', 'variable', name], timeout=20) # release.py:142
if check.returncode != 1 or check.stderr.strip():
    errors.append(f'credential cleanup could not verify absence: {name}')
```

The `clear` result (`p.returncode`/`p.stderr`) is never inspected. A static check of the function confirms the assignment is dead:

```sh
$ python3 - <<'EOF'
import ast
fn = next(n for n in ast.walk(ast.parse(open('/home/nirmal/projects/zsh-config-qa/release.py').read()))
          if isinstance(n, ast.FunctionDef) and n.name == 'cleanup')
stores = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
loads  = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
print('assigned but never read inside cleanup():', sorted(stores - loads))
EOF
assigned but never read inside cleanup(): ['p']
```

## Impact

Absence is still **proven** by the subsequent lookup: if `clear` failed and the value remains, `lookup` returns 0 and `check.returncode != 1` appends an error. So this is not a false-success path. The defects are diagnostic and robustness:

- `clear` failures (backend error, timeout, transport error) are invisible unless the entry survived; if the entry happened to be already gone, the failure is silently swallowed and no evidence records why cleanup needed a second path.
- The unused variable is lint/dead code and invites future readers to assume `clear` was checked.
- `bounded` can return a synthetic timeout result (`returncode == 124`, `qa_common.py:111`) with a timeout marker in stderr; nothing surfaces that.

This is a hygiene/observability issue, not a cleanup-verification failure (the lookup gate remains authoritative), and it should not by itself block a release.

## Expected behavior

Every cleanup command's outcome is either handled or explicitly justified. A `secret-tool clear` failure is reported as a diagnostic, while absence continues to be proven by the independent lookup. (If `clear` fails and the lookup also fails to prove absence, the existing error path already makes the gate non-`YES`.)

## Proposed fix

```python
cleared = bounded(['secret-tool', 'clear', 'application', 'cgm', 'variable', name], timeout=20)
check = bounded(['secret-tool', 'lookup', 'application', 'cgm', 'variable', name], timeout=20)
if check.returncode != 1 or check.stderr.strip():
    errors.append(f'credential cleanup could not verify absence: {name}')
elif cleared.returncode != 0 or cleared.stderr.strip():
    errors.append(
        f'credential clear reported failure but absence is verified: {name}: '
        f'{cleared.stderr.strip() or "exit " + str(cleared.returncode)}'
    )
```

If failing the run for an already-verified-absent credential is undesirable, downgrade the `elif` branch to a recorded diagnostic (not an `errors` entry) and document that choice.

## Test plan

- Extend `tests/test_gate.py::test_cleanup_backend_failure_is_not_success` (and the surrounding cleanup tests) with a stubbed `secret-tool` whose `clear` exits nonzero while `lookup` exits 1: assert cleanup reports the clear diagnostic (or the documented non-fatal record) and never returns success without the lookup proof.
- Run `./run-all.zsh selftest` to confirm the new self-test identity is added to `coverage.json`.
