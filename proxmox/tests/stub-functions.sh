#!/usr/bin/env bash
# Stand-ins for the community-scripts engine functions an install script uses
# (passed in as $FUNCTIONS_FILE_PATH, exactly like the real engine does).
# Test harness only: they print what would happen and do the minimum needed.

STUB_STD_LOG="${STUB_STD_LOG:-/var/log/stub-std.log}"

silent() { "$@" >>"$STUB_STD_LOG" 2>&1; }
STD="silent"

msg_info() { echo "[info] $*"; }
msg_ok() { echo "[ok] $*"; }
msg_warn() { echo "[warn] $*"; }
msg_error() { echo "[error] $*" >&2; }
msg_custom() { echo "[${1:-}] ${3:-}"; }

# shellcheck disable=SC2034  # used by the install script
color() { YW="" GN="" RD="" BL="" CL="" BOLD="" TAB="" INFO="" GATEWAY=""; }
verb_ip6() { :; }
catch_errors() {
  set -Eeuo pipefail
  trap 'echo "[stub] error at line $LINENO: $BASH_COMMAND" >&2' ERR
}
setting_up_container() { msg_ok "Set up Container OS (stub)"; }
network_check() { msg_ok "Network check (stub)"; }
update_os() {
  export DEBIAN_FRONTEND=noninteractive
  $STD apt-get update
  msg_ok "Updated package lists (stub)"
}
motd_ssh() { :; }
customize() { :; }
cleanup_lxc() { :; }
