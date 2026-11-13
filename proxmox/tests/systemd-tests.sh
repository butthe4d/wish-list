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

# Start limit on real systemd (StartLimitBurst=5 per StartLimitIntervalSec=120
# in the unit): manual restarts count, so quick settings changes must clear
# it (reset-failed) or the app ends up down. The unit runs a tiny stand-in
# app answering /healthz on port 8000 (drop-in), everything else is real:
# the unit's limits, wishlist-settings and the lib.
cat >/tmp/fakeapp.py <<'PY'
import http.server
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"ok" if self.path == "/healthz" else b"no"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a):
        pass
http.server.HTTPServer.allow_reuse_address = True
http.server.HTTPServer(("127.0.0.1", 8000), H).serve_forever()
PY
chmod 644 /tmp/fakeapp.py
mkdir -p /etc/systemd/system/wishlist.service.d
printf '%s\n' '[Service]' 'ExecStartPre=' 'ExecStart=' 'ExecStart=/usr/bin/python3 /tmp/fakeapp.py' \
  >/etc/systemd/system/wishlist.service.d/test.conf
export WISHLIST_ENV=/etc/wishlist/wishlist.env
: >"$WISHLIST_ENV"
wl_settings_set ADMIN_NAME Admin
chown root:wishlist "$WISHLIST_ENV" && chmod 640 "$WISHLIST_ENV"
systemctl daemon-reload
first_refused=""
for i in 1 2 3 4 5 6; do systemctl restart wishlist >/dev/null 2>&1 || { first_refused=$i; break; }; done
eq "real systemd refuses the 6th quick restart" 6 "$first_refused"
eq "the refusal is the start limit" start-limit-hit "$(systemctl show -p Result --value wishlist)"
systemctl reset-failed wishlist
systemctl start wishlist
wl_health 30 >/dev/null 2>&1
fails=0
for i in 1 2 3 4 5 6 7; do
  WISHLIST_LIB="$REPO/proxmox/misc/wishlist-lib.sh" bash "$REPO/proxmox/misc/wishlist-settings" \
    --set ADMIN_NAME "Quick $i" >/tmp/quick.out 2>&1 || { fails=$((fails + 1)); tail -n 5 /tmp/quick.out; }
done
eq "7 consecutive --set calls succeed on real systemd" 0 "$fails"
eq "last value saved" "Quick 7" "$(wl_settings_get ADMIN_NAME)"
eq "service active after 7 quick --set calls" active "$(systemctl is-active wishlist)"
eq "app answers after 7 quick --set calls" ok "$(curl -fsS --max-time 5 http://127.0.0.1:8000/healthz 2>/dev/null)"
systemctl stop wishlist

echo
echo "systemd tests: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
