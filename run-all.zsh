#!/usr/bin/env zsh
# Run the complete live QA harness.
#
# Stages (default order):
#   fixtures  build throwaway fixtures under QA_WORK_DIR
#   safe      read-only sweep over every user-facing command
#   env       theme, glyph, and custom-palette matrix
#   pty       interactive widget/picker scenarios
#
# Usage:
#   ./run-all.zsh                 # every stage
#   ./run-all.zsh fixtures safe   # selected stages
#
# Environment:
#   ZSH_CONFIG_DIR   repository under test (default: ~/.config/zsh)
#   QA_WORK_DIR      fixture/work directory (default: ./.work)
#   QA_SKIP_NETWORK  set to 1 to skip live network checks in the safe stage

emulate -L zsh
setopt no_unset pipe_fail

typeset -r project_dir=${0:A:h}
typeset -a stages
if (( $# > 0 )); then
  stages=( "$@" )
else
  stages=( fixtures safe env pty )
fi

typeset -gi failures=0
typeset tool
typeset stage
integer rc

print -r -- 'zsh-config-qa: live QA harness'
print -r -- "repository: ${ZSH_CONFIG_DIR:-$HOME/.config/zsh}"
print -r -- "work dir:   ${QA_WORK_DIR:-$project_dir/.work}"
print -r -- ''

typeset -a missing
for tool in zsh git python3 fzf; do
  (( $+commands[$tool] )) || missing+=("$tool")
done
if (( ${#missing[@]} > 0 )); then
  print -u2 -r -- "fatal: missing required tools: ${(j:, :)missing}"
  exit 2
fi
for tool in curl nix jq secret-tool; do
  (( $+commands[$tool] )) || print -r -- "note: optional tool missing: $tool (dependent checks will skip)"
done
print -r -- ''

run_stage() {
  local stage_name=$1
  print -r -- "=== stage: $stage_name ==="
  case $stage_name in
    fixtures) zsh "$project_dir/setup-fixtures.zsh" ;;
    safe)     zsh "$project_dir/run-safe.zsh" ;;
    env)      zsh "$project_dir/run-env.zsh" ;;
    pty)      python3 "$project_dir/qa-pty.py" ;;
    *)
      print -u2 -r -- "unknown stage: $stage_name"
      return 2
      ;;
  esac
}

for stage in "${stages[@]}"; do
  run_stage "$stage"
  rc=$?
  if (( rc != 0 )); then
    print -r -- "stage $stage failed (rc=$rc)"
    (( failures++ ))
  fi
  print -r -- ''
done

print -r -- "run-all: ${#stages[@]} stage(s), $failures failed"
(( failures == 0 )) || exit 1
