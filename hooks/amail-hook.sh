#!/bin/zsh
# Stable shim: harness hook configs point HERE and this command line never
# changes (Codex trusts hooks per command hash; changing the command line
# silently disables the hook until re-approved). All behavior lives in
# `amail hook`.
#
# The trust covers the command line only, not this file's contents: editing
# the body below keeps the hook active — verified live on codex-cli 0.153.4,
# acceptance gate 11, 2026-09-07.
#
# Resolution order for the binary: AMAIL_BIN, then PATH, then the uv tool
# location — Dock-launched apps do not inherit a login shell's PATH.
amail_bin="${AMAIL_BIN:-}"
if [[ -z "$amail_bin" ]]; then
  amail_bin="$(command -v amail 2>/dev/null)"
fi
if [[ -z "$amail_bin" && -x "$HOME/.local/bin/amail" ]]; then
  amail_bin="$HOME/.local/bin/amail"
fi
if [[ -n "$amail_bin" ]]; then
  "$amail_bin" hook "$@"
fi
exit 0
