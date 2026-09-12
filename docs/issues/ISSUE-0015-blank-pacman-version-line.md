# ISSUE-0015: tool_metadata records a blank first version line for tools like pacman

- **Status:** Open
- **Severity:** Low
- **Category:** evidence-integrity
- **Affected:** `release.py:54-67`, `release.py:64-65`
- **Confidence:** Confirmed by execution
- **Filed:** 2026-09-12 against harness `60f633a` plus the pending audit working tree

## Summary

`tool_metadata()` probes each tool with `--version` and keeps only the first output line:

```python
probe = bounded([path, '--version'], env=env, timeout=5)
entry.update(version=(probe.stdout or probe.stderr).splitlines()[:1], version_exit=probe.returncode)
```

(`release.py:64-65`). `pacman --version` prints a leading blank line before its banner, so
the retained report records `"version": [""]` with exit status 0. The tool-version section
of the report therefore contains no usable pacman version, even though every other probed
tool records a real string.

## Impact

- The report's machine-readable evidence is silently degraded for affected tools: an
  operator or later audit cannot tell which pacman version produced the run, and the
  `YES` scope claim ("installed tools", README:30-34) cannot be reconstructed.
- The same truncation affects any tool whose `--version` starts with an empty line (or
  prints its version after a banner); no warning is emitted, so the blank is
  indistinguishable from a tool that genuinely printed nothing.
- A fixed run still passes; this is an evidence-fidelity defect, not a gate failure.

## Root cause

`splitlines()[:1]` assumes line 1 is the version. There is no check that the retained line
is non-empty or that the probe output is usable, and `version_exit == 0` is not combined
with a non-empty version in any validation.

## Reproduction

```sh
mkdir -p /tmp/opencode/audit-r1/issue-0015 && cd /tmp/opencode/audit-r1/issue-0015
pacman --version | head -1 | od -c
python3 - <<'PY'
import json, os, shutil, sys
from pathlib import Path
HARNESS = Path('/home/nirmal/projects/zsh-config-qa')
sys.path.insert(0, str(HARNESS))
import release

# Exact slice used by release.py:64-65.
probe = release.bounded([shutil.which('pacman'), '--version'], env=dict(os.environ), timeout=5)
entry = {}
entry.update(version=(probe.stdout or probe.stderr).splitlines()[:1], version_exit=probe.returncode)
print('tool_metadata slice for pacman:', json.dumps(entry))

zsh_probe = release.bounded([shutil.which('zsh'), '--version'], env=dict(os.environ), timeout=5)
zsh_entry = {}
zsh_entry.update(version=(zsh_probe.stdout or zsh_probe.stderr).splitlines()[:1], version_exit=zsh_probe.returncode)
print('tool_metadata slice for zsh   :', json.dumps(zsh_entry))

report = HARNESS / '.runs/20260912-193357-431ba0a8/report.json'
print('report.json pacman entry      :', json.dumps(json.loads(report.read_text())['tools']['pacman'], indent=2))
PY
```

### Observed

```text
0000000  \n
0000001
tool_metadata slice for pacman: {"version": [""], "version_exit": 0}
tool_metadata slice for zsh   : {"version": ["zsh 5.9.2 (x86_64-pc-linux-gnu)"], "version_exit": 0}
report.json pacman entry      : {
  "path": "/usr/bin/pacman",
  "sha256": "bef41dfdb55bf620aa6d5b50a657b6d04b894d1b48da7d447a9d23a29ff66478",
  "version": [
    ""
  ],
  "version_exit": 0
}
```

The `od -c` output shows the retained first line is just `\n`. The last block is read
from the retained report of run `20260912-193357-431ba0a8` (read-only), confirming the
same blank value was stored by a real gate run.

## Expected behavior

`tool_metadata()` records the first non-empty version line — or a clear marker such as
`version: []` plus a note — rather than an empty string that looks like successful
evidence.

## Proposed fix

Select the first non-empty line:

```python
lines = (probe.stdout or probe.stderr).splitlines()
version = next((line for line in lines if line.strip()), '')
entry.update(version=version or None, version_exit=probe.returncode)
```

Optionally keep the first two non-empty lines for banner-repeating tools, and flag a
zero-exit probe that produced no non-empty line.

## Test plan

- Unit test with a fake executable that prints `"\nVERSION 1\n"` and assert the recorded
  version is `VERSION 1`.
- Assert a probe with empty output records `None`/`''` explicitly and can be
  distinguished from a good version in `report.json`.
- No coverage inventory change unless a new named selftest is introduced.
