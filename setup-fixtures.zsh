#!/usr/bin/env zsh
# Build deterministic fixtures for the live QA sweeps.
#
# Everything is created beneath QA_WORK_DIR (default: ./.work) and can be
# deleted at any time; the repository under test is never modified.

emulate -L zsh
setopt ERR_EXIT NO_UNSET PIPE_FAIL

typeset -r project_dir=${0:A:h}
typeset -r work_dir=${QA_WORK_DIR:-$project_dir/.work}
typeset -r scratch=$work_dir/scratch
typeset -r repo_dir=${ZSH_CONFIG_DIR:-$HOME/.config/zsh}

python3 "$project_dir/qa_common.py" verify || exit 2
[[ ! -L $scratch ]] || { print -u2 "fatal: scratch is a symlink"; exit 2; }
command rm -rf -- "$scratch"
command mkdir -p -- "$scratch"/nav/sub/deep "$scratch"/files
cd -- "$scratch" || exit 1

# Bounded fixture step: every external command that can block gets a deadline
# (ISSUE-0043). GNU coreutils `timeout` is a platform dependency (AGENTS.md).
typeset -gi fixture_timeout=${QA_FIXTURE_TIMEOUT:-60}
fgit() {
  local step=$1
  shift
  if ! command timeout "$fixture_timeout" "$@"; then
    print -u2 -r -- "fatal: fixture step '${step}' failed or exceeded its ${fixture_timeout}s deadline"
    exit 2
  fi
}

# --- files -------------------------------------------------------------
print -r -- 'hello TODO world' > files/a.txt
print -r -- 'second line' >> files/a.txt
print -r -- 'different TODO text' > files/b.txt
print -r -- 'print("hi")' > files/script.py
print -r -- 'data' > files/archive-src.txt
print -r -- 'zipdata' > files/z.txt
command tar czf files/sample.tar.gz -C files archive-src.txt
if (( $+commands[xz] )); then
  command tar cJf files/sample.tar.xz -C files archive-src.txt
fi
if (( $+commands[bzip2] )); then
  command tar cjf files/sample.tar.bz2 -C files archive-src.txt
fi
command python3 -c 'import zipfile; zipfile.ZipFile("files/sample.zip", "w").write("files/z.txt", "z.txt")'

# --- git repository ----------------------------------------------------
fgit 'fixture git step 1' git init -q gitrepo
cd -- gitrepo || exit 1
fgit 'fixture git step 2' git config user.email qa@example.com
fgit 'fixture git step 3' git config user.name QA
print -r -- 'one' > f.txt
fgit 'fixture git step 4' git add f.txt
fgit 'fixture git step 5' git commit -qm first
print -r -- 'two' >> f.txt
fgit 'fixture git step 6' git commit -qam second
cd -- .. || exit 1

# --- pull-with-rebase fixture ------------------------------------------
# gpr-clone is created before the upstream commit, so it is behind origin.
fgit 'fixture git step 7' git init -q --bare gpr-origin.git
fgit 'fixture git step 8' git clone -q gpr-origin.git gpr-seed
cd -- gpr-seed || exit 1
fgit 'fixture git step 9' git config user.email qa@example.com
fgit 'fixture git step 10' git config user.name QA
print -r -- 'base' > base.txt
fgit 'fixture git step 11' git add base.txt
fgit 'fixture git step 12' git commit -qm base
fgit 'fixture git step 13' git push -q origin HEAD
cd -- .. || exit 1
fgit 'fixture git step 14' git clone -q gpr-origin.git gpr-clone
cd -- gpr-seed || exit 1
print -r -- 'upstream' > upstream-only.txt
fgit 'fixture git step 15' git add upstream-only.txt
fgit 'fixture git step 16' git commit -qm upstream
fgit 'fixture git step 17' git push -q origin HEAD
cd -- .. || exit 1

# --- zoxide seed -------------------------------------------------------
# Seed the database so `z` has a deterministic target even on a cold db.
if (( $+commands[zoxide] )); then
  command zoxide add "$scratch" >/dev/null 2>&1 || exit 1
fi

print -r -- "fixtures ready under $scratch"
print -r -- "repository under test: $repo_dir"
