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
setopt no_unset pipe_fail

typeset -r project_dir=${0:A:h}
typeset -r work_dir=${QA_WORK_DIR:-$project_dir/.work}
typeset -r scratch=$work_dir/scratch
typeset -r repo_dir=${ZSH_CONFIG_DIR:-$HOME/.config/zsh}

typeset -gi n_pass=0 n_fail=0
typeset -ga failures

if [[ ! -d $scratch || -L $scratch ]]; then
  print -u2 -r -- "fatal: fixtures missing under $scratch (or it is a symlink); run ./setup-fixtures.zsh first"
  exit 2
fi

python3 "$project_dir/qa_common.py" verify || exit 2
check() {
  python3 "$project_dir/qa_common.py" case "$1" "$2" "${3-}"
  if (( $? == 0 )); then (( n_pass++ )); else (( n_fail++ )); failures+=("$1"); fi
  return 0
}

print -r -- "repository: $repo_dir"
print -r -- ''

print -r -- '== Theme shows =='
for theme in catppuccin-mocha catppuccin-latte nord gruvbox-dark terminal; do
  check "ztheme show $theme" "ztheme show $theme >/dev/null"
done

print -r -- '== Glyph modes =='
check 'NO_NERD_FONT becomes unicode' 'ztheme current > "$HOME/glyphs.out" && command grep -q "^glyphs: unicode" "$HOME/glyphs.out"' 'NO_NERD_FONT=1'
check 'glyphs ascii tier' 'ztheme current > "$HOME/glyphs.out" && command grep -q "^glyphs: ascii" "$HOME/glyphs.out"' 'ZSH_UI_GLYPHS=ascii'
check 'LC_ALL=C auto becomes ascii' 'ztheme current > "$HOME/glyphs.out" && command grep -q "^glyphs: ascii" "$HOME/glyphs.out"' 'LC_ALL=C ZSH_UI_GLYPHS=auto'

print -r -- '== Custom palettes =='
check 'complete custom palette applies' 'typeset -gA ZSH_UI_CUSTOM_COLORS; for role in "${_ZSH_UI_THEME_ROLES[@]}"; do ZSH_UI_CUSTOM_COLORS[$role]=101010; done; ZSH_UI_CUSTOM_COLORS[accent]=abcdef; ZSH_UI_THEME=custom; _zsh_theme_resolve_settings && _fzf_require_ready && [[ $FZF_DEFAULT_OPTS == *abcdef* ]]'
check 'incomplete custom falls back atomically' 'typeset -gA ZSH_UI_CUSTOM_COLORS; ZSH_UI_CUSTOM_COLORS[bg]=101010; ZSH_UI_THEME=custom; _zsh_theme_resolve_settings && _fzf_require_ready && [[ $FZF_DEFAULT_OPTS != *101010* ]]'

print -r -- ''
check 'NO_COLOR plain output' 'ztheme show nord > "$HOME/plain.out" && ! command grep -q "$(printf "\\033")" "$HOME/plain.out"' 'NO_COLOR=1'

print -r -- "env sweep: $n_pass passed, $n_fail failed"
if (( n_fail > 0 )); then
  print -r -- "failed: ${(j:, :)failures}"
  exit 1
fi
