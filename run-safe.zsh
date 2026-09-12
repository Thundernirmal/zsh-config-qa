#!/usr/bin/env zsh
# Live read-only sweep over every user-facing command in the shared Zsh config.
#
# Each case runs in a throwaway child zsh that sources the repository's
# init.zsh, so aliases, functions, lazy loaders, and theme wiring behave the
# same way they do in a real interactive shell.  Nothing here mutates the
# system: git cases use fixtures, extract cases use fixture archives, package
# cases are read-only or dry-run, and credential cases never retrieve values.
#
# Environment:
#   ZSH_CONFIG_DIR    repository under test (default: ~/.config/zsh)
#   QA_WORK_DIR       fixture/work directory (default: ./.work)
#   QA_SKIP_NETWORK   set to 1 to skip live network and mirror checks

emulate -L zsh
setopt no_unset

typeset -r project_dir=${0:A:h}
typeset -r work_dir=${QA_WORK_DIR:-$project_dir/.work}
typeset -r scratch=$work_dir/scratch
typeset -r repo_dir=${ZSH_CONFIG_DIR:-$HOME/.config/zsh}

typeset -gi n_pass=0 n_fail=0 n_skip=0
typeset -ga failures

if [[ ! -d $scratch || -L $scratch ]]; then
  print -u2 -r -- "fatal: fixtures missing under $scratch (or it is a symlink); run ./setup-fixtures.zsh first"
  exit 2
fi
if [[ ! -r $repo_dir/init.zsh ]]; then
  print -u2 -r -- "fatal: no init.zsh under $repo_dir; set ZSH_CONFIG_DIR"
  exit 2
fi

python3 "$project_dir/qa_common.py" verify || exit 2

qa() {
  python3 "$project_dir/qa_common.py" case "$1" "$2" "${3-}"
  if (( $? == 0 )); then (( n_pass++ )); else (( n_fail++ )); failures+=("$1"); fi
  return 0
}

qa_nz() {
  python3 "$project_dir/qa_common.py" case "$1" "$2" "${3-}" --nonempty
  if (( $? == 0 )); then (( n_pass++ )); else (( n_fail++ )); failures+=("$1"); fi
  return 0
}

qa_skip() {
  python3 "$project_dir/qa_common.py" skip "$1" "$2" || return 2
  (( n_skip++ ))
  return 0
}

# qa_opt NAME "tool1 tool2" CODE [ENV] -- skip when a required tool is absent
qa_opt() {
  local name=$1 needs=$2 code=$3 envvar=${4-}
  local tool
  for tool in ${=needs}; do
    if (( ! $+commands[$tool] )); then
      qa_skip "$name" "missing $tool"
      return 0
    fi
  done
  qa "$name" "$code" "$envvar"
}

# qa_opt_nz NAME "tool1 tool2" CODE [ENV] -- qa_opt with a nonempty-stdout check
qa_opt_nz() {
  local name=$1 needs=$2 code=$3 envvar=${4-}
  local tool
  for tool in ${=needs}; do
    if (( ! $+commands[$tool] )); then
      qa_skip "$name" "missing $tool"
      return 0
    fi
  done
  qa_nz "$name" "$code" "$envvar"
}

# qa_fixture NAME FILE CODE [NEEDS] -- skip when a fixture archive or a required tool is absent
qa_fixture() {
  local name=$1 file=$2 code=$3 needs=${4-}
  local tool
  for tool in ${=needs}; do
    if (( ! $+commands[$tool] )); then
      qa_skip "$name" "missing $tool"
      return 0
    fi
  done
  if [[ ! -e $scratch/$file ]]; then
    qa_skip "$name" "missing fixture ${file:t}"
    return 0
  fi
  qa "$name" "$code"
}

network_enabled() {
  [[ ${QA_SKIP_NETWORK:-0} == 1 ]] && return 1
  return 0
}

[[ ${QA_LIBRARY_ONLY:-0} == 1 ]] && return 0

print -r -- "repository: $repo_dir"
print -r -- "work dir:   $work_dir"
print -r -- ''

print -r -- '== Navigation =='
qa 'dots: ..'            'cd nav/sub/deep && .. && [[ $PWD == */nav/sub ]]'
qa 'dots: ...'           'cd nav/sub/deep && ... && [[ $PWD == */nav ]]'
qa 'dots: ....'          'cd nav/sub/deep && .... && [[ $PWD == *scratch ]]'
qa 'dash returns'        'cd nav && cd sub && - && [[ $PWD == */nav ]]'
qa_opt 'zoxide z jump' zoxide \
  "cd ${(q)work_dir} && zoxide add ${(q)scratch} >/dev/null 2>&1; z ${(q)scratch} >/dev/null 2>&1 && [[ \$PWD == ${(q)scratch} ]]"
qa 'mkcd creates+enters' 'rm -rf qa-mkcd; mkcd qa-mkcd && [[ -d $PWD && $PWD == */qa-mkcd ]]'
qa 'croot enters root'   'cd gitrepo && mkdir -p x/y && cd x/y && croot && [[ $PWD == */gitrepo ]]'

print -r -- '== Files =='
qa_nz 'ls'          'ls'
qa_nz 'll'          'll'
qa_nz 'la'          'la'
qa_nz 'lt'          'lt'
qa 'cat'            'cat files/a.txt | grep TODO'
qa 'peek' 'peek files/a.txt | grep TODO'
qa_nz 'dusage'      'dusage . 5'
qa_nz 'bigfiles'    'bigfiles files 5'
qa 'extract tar.gz' 'rm -rf out && mkdir out && extract --destination out files/sample.tar.gz && [[ -f out/archive-src.txt ]]'
qa_fixture 'extract zip' files/sample.zip \
  'rm -rf out-zip && mkdir out-zip && extract --destination out-zip files/sample.zip && [[ -f out-zip/z.txt ]]' unzip
qa_fixture 'extract tar.xz' files/sample.tar.xz \
  'rm -rf out-xz && mkdir out-xz && extract --destination out-xz files/sample.tar.xz && [[ -f out-xz/archive-src.txt ]]'
qa_fixture 'extract tar.bz2' files/sample.tar.bz2 \
  'rm -rf out-bz && mkdir out-bz && extract --destination out-bz files/sample.tar.bz2 && [[ -f out-bz/archive-src.txt ]]'
qa_fixture 'extract --keep' files/sample.tar.gz \
  'rm -rf out-keep && mkdir out-keep && extract --keep --destination out-keep files/sample.tar.gz && [[ -f files/sample.tar.gz && -f out-keep/archive-src.txt ]]'
qa 'grep match'     'grep TODO files/a.txt >/dev/null'
qa 'diff differs'   'diff files/a.txt files/b.txt >/dev/null; (( $? == 1 ))'
qa 'diff equal'     'diff files/a.txt files/a.txt >/dev/null'
qa 'ff' 'ff a.txt files | grep a.txt'
qa 'ft' 'ft TODO files | grep TODO'

print -r -- '== Git =='
qa 'glog' 'cd gitrepo && glog | grep second'
qa_nz 'gitcount'    'cd gitrepo && gitcount'
qa_nz 'gcount'      'cd gitrepo && gcount'
qa 'gun resets'     'rm -rf ../gunrepo && cp -r gitrepo ../gunrepo && cd ../gunrepo && gun >/dev/null && [[ $(git rev-list --count HEAD) == 1 ]] && ! git diff --cached --quiet'
qa 'gpr pulls with rebase' 'cd gpr-clone && gpr >/dev/null 2>&1 && [[ -f upstream-only.txt ]]'

print -r -- '== System =='
if network_enabled; then
  qa_opt 'weather' curl 'weather'
  qa_opt 'headers' curl 'headers https://example.com'
  qa_opt 'myip'    curl 'myip'
else
  qa_skip 'weather' 'QA_SKIP_NETWORK=1'
  qa_skip 'headers' 'QA_SKIP_NETWORK=1'
  qa_skip 'myip'    'QA_SKIP_NETWORK=1'
fi
qa_opt_nz 'ports'   ss 'ports'
qa_nz 'path'        'path'
qa 'fanprofile help' 'fanprofile --help | grep Usage'

print -r -- '== Meta =='
qa_nz 'tips'        'tips'
qa_nz 'zhelp plain' 'zhelp --plain package'
qa_opt_nz 'zdoctor' 'curl lsd ss zoxide' 'zdoctor'
if network_enabled; then
  qa_opt_nz 'zdoctor --network --secrets' 'curl lsd ss zoxide secret-tool' 'zdoctor --network --secrets'
else
  qa_skip 'zdoctor --network --secrets' 'QA_SKIP_NETWORK=1'
fi

print -r -- '== Meta (global aliases enabled) =='
qa 'G alias'        '[[ $(print "aaa\nbbb" G bbb) == bbb ]]' 'ZSH_GLOBAL_ALIASES=1'
qa 'W alias'        '[[ $(print "aaa\nbbb" W) == 2 ]]' 'ZSH_GLOBAL_ALIASES=1'
qa 'H alias'        '[[ $(print -l {1..15} H) == $(print -l {1..10}) ]]' 'ZSH_GLOBAL_ALIASES=1'
qa 'T alias'        '[[ $(print -l {1..15} T) == $(print -l {6..15}) ]]' 'ZSH_GLOBAL_ALIASES=1'
qa 'L alias'        '[[ -n ${galiases[L]} && $(print -l {1..3} L | wc -l) -eq 3 ]]' 'ZSH_GLOBAL_ALIASES=1'
qa 'NE alias'       '[[ -n ${galiases[NE]} && $( print -r -- x NE ) == x ]]' 'ZSH_GLOBAL_ALIASES=1'
qa 'NUL alias'      '[[ -z $(print hello NUL) ]]' 'ZSH_GLOBAL_ALIASES=1'

print -r -- '== Theme =='
qa_nz 'ztheme list'    'ztheme list'
qa_nz 'ztheme current' 'ztheme current'
qa_nz 'ztheme show'    'ztheme show nord'
qa_nz 'ztheme export'  'ztheme export nord'
qa 'ztheme use/reset'  'ztheme use nord >/dev/null && ztheme reset >/dev/null'
qa 'ztheme invalid'    'ztheme use invalid-theme >/dev/null 2>&1; (( $? != 0 ))'

print -r -- '== Packages (read-only) =='
qa_nz 'upkg managers'      'upkg managers'
qa_opt 'npkg help' nix      'npkg help'
qa_opt 'npkg list (empty ok)' nix 'npkg list'
qa_opt 'upkg search pacman' pacman 'upkg search ripgrep --only=pacman'
if network_enabled; then
  qa_opt 'upkg outdated pacman' pacman 'upkg outdated --only=pacman'
  qa_opt 'upkg plan pacman' pacman 'upkg plan --only=pacman'
  qa_opt 'upkg upgrade --dry-run pacman' pacman 'upkg upgrade --dry-run --only=pacman'
else
  qa_skip 'upkg outdated pacman' 'QA_SKIP_NETWORK=1'
  qa_skip 'upkg plan pacman' 'QA_SKIP_NETWORK=1'
  qa_skip 'upkg upgrade --dry-run pacman' 'QA_SKIP_NETWORK=1'
fi

print -r -- '== Credentials (read-only) =='
qa_opt 'cgm check'  secret-tool 'cgm check'
qa_opt 'cgm list'   secret-tool 'cgm list'
qa_opt 'cgm status' secret-tool 'cgm status'

print -r -- ''
print -r -- "safe sweep: $n_pass passed, $n_fail failed, $n_skip skipped"
if (( n_fail > 0 )); then
  print -r -- "failed: ${(j:, :)failures}"
  exit 1
fi
