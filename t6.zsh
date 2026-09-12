source "$HOME/.config/zsh/init.zsh" >/dev/null 2>&1
zdoctor >/tmp/opencode/zdoc2.out 2>&1
print -r -- "zdoctor_rc=$?"
