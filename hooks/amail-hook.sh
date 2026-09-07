#!/bin/zsh
# Stable shim: harness hook configs point HERE and this command line never
# changes (Codex trusts hooks per command hash; an edit silently disables
# the hook until re-approved). All behavior lives in `amail hook`.
"${AMAIL_BIN:-amail}" hook "$@"
exit 0
