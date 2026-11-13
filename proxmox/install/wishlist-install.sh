#!/usr/bin/env bash

# Copyright (c) 2026 butthe4d
# License: Apache-2.0 | https://github.com/butthe4d/wish-list/raw/main/LICENSE
# Source: https://github.com/butthe4d/wish-list

source /dev/stdin <<<"$FUNCTIONS_FILE_PATH"
color
verb_ip6
catch_errors
setting_up_container
network_check
update_os

WISHLIST_SCRIPTS_URL="${COMMUNITY_SCRIPTS_URL:-https://raw.githubusercontent.com/butthe4d/wish-list/main/proxmox}"

# Helper files come from the same place as this script (WISHLIST_SOURCE_DIR:
# a local checkout, used by the test harness).
fetch_misc() { # name destination mode
  if [[ -n "${WISHLIST_SOURCE_DIR:-}" ]]; then
    install -m "$3" "$WISHLIST_SOURCE_DIR/proxmox/misc/$1" "$2"
  else
    curl -fsSL --retry 3 -o "$2" "$WISHLIST_SCRIPTS_URL/misc/$1"
    chmod "$3" "$2"
  fi
}

msg_info "Installing Dependencies"
$STD apt-get install -y python3 python3-venv sqlite3 curl ca-certificates openssl whiptail
msg_ok "Installed Dependencies"

msg_info "Installing Wish List helpers"
mkdir -p /usr/local/lib/wishlist
fetch_misc wishlist-lib.sh /usr/local/lib/wishlist/wishlist-lib.sh 644
fetch_misc wishlist-settings /usr/local/bin/wishlist-settings 755
fetch_misc wishlist.service /etc/systemd/system/wishlist.service 644
# shellcheck source-path=SCRIPTDIR source=../misc/wishlist-lib.sh
source /usr/local/lib/wishlist/wishlist-lib.sh
if ! id -u "$WL_USER" &>/dev/null; then
  useradd --system --home-dir "$WL_DATA_DIR" --no-create-home --shell /usr/sbin/nologin "$WL_USER"
fi
install -d -o "$WL_USER" -g "$WL_USER" -m 750 "$WL_DATA_DIR"
install -d -o root -g "$WL_USER" -m 750 "$(dirname "$WISHLIST_ENV")"
msg_ok "Installed Wish List helpers"

# Prompts (or defaults when unattended / no terminal); writes the settings file.
wl_collect_settings
msg_ok "Saved settings to $WISHLIST_ENV"

msg_info "Installing Wish List (Python packages, this takes a minute)"
if [[ -n "${WISHLIST_SOURCE_DIR:-}" ]]; then
  WISHLIST_REF=local
else
  WISHLIST_REF="${WISHLIST_REF:-$(wl_latest_ref "$(wl_settings_get WISHLIST_CHANNEL)")}"
fi
# Re-run on an existing install: the code dir is swapped, so stop the app first.
was_running=0
if systemctl is-active --quiet "$WL_SERVICE" 2>/dev/null; then
  was_running=1
  systemctl stop "$WL_SERVICE"
fi
# Long steps run shielded from SIGHUP: closing the console must not kill pip.
if ! wl_shielded wl_install_ref "$WISHLIST_REF" "$WL_APP_DIR"; then
  # wl_install_ref left the old code in place: bring the old app back up.
  if [[ $was_running == 1 ]]; then
    wl_service start || true
  fi
  msg_error "Installing Wish List $WISHLIST_REF failed; the previous installation was left in place."
  exit 1
fi
echo "$WISHLIST_REF" >"$WL_VERSION_FILE"
msg_ok "Installed Wish List $WISHLIST_REF"

msg_info "Creating database and first admin"
# Run once here (not only in the unit's ExecStartPre) so the one-time admin
# link is captured from stdout instead of landing in the journal.
boot_out="$(wl_shielded wl_run_as_app "$WL_APP_DIR/.venv/bin/python" -m wishlist.bootstrap)"
admin_link="$(sed -n 's/^Admin link: //p' <<<"$boot_out" | head -n 1)"
unset boot_out
if [[ -n "$admin_link" ]]; then
  (
    umask 077
    printf '# Wish List: open this link to sign in as the first admin. Keep it private.\nAdmin link: %s\n' \
      "$admin_link" >"$WL_CREDS_FILE"
  )
  chmod 600 "$WL_CREDS_FILE"
  msg_ok "Created database and first admin"
else
  msg_ok "Database ready (members already exist, no new admin link)"
fi

msg_info "Starting Wish List"
systemctl daemon-reload
systemctl reset-failed "$WL_SERVICE" 2>/dev/null || true
systemctl enable -q --now "$WL_SERVICE"
wl_health 60
msg_ok "Started Wish List"

motd_ssh
customize
# customize() wrote /usr/bin/update with the slug "wishlist", which in the
# community-scripts catalogue is a different app: its disabled/removed state
# or advisories would block our updates. Same entrypoint, our own slug.
wl_write_update_entrypoint || msg_warn "Could not write /usr/bin/update; run updates with: bash -c \"\$(curl -fsSL ${WISHLIST_SCRIPTS_URL}/ct/wishlist.sh)\""
cleanup_lxc

echo -e "${INFO:-}${YW:-} Wish List is running on $(wl_settings_get BASE_URL)${CL:-}"
if [[ -n "$admin_link" ]]; then
  # Only onto a live terminal: after a console hangup the engine sends this
  # output to its install log, and the link is a login.
  if [[ -t 1 && "${_CS_HANGUP:-0}" != 1 ]]; then
    echo -e "${INFO:-}${YW:-} Sign in as the first admin with this link (also saved in $WL_CREDS_FILE):${CL:-}"
    echo -e "${TAB:-   }Admin link: ${admin_link}"
  else
    echo -e "${INFO:-}${YW:-} The first admin's sign-in link is in $WL_CREDS_FILE (cat $WL_CREDS_FILE)${CL:-}"
  fi
fi
echo -e "${INFO:-}${YW:-} Change settings any time with: wishlist-settings${CL:-}"
