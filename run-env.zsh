#!/usr/bin/env zsh
# Environment-matrix spot checks: themes, glyph modes, and custom palettes.
#
# These checks source init.zsh in a non-interactive child shell and call the
# theme API directly, so they work without a terminal.  Layout and fzf-option
# behavior is covered interactively by qa-pty.py instead, because the fzf
# integration only exports its final options in a real interactive session.
#
# Environment:
#   ZSH_CONFIG_DIR    repository under test (default: ~/.config/zsh)
#   QA_WORK_DIR       fixture/work directory (default: ./.work)

emulate -L zsh
setopt no_unset

typeset -r project_dir=${0:A:h}
typeset -r work_dir=${QA_WORK_DIR:-$project_dir/.work}
typeset -r scratch=$work_dir/scratch
typeset -r repo_dir=${ZSH_CONFIG_DIR:-$HOME/.config/zsh}

typeset -gi n_pass=0 n_fail=0
typeset -ga failures

if [[ ! -d $scratch ]]; then
  print -u2 -r -- "fatal: fixtures missing under $scratch; run ./setup-fixtures.zsh first"
  exit 2
fi

# Inherited fzf options from the invoking shell must not leak into the checks.
typeset -ra strip_fzf=(
  FZF_DEFAULT_OPTS FZF_CTRL_T_OPTS FZF_CTRL_R_OPTS FZF_ALT_C_OPTS
  FZF_COMPLETION_OPTS FZF_COMPLETION_PATH_OPTS FZF_COMPLETION_DIR_OPTS
)
typeset -a env_args
local var
for var in "${strip_fzf[@]}"; do
  env_args+=(-u "$var")
done

check() {
  local name=$1 code=$2 envs=${3-}
  local out rc
  out=$(cd -- "$scratch" && env "${env_args[@]}" ${=envs} zsh -fc "source ${(q)repo_dir}/init.zsh 2>/dev/null; $code" 2>&1)
  rc=$?
  if (( rc == 0 )); then
    print -r -- "PASS  $name"
    (( n_pass++ ))
  else
    print -r -- "FAIL  $name (rc=$rc)"
    local -a lines=( "${(@f)out}" )
    print -r -- "      ${lines[1]:-<no output>}"
    failures+=("$name")
    (( n_fail++ ))
  fi
}

print -r -- "repository: $repo_dir"
print -r -- ''

print -r -- '== Theme shows =='
for theme in catppuccin-mocha catppuccin-latte nord gruvbox-dark terminal; do
  check "ztheme show $theme" "ztheme show $theme >/dev/null"
done

print -r -- '== Glyph modes =='
check 'NO_NERD_FONT becomes unicode' 'ztheme current | grep -qi unicode' 'NO_NERD_FONT=1'
check 'glyphs ascii tier' 'ztheme current | grep -qi ascii' 'ZSH_UI_GLYPHS=ascii'
check 'LC_ALL=C auto becomes ascii' 'ztheme current | grep -qi ascii' 'LC_ALL=C ZSH_UI_GLYPHS=auto'

print -r -- '== Custom palettes =='
check 'complete custom palette applies' 'typeset -gA ZSH_UI_CUSTOM_COLORS; for role in "${_ZSH_UI_THEME_ROLES[@]}"; do ZSH_UI_CUSTOM_COLORS[$role]=101010; done; ZSH_UI_CUSTOM_COLORS[accent]=abcdef; ZSH_UI_THEME=custom; _zsh_theme_resolve_settings && _fzf_require_ready && [[ $FZF_DEFAULT_OPTS == *abcdef* ]]'
check 'incomplete custom falls back atomically' 'typeset -gA ZSH_UI_CUSTOM_COLORS; ZSH_UI_CUSTOM_COLORS[bg]=101010; ZSH_UI_THEME=custom; _zsh_theme_resolve_settings; _fzf_require_ready; [[ $FZF_DEFAULT_OPTS != *101010* ]]'

print -r -- ''
print -r -- "env sweep: $n_pass passed, $n_fail failed"
if (( n_fail > 0 )); then
  print -r -- "failed: ${(j:, :)failures}"
  exit 1
fi
