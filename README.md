# zsh-config-qa

Live QA harness for the shared Zsh configuration in `~/.config/zsh` (the
repository is configurable). It exercises the parts of the configuration that
unit-style fixtures cannot: the real interactive terminal boundary, real
installed tools, and real package-manager and credential-service round trips.

The repository's own `scripts/run-tests.zsh` proves the modules with fake
binaries and `scripts/test-fzf-pty.py` proves one fzf picker through a real PTY.
This project complements those with a full live pass:

- every user-facing command runs for real (navigation, files, git, system,
  meta aliases, themes, packages, credentials),
- every themed widget/picker is driven through a real PTY (Ctrl+T, Ctrl+R,
  Alt+C, `**<Tab>` completion, `zhelp`, `fbr`, `fkill`, `zi`, `npkg`, `cgm`),
- environment matrices are verified (layouts, glyph modes, `NO_COLOR`,
  inherited `FZF_DEFAULT_OPTS`, custom palettes, fzf cold/warm cache),
- mutating scenarios run against throwaway state (`npkg` uses an isolated
  HOME/profile, `cgm` creates and deletes a test credential, `fkill` signals a
  dummy process).

## Requirements

Required:

- Linux (uses `/proc` for child-process detection)
- `zsh` 5.9+ (tested with 5.9.2)
- `fzf` 0.68.0+ (tested with 0.74.3)
- `git`, `python3` 3.9+ (standard library only), coreutils `timeout`

Optional tooling decides which checks run:

| Tool | Enables |
| --- | --- |
| `nix` + `jq` | `npkg` picker scenarios and `npkg help/list` checks |
| `secret-tool` + a running Secret Service | `cgm` scenarios and checks |
| `pacman` (or another supported manager) | real `upkg` read-only checks |
| `curl` | `weather`, `headers`, `myip`, `zdoctor --network` |
| `xz`, `bzip2` | tar.xz / tar.bz2 fixture archives |
| `zoxide` | `z`/`zi` checks |

Missing optional tools produce `SKIP` lines, not failures.

## Quick start

```sh
cd ~/projects/zsh-config-qa

./run-all.zsh                 # fixtures + safe + env + pty
./run-all.zsh fixtures safe   # selected stages only

./setup-fixtures.zsh          # rebuild fixtures only
./run-safe.zsh                # read-only command sweep
./run-env.zsh                 # theme/glyph/custom-palette matrix
python3 qa-pty.py --list      # list interactive scenarios
python3 qa-pty.py ctrl-t      # run one scenario
python3 qa-pty.py             # run all interactive scenarios
```

Exit codes: `0` success, `1` at least one check failed, `2` configuration or
prerequisite problem (missing fixtures, repository, or `fzf`).

## Environment variables

| Variable | Default | Meaning |
| --- | --- | --- |
| `ZSH_CONFIG_DIR` | `~/.config/zsh` | Repository under test (must contain `init.zsh`) |
| `QA_WORK_DIR` | `./.work` | Fixtures, isolated HOME, probe output |
| `QA_SKIP_NETWORK` | unset | `1` skips network-dependent safe-sweep checks |

`.work/` is disposable and gitignored. Deleting it only forces fixture and
isolated-profile rebuilds.

`ZSH_CONFIG_DIR` is authoritative for `run-safe.zsh`, `run-env.zsh`, and the
isolated PTY scenarios. The remaining PTY scenarios launch a real interactive
shell, which reaches the repository through the user's `~/.zshrc`
(`source ~/.config/zsh/init.zsh`). Keep the default install path when you want
those scenarios to reflect the installed configuration.

## Layout

| Path | Purpose |
| --- | --- |
| `run-all.zsh` | Orchestrates the stages and reports per-stage status |
| `setup-fixtures.zsh` | Builds scratch dirs, archives, git repos, and pull-rebase fixtures |
| `run-safe.zsh` | Read-only sweep over every user-facing command |
| `run-env.zsh` | Theme, glyph, and custom-palette checks |
| `qa-pty.py` | Interactive PTY driver and scenario suite |
| `.work/` | Generated state (fixtures, isolated HOME, probes) |

## Stage: fixtures

`setup-fixtures.zsh` recreates `$QA_WORK_DIR/scratch` deterministically:

- `nav/sub/deep`, `files/` with text files and archives (`tar.gz`, `tar.xz`,
  `tar.bz2`, `zip` when the tools exist),
- `gitrepo/` with two commits,
- `gpr-origin.git` + `gpr-seed` + `gpr-clone`, where the clone is one commit
  behind origin so `gpr` must rebase,
- a zoxide database seed for the scratch directory.

## Stage: safe sweep

`run-safe.zsh` runs each case in a fresh child zsh that sources `init.zsh`, so
aliases, lazy loaders, theme wiring, and guarded integrations behave exactly as
they do in a real shell. Categories and representative cases:

| Category | Cases |
| --- | --- |
| Navigation | `..`, `...`, `....`, `-`, `z`, `mkcd`, `croot` |
| Files | `ls`, `ll`, `la`, `lt`, `cat`, `peek`, `dusage`, `bigfiles`, `extract` (gz/zip/xz/bz2/`--keep`), `grep`, `diff`, `ff`, `ft` |
| Git | `glog`, `gitcount`, `gcount`, `gun`, `gpr` |
| System | `weather`, `headers`, `myip`, `ports`, `path`, `fanprofile` |
| Meta | `G`, `W`, `H`, `T`, `L`, `NE`, `NUL` (with `ZSH_GLOBAL_ALIASES=1`), `tips`, `zhelp --plain`, `zdoctor`, `zdoctor --network --secrets` |
| Theme | `ztheme list/current/show/export/use/reset`, invalid theme rejection |
| Packages | `upkg managers/search/outdated/plan/upgrade --dry-run`, `npkg help/list` |
| Credentials | `cgm check/list/status` (never retrieves values) |

No case mutates the system: git cases use fixtures, package cases are
read-only or dry-run, and credential cases only read names and health.

## Stage: environment matrix

`run-env.zsh` sources `init.zsh` in a non-interactive child (with inherited
`FZF_*` variables stripped) and checks:

- `ztheme show` for every built-in palette,
- `NO_NERD_FONT=1` resolves to the Unicode glyph tier,
- `ZSH_UI_GLYPHS=ascii` and `LC_ALL=C ZSH_UI_GLYPHS=auto` resolve to ASCII,
- a complete custom palette applies, and an incomplete one falls back
  atomically with no partial colors.

Layout and fzf-option behavior lives in the PTY stage instead, because the fzf
integration only exports its final options in a real interactive session.

## Stage: interactive PTY scenarios

`qa-pty.py` drives a real interactive zsh through a PTY with its own
controlling terminal. Every scenario is available individually:

```sh
python3 qa-pty.py --list
python3 qa-pty.py ctrl-t fkill-signal
```

| Scenario | What it proves |
| --- | --- |
| `startup` | Fresh interactive startup is clean, no diagnostics |
| `nounset-startup` | `setopt NO_UNSET` before sourcing init still loads zoxide and the other integrations |
| `fzf-cold-start` | An empty fzf cache is rebuilt in the isolated HOME |
| `fzf-warm-start` | A warm cache is reused without regeneration |
| `fzf-blocked` | fzf 0.67 blocks pickers but not plain `zhelp` or other commands |
| `env-no-color` | `NO_COLOR=1` reaches the pickers (`--no-color`, `--color=never`) |
| `env-extra-opts` | `ZSH_FZF_EXTRA_OPTS` is appended to the managed options |
| `env-inherited-opts` | Inherited `FZF_DEFAULT_OPTS` survives alongside managed chrome |
| `env-layout-compact/roomy/minimal` | Each layout exports its expected frame, padding, and preview width |
| `ctrl-t` | Files picker opens; Ctrl+P and Ctrl+/ toggles work; Esc cancels |
| `ctrl-t-insert` | Selecting a file inserts its path without executing it |
| `ctrl-r` | History picker opens word-wrapped with preview toggles |
| `alt-c` | Directories picker opens with hidden preview |
| `completion` | `**<Tab>` opens the fzf completion overlay |
| `zhelp` | Commands palette opens with the Usage preview |
| `zhelp-queue` | Enter queues the selected example on the command line |
| `fbr` / `fbr-select` | Branch picker opens; selecting checks the branch out |
| `fkill` / `fkill-signal` | Picker opens; Esc sends nothing; Enter sends SIGTERM to the selected PID |
| `zi` / `zi-select` | zoxide picker opens; selecting changes directory |
| `cgm` | Store (hidden input), list, load, unset, and delete a test credential |
| `npkg-remove` | Remove picker removes the selected package from the isolated profile |
| `npkg-add` | Add picker installs the selected package into the isolated profile |

Scenarios whose tools are missing are reported as `SKIP` with the missing
dependency; they are not failures.

## Safety

- The repository under test is never written to.
- `npkg` scenarios use `$QA_WORK_DIR/nixhome` as HOME and its own Nix profile
  (`~/.local/state/nix/profiles` beneath that HOME). Your real profile is not
  touched.
- The `cgm` scenario stores `QA_PTY_TEST` in the real Secret Service and
  deletes it before finishing. It never prints credential values.
- `fkill-signal` only ever signals a `sleep 600` process started by the test.
- No `upkg upgrade` or `upkg clean` is ever executed; only read-only and
  `--dry-run` paths run.
- `extract`, `gun`, `mkcd`, `fbr-select`, and `zi-select` operate on fixture
  paths under `$QA_WORK_DIR`.

## Design notes

- **Prompt synchronization.** Each step sends a `print "QA-${:-SYNC}-…"`
  command. The echoed command line contains the literal `${:-SYNC}` while the
  executed output contains `QA-SYNC`, so a single marker occurrence proves the
  command actually ran, not just that it was typed.
- **zle readiness.** After each command the driver waits until the PTY leaves
  canonical mode, which means zle is accepting keys. Without this, keystrokes
  can be swallowed by the line discipline before zle starts.
- **Controlling terminal.** The child calls `setsid()` and `TIOCSCTTY` so
  `/dev/tty` works; the generated fzf widgets read from it.
- **Picker lifecycle.** `wait_no_fzf()` watches `/proc/<pid>/…/children`
  until the fzf process is gone before typing the next command, avoiding the
  race where sync text lands in fzf's query.
- **Effect-based verification.** Scenarios that select from a picker verify
  the effect directly instead of typing a follow-up command, because
  keystrokes can be lost while the widget returns and runs foreground work:
  zle buffer render (`ctrl-t-insert`, `zhelp-queue`), `/proc/<pid>/cwd`
  (`zi-select`), `.git/HEAD` (`fbr-select`), the dummy process state
  (`fkill-signal`), and the isolated Nix profile (`npkg-*`).
- **Terminal stripping.** ANSI/OSC sequences are removed lazily and
  non-greedily; ST-terminated OSC sequences would otherwise swallow rendered
  text up to the next BEL.
- **Isolated HOME.** Scenarios that need a clean HOME use
  `$QA_WORK_DIR/nixhome` with a `.config/zsh` symlink to the repository, the
  same pattern as the repository's fixed-install-path smoke test.

## Coverage map

The harness covers the local `qa-features.csv` checklist rows from the
repository, plus extensions:

| Checklist area | Harness coverage |
| --- | --- |
| Cold/warm startup, empty cache | `startup`, `fzf-cold-start`, `fzf-warm-start` |
| `NO_UNSET` startup robustness | `nounset-startup` |
| fzf below minimum | `fzf-blocked` |
| Theme list/current/show/export/use/reset/validation | `run-env.zsh`, `run-safe.zsh` |
| Custom palettes (complete/incomplete) | `run-env.zsh` |
| Layouts, `NO_COLOR`, glyph modes, inherited options | `qa-pty.py` env scenarios |
| Ctrl+T / Ctrl+R / Alt+C / completion / zhelp / fbr / fkill / zi | dedicated PTY scenarios |
| `npkg add` / `npkg remove` | real Nix picker scenarios in an isolated profile |
| `cgm` round trip | real Secret Service scenario |
| Real managers, network, archives | `run-safe.zsh` |

## Known limitations

- Visual appearance (font rendering, perceived contrast, Nerd Font glyph
  shaping) is not verifiable by automation; the harness asserts terminal
  output, widths, and option values instead.
- Destructive package paths (`upkg upgrade`, `upkg clean`) and bulk/SIGKILL
  `fkill` review paths are intentionally excluded.
- `npkg-add` needs network access on a cold Nix cache to build the nixpkgs
  attribute index; the index is cached in the isolated HOME afterward.
- `cgm` requires a running Secret Service; without one the scenario is either
  skipped (`secret-tool` missing) or fails with the service diagnostic.
- Linux-only due to `/proc` child inspection and `timeout` usage.

## Troubleshooting

- `fatal: fixtures missing` — run `./setup-fixtures.zsh` (or `./run-all.zsh`).
- `fatal: no init.zsh under …` — set `ZSH_CONFIG_DIR` to the repository.
- `npkg` scenarios skipped — install `nix` and `jq`.
- `cgm` scenario fails at "Store Credential" — no Secret Service provider is
  running in the session (`gnome-keyring`, `kwallet`, etc.).
- `fzf-blocked` reports the minimum-version diagnostic in your real shell —
  upgrade fzf to 0.68.0 or newer.
- A widget scenario times out after Esc — rerun it; the driver retries the
  cancel and waits for the fzf process to exit, but terminal timing can still
  vary under heavy load.
