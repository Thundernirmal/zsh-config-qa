# ISSUE-0019: Scenario descriptions overclaim relative to what is asserted (`fkill` "cancels safely", `ctrl-t` "toggles previews", layout "frame and preview")

- **Status:** Open
- **Severity:** Low
- **Category:** contract-drift
- **Affected:** `qa-pty.py:953` with `qa-pty.py:725-734`; `qa-pty.py:944` with `qa-pty.py:567-579`; `qa-pty.py:941-943` with `qa-pty.py:543-562`
- **Confidence:** Confirmed by inspection
- **Filed:** 2026-09-12 against harness `60f633a` plus the pending audit working tree

## Summary

Three entries in `SCENARIOS` (which feed `--list`, the stage log, and the human-facing scenario inventory) promise more than the scenario functions assert.

### `fkill` — "fkill opens the Processes picker and cancels safely" (`qa-pty.py:953`)

`fkill_picker()` (`qa-pty.py:725-734`):

```python
session.clear_output()
session.sendline("fkill")
session.wait_for("Processes", timeout=20)
close_picker(session)  # cancel: never send a signal
assert "Processes" in session.text(), "Processes frame never rendered"
```

Asserted:
- the fkill picker rendered (`Processes` appeared),
- `close_picker()` sent ESC and the shell returned to ZLE (`close_picker` at `qa-pty.py:394-407` calls `wait_children_empty`, `wait_for_zle`, `sync`).

**Not** asserted:
- that ESC cancelled the selection without executing the signal path — there is no target process for this scenario and no check that a process stayed alive, no assertion that the "sent SIGTERM" line is absent, and no check of the picker's exit status/effect. If the ESC path accidentally fell through to `kill`, the current assertions would still pass.

The Enter path is covered separately by `fkill-signal` (`qa-pty.py:737-760`), which starts a dummy `sleep` and asserts the exact termination (`dummy.poll() == -signal.SIGTERM`). That is the model the cancel path should follow: start a dummy, ESC, assert it is still alive and no signal line appeared.

### `ctrl-t` — "Ctrl+T opens the Files picker and toggles previews" (`qa-pty.py:944`)

`ctrl_t_picker()` (`qa-pty.py:567-579`):

```python
session.send(b"\x14")                      # Ctrl+T
session.wait_for("Files", timeout=15)
session.wait_for("Type to filter files", timeout=5)
session.send(b"\x10")                      # Ctrl+P preview toggle
session.send(b"\x1f")                      # Ctrl+/ wrap toggle
close_picker(session)
assert "Files" in session.text(), "Files frame never rendered"
```

Asserted:
- the Files picker opened (two markers: `Files`, `Type to filter files`),
- after sending Ctrl-P/Ctrl-/ the picker could be cancelled and the shell returned to a prompt.

**Not** asserted:
- any effect of Ctrl-P (preview pane appearing/toggling) or Ctrl-/ (wrap toggle): the keystrokes are sent blind, with no wait for a post-toggle marker and no before/after comparison of the rendered frame. If both key bindings were removed or broken, this scenario would still pass.

### `env-layout-*` — "compact/roomy/minimal layout frame and preview" (`qa-pty.py:941-943`)

`_env_layout()` (`qa-pty.py:543-562`) runs `probe_shell` with `ZSH_FZF_LAYOUT`
set and only substring-matches the exported option strings
(`FZF_CTRL_T_OPTS`/`FZF_DEFAULT_OPTS`) for `--height=~60%`, `--padding=0,1`,
`50%`, etc. No picker is opened and no rendered frame or preview pane is
inspected. A regression that kept the options but rendered a different layout,
or dropped the preview entirely, would still pass.

## Impact

The release report and `--list` inventory state behaviors that were never verified. A regression in the picker cancel path (e.g. ESC falling through to the default action), in the preview/wrap toggle bindings, or in the rendered layout would ship with these scenarios marked `pass`, and `coverage.json` would still treat the descriptions as covered.

## Expected behavior (and proposed fix)

- **fkill cancel:** start a registered dummy `sleep` (as `fkill_signal` does), open fkill, send ESC, assert the dummy is still running (`dummy.poll() is None`), assert `"sent SIGTERM" not in session.text()`, then clean up the dummy. Keep the frame assertion.
- **ctrl-t preview:** after `session.send(b"\x10")`, wait for an observable preview marker/state change (e.g. the preview border/header or a known file-content line rendered in the preview pane) and assert it differs from the pre-toggle frame; for Ctrl-/, assert the visible toggle effect or drop the claim and update the description to "sends preview/wrap toggle keys". If a robust marker is not available, narrow the description instead of leaving the overclaim.
- **layout:** either open the picker once per layout and assert the rendered frame/preview markers the description claims, or rename the descriptions to "layout options" so they describe the option-string check that exists.
- Update the SCENARIOS descriptions only after the assertions exist.

## Test plan

- Mutation check: in a scratch copy/experiment, stub the fkill ESC path to send a signal and confirm the strengthened scenario fails while the current one passes; similarly strip the Ctrl-P binding and confirm the strengthened ctrl-t scenario fails.
- Re-run `./run-all.zsh pty` (both repetitions) and confirm `fkill`, `fkill-signal`, `ctrl-t`, and `ctrl-t-insert` all pass with the stronger assertions.
- If descriptions change, verify `python3 qa-pty.py --list` output and any README wording that references the scenarios.
