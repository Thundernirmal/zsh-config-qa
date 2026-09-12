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
command git init -q gitrepo
cd -- gitrepo || exit 1
command git config user.email qa@example.com
command git config user.name QA
print -r -- 'one' > f.txt
command git add f.txt
command git commit -qm first
print -r -- 'two' >> f.txt
command git commit -qam second
cd -- .. || exit 1

# --- pull-with-rebase fixture ------------------------------------------
# gpr-clone is created before the upstream commit, so it is behind origin.
command git init -q --bare gpr-origin.git
command git clone -q gpr-origin.git gpr-seed
cd -- gpr-seed || exit 1
command git config user.email qa@example.com
command git config user.name QA
print -r -- 'base' > base.txt
command git add base.txt
command git commit -qm base
command git push -q origin HEAD
cd -- .. || exit 1
command git clone -q gpr-origin.git gpr-clone
cd -- gpr-seed || exit 1
print -r -- 'upstream' > upstream-only.txt
command git add upstream-only.txt
command git commit -qm upstream
command git push -q origin HEAD
cd -- .. || exit 1

# --- zoxide seed -------------------------------------------------------
# Seed the database so `z` has a deterministic target even on a cold db.
if (( $+commands[zoxide] )); then
  command zoxide add "$scratch" >/dev/null 2>&1 || exit 1
fi

print -r -- "fixtures ready under $scratch"
print -r -- "repository under test: $repo_dir"
