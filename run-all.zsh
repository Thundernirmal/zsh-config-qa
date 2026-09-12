#!/usr/bin/env zsh
# Private local gate. Full validation by default; partial runs cannot approve release.
emulate -L zsh
exec python3 "${0:A:h}/release.py" "$@"
