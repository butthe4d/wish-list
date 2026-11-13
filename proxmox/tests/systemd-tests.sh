#!/usr/bin/env bash
# Runs inside a debian:13 container booted with systemd (see
# run-container-tests.sh). Checks that systemd's EnvironmentFile parser reads
# the settings file exactly as the lib's wl_settings_get does, and that
# wishlist.service passes `systemd-analyze verify`.
set -uo pipefail

REPO="${REPO:-/repo}"
PASS=0
FAIL=0
eq() {
  if [[ "$2" == "$3" ]]; then
    PASS=$((PASS + 1))
    echo "PASS: $1"
  else
    FAIL=$((FAIL + 1))
    echo "FAIL: $1 (expected [$2], got [$3])"
  fi
}

# shellcheck source-path=SCRIPTDIR source=../misc/wishlist-lib.sh
. "$REPO/proxmox/misc/wishlist-lib.sh"
export WISHLIST_ENV=/tmp/systemd-check.env

values=(
  "p'a\$s #x"
  'back\slash \n \\ end\'
  "  lead 'and' trail  "
  'dq"x ${HOME} $HOME `id` $(id) %h %%'
  "'"
  "x''y"
  "tab	here"
  ""
  "#hash ; & |"
  "plain"
)
i=0
for v in "${values[@]}"; do
  i=$((i + 1))
  wl_settings_set SMTP_PASSWORD "$v"
  eq "lib reads value $i back" "$v" "$(wl_settings_get SMTP_PASSWORD)"
  # shellcheck disable=SC2016
  got="$(systemd-run -q --pipe --wait -p EnvironmentFile="$WISHLIST_ENV" \
    python3 -c 'import os, sys; sys.stdout.write(os.environ.get("SMTP_PASSWORD", "<unset>"))')"
  eq "systemd reads value $i identically" "$v" "$got"
done

# Unit sanity: real user, data dir and executable so verify checks the unit itself.
useradd --system --home-dir /var/lib/wishlist --shell /usr/sbin/nologin wishlist
mkdir -p /var/lib/wishlist /opt/wishlist/.venv/bin /etc/wishlist
ln -sf /usr/bin/python3 /opt/wishlist/.venv/bin/python
touch /etc/wishlist/wishlist.env
cp "$REPO/proxmox/misc/wishlist.service" /etc/systemd/system/wishlist.service
out="$(systemd-analyze verify /etc/systemd/system/wishlist.service 2>&1)"
eq "systemd-analyze verify is clean" "" "$out"

echo
echo "systemd tests: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
