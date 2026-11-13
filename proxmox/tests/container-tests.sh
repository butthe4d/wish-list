#!/usr/bin/env bash
# Runs inside a plain debian:13 container (started by run-container-tests.sh)
# with the repo mounted read-only at /repo. Installs Wish List with the real
# install script (engine functions and systemctl stubbed) and checks the result.
set -uo pipefail

REPO="${REPO:-/repo}"
PASS=0
FAIL=0
ok() { PASS=$((PASS + 1)); echo "PASS: $1"; }
bad() { FAIL=$((FAIL + 1)); echo "FAIL: $1"; }
check() { # description, command...
  local d="$1"
  shift
  if "$@"; then ok "$d"; else bad "$d"; fi
}
eq() { # description, expected, actual
  if [[ "$2" == "$3" ]]; then ok "$1"; else bad "$1 (expected [$2], got [$3])"; fi
}
finish() {
  echo
  echo "container tests: $PASS passed, $FAIL failed"
  [[ $FAIL -eq 0 ]]
  exit
}

export DEBIAN_FRONTEND=noninteractive
install -m 755 "$REPO/proxmox/tests/stubs/systemctl" /usr/local/bin/systemctl
install -m 755 "$REPO/proxmox/tests/stubs/journalctl" /usr/local/bin/journalctl

ENV_FILE=/etc/wishlist/wishlist.env
LIB=/usr/local/lib/wishlist/wishlist-lib.sh
collect() { (wl_collect_settings); } # own shell: fresh answer descriptor
health() { curl -fsS --max-time 5 http://127.0.0.1:8000/healthz 2>/dev/null; }
app_pid() { cat /run/stub-systemd/wishlist.pid 2>/dev/null; }

# --- 1. Unattended install -----------------------------------------------------
echo "== unattended install (this takes a few minutes) =="
FUNCTIONS_FILE_PATH="$(cat "$REPO/proxmox/tests/stub-functions.sh")"
export FUNCTIONS_FILE_PATH
if WISHLIST_SOURCE_DIR="$REPO" WISHLIST_UNATTENDED=1 \
  bash "$REPO/proxmox/install/wishlist-install.sh" </dev/null >/tmp/install.out 2>&1; then
  ok "unattended install exits 0"
else
  bad "unattended install exits 0"
  tail -n 40 /tmp/install.out
  tail -n 40 /var/log/stub-std.log 2>/dev/null
  finish
fi

eq "/healthz answers ok" "ok" "$(health)"
check "creds file has the admin link" grep -q '^Admin link: http://' /root/wishlist.creds
eq "creds file is 600 root" "600 root" "$(stat -c '%a %U' /root/wishlist.creds)"
check "install output (not a terminal) points to the creds file" grep -q 'sign-in link is in /root/wishlist.creds' /tmp/install.out
eq "settings file is 640 root:wishlist" "640 root:wishlist" "$(stat -c '%a %U:%G' "$ENV_FILE")"
eq "version file records the ref" "local" "$(cat /opt/wishlist_version.txt)"
check "lib installed" test -f "$LIB"
check "wishlist-settings installed" test -x /usr/local/bin/wishlist-settings
check "unit installed and enabled" test -e /etc/systemd/system/multi-user.target.wants/wishlist.service
check "venv in /opt/wishlist/.venv" test -x /opt/wishlist/.venv/bin/python
check "translations compiled" test -f /opt/wishlist/wishlist/translations/pl/LC_MESSAGES/messages.mo
eq "data dir owned by wishlist" "wishlist" "$(stat -c %U /var/lib/wishlist)"
check "database created" test -f /var/lib/wishlist/wishlist.db
eq "app runs as wishlist" "$(id -u wishlist)" "$(awk '/^Uid:/{print $2}' "/proc/$(app_pid)/status" 2>/dev/null)"
workers_ok=1
n_gunicorn=0
for p in /proc/[0-9]*; do
  tr '\0' ' ' <"$p/cmdline" 2>/dev/null | grep -q 'gunicorn' || continue
  n_gunicorn=$((n_gunicorn + 1))
  [[ "$(awk '/^Uid:/{print $2}' "$p/status")" == "$(id -u wishlist)" ]] || workers_ok=0
done
check "gunicorn master and workers found" test "$n_gunicorn" -ge 3
eq "every gunicorn process runs as wishlist" 1 "$workers_ok"

# shellcheck source=/dev/null
. "$LIB"
eq "SECRET_KEY generated (64 hex)" 1 "$([[ "$(wl_settings_get SECRET_KEY)" =~ ^[0-9a-f]{64}$ ]] && echo 1)"
eq "DATABASE_URL" "sqlite:////var/lib/wishlist/wishlist.db" "$(wl_settings_get DATABASE_URL)"
eq "DATA_DIR" "/var/lib/wishlist" "$(wl_settings_get DATA_DIR)"
eq "default ADMIN_NAME" "Admin" "$(wl_settings_get ADMIN_NAME)"
eq "default ADMIN_EMAIL" "admin@example.com" "$(wl_settings_get ADMIN_EMAIL)"
eq "default DEFAULT_LANGUAGE" "en" "$(wl_settings_get DEFAULT_LANGUAGE)"
eq "default WISHLIST_CHANNEL" "main" "$(wl_settings_get WISHLIST_CHANNEL)"
eq "default WISHLIST_REPO" "butthe4d/wish-list" "$(wl_settings_get WISHLIST_REPO)"
eq "no SMTP by default" "" "$(wl_settings_get SMTP_HOST)"
eq "BASE_URL defaults to container IP" "http://$(hostname -I | awk '{print $1}'):8000" "$(wl_settings_get BASE_URL)"
for k in BASE_URL SECRET_KEY DATABASE_URL DATA_DIR ADMIN_NAME ADMIN_EMAIL DEFAULT_LANGUAGE SMTP_HOST SMTP_PORT \
  SMTP_SECURITY SMTP_USER SMTP_PASSWORD SMTP_FROM GSMSERVICE_TOKEN GSMSERVICE_SENDER FETCH_DENY_CIDRS \
  WISHLIST_CHANNEL WISHLIST_REPO; do
  grep -q "^$k=" "$ENV_FILE" || bad "settings file has $k"
done
app_env="$(WL_LEAK_CHECK=1 wl_run_as_app /usr/bin/env)"
check "wl_run_as_app passes the settings" grep -q '^SECRET_KEY=' <<<"$app_env"
check "wl_run_as_app drops the caller's environment" bash -c "! grep -q '^WL_LEAK_CHECK=' <<<\"\$1\"" _ "$app_env"
eq "wl_run_as_app runs as wishlist" wishlist "$(wl_run_as_app /usr/bin/id -un)"
admin_key="$(sed -n 's|^Admin link: .*/u/||p' /root/wishlist.creds)"
check "admin key not in install output (not a terminal)" bash -c "! grep -qF -- '$admin_key' /tmp/install.out"
check "admin key not in service log" bash -c "! grep -qF -- '$admin_key' /var/log/stub-systemd/wishlist.log"
check "admin key not in engine log" bash -c "! grep -qF -- '$admin_key' /var/log/stub-std.log"

# --- 2. Settings file quoting ----------------------------------------------------
echo "== settings file quoting =="
T=/tmp/quoting.env
rm -f "$T"
tricky=(
  "p'a\$s #x"
  'back\slash \n \\ end\'
  "  lead 'and' trail  "
  'dq"x ${HOME} $HOME `id` $(touch /tmp/pwned) %h'
  "'"
  "x''y"
  "tab	here"
  ""
  "#hash ; & |"
)
i=0
for v in "${tricky[@]}"; do
  i=$((i + 1))
  WISHLIST_ENV=$T wl_settings_set SMTP_PASSWORD "$v" || bad "set tricky value $i"
  eq "roundtrip tricky value $i" "$v" "$(WISHLIST_ENV=$T wl_settings_get SMTP_PASSWORD)"
  # shellcheck disable=SC2016
  eq "shell/systemd-style parse of value $i" "$v" "$(env -i bash -c 'set -a; . "$1"; printf %s "$SMTP_PASSWORD"' _ "$T")"
done
check "parsing never executes values" test ! -e /tmp/pwned
eq "one line per key after repeated sets" 1 "$(grep -c '^SMTP_PASSWORD=' "$T")"
WISHLIST_ENV=$T wl_settings_set SMTP_PASSWORD "p'a\$s #x"
eq "brief example roundtrip" "p'a\$s #x" "$(WISHLIST_ENV=$T wl_settings_get SMTP_PASSWORD)"
check "newline in a value is rejected" bash -c ". $LIB; ! WISHLIST_ENV=$T wl_settings_set SMTP_PASSWORD \$'a\nb' 2>/dev/null"
check "unknown key rejected" bash -c ". $LIB; ! WISHLIST_ENV=$T wl_settings_set 'BAD KEY' x 2>/dev/null"

# --- 3. Validation ---------------------------------------------------------------
echo "== validation =="
valid() { if wl_validate "$1" "$2" 2>/dev/null; then ok "valid $1=[$2]"; else bad "valid $1=[$2]"; fi; }
invalid() { if wl_validate "$1" "$2" 2>/dev/null; then bad "invalid $1=[$2]"; else ok "invalid $1=[$2]"; fi; }
valid BASE_URL "https://wish.example.com"
valid BASE_URL "http://10.0.0.5:8000"
invalid BASE_URL "wish.example.com"
invalid BASE_URL "ftp://x"
valid ADMIN_EMAIL "a@b.c"
invalid ADMIN_EMAIL "nope"
valid SMTP_FROM ""
invalid SMTP_FROM "no-at"
valid DEFAULT_LANGUAGE pl
invalid DEFAULT_LANGUAGE de
valid SMTP_PORT 465
invalid SMTP_PORT abc
invalid SMTP_PORT 70000
valid SMTP_SECURITY ssl
invalid SMTP_SECURITY tls
valid WISHLIST_CHANNEL releases
invalid WISHLIST_CHANNEL nightly
valid FETCH_DENY_CIDRS "203.0.113.7/32, 2001:db8::/48"
valid FETCH_DENY_CIDRS ""
invalid FETCH_DENY_CIDRS garbage
invalid SECRET_KEY short
invalid NOT_A_SETTING x
msg="$(wl_validate FETCH_DENY_CIDRS garbage 2>&1)"
check "validation message names the setting" grep -q FETCH_DENY_CIDRS <<<"$msg"

# --- 4. wishlist-settings --get/--set ---------------------------------------------
echo "== wishlist-settings =="
printf '%s' "p'a\$s #x" | WISHLIST_HEALTH_TIMEOUT=60 wishlist-settings --set SMTP_PASSWORD - >/tmp/set.out 2>&1
eq "--set KEY - (value on stdin) exits 0" 0 "$?"
check "--help documents --set KEY -" bash -c "wishlist-settings --help | grep -q -- '--set KEY -'"
eq "--get returns the value byte-for-byte" "p'a\$s #x" "$(wishlist-settings --get SMTP_PASSWORD)"
eq "service healthy after --set" ok "$(health)"
eq "service saw the value (bash-loaded env)" "p'a\$s #x" \
  "$(setpriv --reuid=wishlist --regid=wishlist --init-groups cat "/proc/$(app_pid)/environ" | tr '\0' '\n' | sed -n 's/^SMTP_PASSWORD=//p')"
before="$(sha256sum "$ENV_FILE")"
out="$(wishlist-settings --set FETCH_DENY_CIDRS garbage 2>&1)"
rc=$?
check "--set garbage CIDR exits non-zero" test "$rc" -ne 0
check "--set garbage CIDR explains why" grep -q FETCH_DENY_CIDRS <<<"$out"
eq "rejected value leaves the file unchanged" "$before" "$(sha256sum "$ENV_FILE")"
pid_before="$(app_pid)"
out="$(WISHLIST_HEALTH_TIMEOUT=60 wishlist-settings --set DATABASE_URL sqlite:////nonexistent/dir/x.db 2>&1)"
rc=$?
check "app-level failure exits non-zero" test "$rc" -ne 0
eq "app-level failure restores the previous file" "$before" "$(sha256sum "$ENV_FILE")"
eq "app-level failure keeps 640 root:wishlist" "640 root:wishlist" "$(stat -c '%a %U:%G' "$ENV_FILE")"
eq "service is up again after restore" ok "$(health)"
check "service was restarted" test "$pid_before" != "$(app_pid)"
check "failure shows the service log" grep -qi "error\|exception\|unable" <<<"$out"

# Several settings in one call: one restart.
pid_before="$(app_pid)"
out="$(printf '%s' 'tok%en' | WISHLIST_HEALTH_TIMEOUT=60 wishlist-settings --set ADMIN_NAME 'Multi One' GSMSERVICE_TOKEN - SMTP_PORT 2525 2>&1)"
eq "--set with several pairs exits 0" 0 "$?"
check "--set with several pairs names them" grep -q "Saved ADMIN_NAME, GSMSERVICE_TOKEN, SMTP_PORT" <<<"$out"
eq "--set several: first value" "Multi One" "$(wishlist-settings --get ADMIN_NAME)"
eq "--set several: stdin value" "tok%en" "$(wishlist-settings --get GSMSERVICE_TOKEN)"
eq "--set several: last value" 2525 "$(wishlist-settings --get SMTP_PORT)"
check "--set several: restarted" test "$pid_before" != "$(app_pid)"
before="$(sha256sum "$ENV_FILE")"
out="$(wishlist-settings --set ADMIN_NAME ok SMTP_PORT notaport 2>&1)"
check "--set several: one invalid value fails" test "$?" -ne 0
eq "--set several: nothing saved when one is invalid" "$before" "$(sha256sum "$ENV_FILE")"
check "--set with a missing value is a usage error" bash -c "wishlist-settings --set ADMIN_NAME x SMTP_PORT >/dev/null 2>&1; [ \$? = 2 ]"
check "--set with two stdin values is refused" bash -c "echo x | wishlist-settings --set ADMIN_NAME - SMTP_USER - >/dev/null 2>&1; [ \$? -ne 0 ]"
eq "--set refused: nothing saved" "$before" "$(sha256sum "$ENV_FILE")"

# systemd's start limit (StartLimitBurst=5 per 120 s, modelled by the stub)
# counts manual restarts: quick changes must not end with the app down.
echo "== start limit: 7 quick --set calls =="
systemctl reset-failed wishlist
first_refused=""
for i in 1 2 3 4 5 6; do systemctl restart wishlist >/dev/null 2>&1 || { first_refused=$i; break; }; done
eq "stub models the start limit (6th quick restart refused)" "6 inactive" "$first_refused $(systemctl is-active wishlist)"
systemctl reset-failed wishlist
systemctl start wishlist
wl_health 60 >/dev/null 2>&1
fails=0
for i in 1 2 3 4 5 6 7; do
  WISHLIST_HEALTH_TIMEOUT=60 wishlist-settings --set ADMIN_NAME "Quick $i" >/tmp/quick.out 2>&1 || { fails=$((fails + 1)); tail -n 5 /tmp/quick.out; }
done
eq "7 consecutive --set calls all succeed" 0 "$fails"
eq "last value saved" "Quick 7" "$(wishlist-settings --get ADMIN_NAME)"
eq "app up after 7 quick --set calls" ok "$(health)"
WISHLIST_HEALTH_TIMEOUT=60 wishlist-settings --set ADMIN_NAME Admin GSMSERVICE_TOKEN '' SMTP_PORT 587 >/dev/null 2>&1
eq "settings put back for the next tests" "Admin||587" \
  "$(wishlist-settings --get ADMIN_NAME)|$(wishlist-settings --get GSMSERVICE_TOKEN)|$(wishlist-settings --get SMTP_PORT)"

# --- 5. Interactive answers (read fallback) ---------------------------------------
echo "== interactive configuration (scripted answers) =="
A=/tmp/answers
cat >"$A" <<'ANS'
y
https://wish.example.org
Ola
not-an-email
ola@example.org
de
pl
y
smtp.example.org
465
ssl
mailer
s3cr'et
wish@example.org
n
203.0.113.7/32
ANS
rm -f /tmp/interactive.env
WISHLIST_ENV=/tmp/interactive.env WISHLIST_ANSWERS=$A collect >/tmp/interactive.out 2>&1
eq "wl_collect_settings exits 0" 0 "$?"
ie() { WISHLIST_ENV=/tmp/interactive.env wl_settings_get "$1"; }
eq "answers: BASE_URL" "https://wish.example.org" "$(ie BASE_URL)"
eq "answers: ADMIN_NAME" "Ola" "$(ie ADMIN_NAME)"
eq "answers: ADMIN_EMAIL (after a rejected answer)" "ola@example.org" "$(ie ADMIN_EMAIL)"
eq "answers: DEFAULT_LANGUAGE (after a rejected answer)" "pl" "$(ie DEFAULT_LANGUAGE)"
eq "answers: SMTP_HOST" "smtp.example.org" "$(ie SMTP_HOST)"
eq "answers: SMTP_PORT" "465" "$(ie SMTP_PORT)"
eq "answers: SMTP_SECURITY" "ssl" "$(ie SMTP_SECURITY)"
eq "answers: SMTP_PASSWORD" "s3cr'et" "$(ie SMTP_PASSWORD)"
eq "answers: no SMS" "" "$(ie GSMSERVICE_TOKEN)"
eq "answers: FETCH_DENY_CIDRS" "203.0.113.7/32" "$(ie FETCH_DENY_CIDRS)"
check "rejected answers are explained" grep -q "DEFAULT_LANGUAGE" /tmp/interactive.out
rm -f /tmp/unattended.env
printf 'n\n' >"$A"
WISHLIST_ENV=/tmp/unattended.env WISHLIST_ANSWERS=$A collect >/dev/null 2>&1
eq "answering no keeps defaults" "admin@example.com" "$(WISHLIST_ENV=/tmp/unattended.env wl_settings_get ADMIN_EMAIL)"

printf '%s\n' BASE_URL "https://menu.example.org" save >"$A"
WISHLIST_ANSWERS=$A WISHLIST_HEALTH_TIMEOUT=60 wishlist-settings >/tmp/menu.out 2>&1
eq "menu exits 0" 0 "$?"
eq "menu edit saved" "https://menu.example.org" "$(wishlist-settings --get BASE_URL)"
eq "service healthy after menu save" ok "$(health)"
check "menu masks the SMTP password" bash -c "! grep -qF \"p'a\" /tmp/menu.out"

# --- 5b. Clearing, timeouts, cancel, engine modes -------------------------------
echo "== prompts: clearing, timeouts, cancel/Esc, engine modes =="
secret_before="$(WISHLIST_ENV=/tmp/interactive.env wl_settings_get SECRET_KEY)"
printf '%s\n' y "" "" "" "" "" "" "" "" - "" "" "" - >"$A"
WISHLIST_ENV=/tmp/interactive.env WISHLIST_ANSWERS=$A collect >/dev/null 2>&1
eq "re-run keeps current values as defaults" "https://wish.example.org" "$(ie BASE_URL)"
eq "'-' clears a value (SMTP_USER)" "" "$(ie SMTP_USER)"
eq "empty answer keeps a secret" "s3cr'et" "$(ie SMTP_PASSWORD)"
eq "'-' clears FETCH_DENY_CIDRS" "" "$(ie FETCH_DENY_CIDRS)"
eq "re-run keeps SECRET_KEY" "$secret_before" "$(ie SECRET_KEY)"

printf 'y\n' >"$A"
rm -f /tmp/mode.env
MODE=appdefaults WISHLIST_ENV=/tmp/mode.env WISHLIST_ANSWERS=$A collect </dev/null >/tmp/mode.out 2>&1
check "engine MODE=appdefaults asks nothing" bash -c "! grep -q 'Configure Wish List now' /tmp/mode.out"
eq "engine MODE=appdefaults uses defaults" "admin@example.com" "$(WISHLIST_ENV=/tmp/mode.env wl_settings_get ADMIN_EMAIL)"
for m in mydefaults-global mydefaults userdefaults-app appdefaults-x 3 4; do
  check "engine MODE=$m counts as unattended" env MODE="$m" WISHLIST_ANSWERS="$A" bash -c ". $LIB; wl_is_unattended"
done
for m in default advanced generated 33 xmydefaults; do
  check "engine MODE=$m does not count as unattended" env MODE="$m" WISHLIST_ANSWERS="$A" bash -c ". $LIB; ! wl_is_unattended"
done

rm -f /tmp/fifo /tmp/timeout.env
mkfifo /tmp/fifo
sleep 60 >/tmp/fifo &
holder=$!
start=$SECONDS
WISHLIST_PROMPT_TIMEOUT=2 WISHLIST_ENV=/tmp/timeout.env WISHLIST_ANSWERS=/tmp/fifo collect >/dev/null 2>&1
eq "no answer to the first question: exits 0" 0 "$?"
check "first question times out quickly" test $((SECONDS - start)) -lt 10
eq "first-question timeout means defaults" "admin@example.com" "$(WISHLIST_ENV=/tmp/timeout.env wl_settings_get ADMIN_EMAIL)"
kill "$holder" 2>/dev/null
rm -f /tmp/fifo /tmp/timeout.env
mkfifo /tmp/fifo
{ echo y; sleep 60; } >/tmp/fifo &
holder=$!
start=$SECONDS
WISHLIST_FIELD_TIMEOUT=1 WISHLIST_ENV=/tmp/timeout.env WISHLIST_ANSWERS=/tmp/fifo collect >/dev/null 2>&1
eq "unanswered fields: exits 0" 0 "$?"
check "unanswered fields time out" test $((SECONDS - start)) -lt 20
eq "unanswered fields keep defaults" "en" "$(WISHLIST_ENV=/tmp/timeout.env wl_settings_get DEFAULT_LANGUAGE)"
kill "$holder" 2>/dev/null

# whiptail paths, on a pseudo-terminal (script) with a scripted whiptail.
mkdir -p /tmp/fakewt
cat >/tmp/fakewt/whiptail <<'WT'
#!/usr/bin/env bash
# STUB_WT: esc|cancel -> Esc/Cancel everywhere except "Configure now?" (yes);
# enter -> accept every prefilled value; hang -> never answer.
printf '%q ' "$@" >>/tmp/wt.log; echo >>/tmp/wt.log
args="$*"
[[ "$STUB_WT" == hang ]] && exec sleep 600
if [[ "$args" == *--yesno* ]]; then
  [[ "$args" == *"Configure Wish List now?"* ]] && exit 0
  [[ "$STUB_WT" == enter ]] && exit 0
fi
case "$STUB_WT" in
esc) exit 255 ;;
cancel) exit 1 ;;
enter)
  [[ "$args" == *--passwordbox* ]] && exit 0
  [[ "$args" == *--inputbox* ]] && printf '%s' "${@: -1}" >&2
  exit 0
  ;;
esac
WT
chmod +x /tmp/fakewt/whiptail
on_tty() { # env assignments... -- runs wl_collect_settings on a pty
  script -qec "env PATH=/tmp/fakewt:\$PATH TERM=xterm $* bash -c '. $LIB; wl_collect_settings'" /dev/null >/dev/null 2>&1
}
for mode in esc cancel; do
  cp /tmp/interactive.env /tmp/wt.env
  sum="$(sha256sum </tmp/wt.env)"
  rm -f /tmp/wt.log
  on_tty STUB_WT=$mode WISHLIST_ENV=/tmp/wt.env
  eq "whiptail $mode everywhere: exits 0" 0 "$?"
  check "whiptail $mode: whiptail was used" grep -q -- '--inputbox' /tmp/wt.log
  eq "whiptail $mode keeps every value" "$sum" "$(sha256sum </tmp/wt.env)"
done
cp /tmp/interactive.env /tmp/wt.env
rm -f /tmp/wt.log
on_tty STUB_WT=enter WISHLIST_ENV=/tmp/wt.env
eq "whiptail enter: exits 0" 0 "$?"
eq "empty passwordbox keeps the secret" "s3cr'et" "$(WISHLIST_ENV=/tmp/wt.env wl_settings_get SMTP_PASSWORD)"
check "passwordbox starts empty (secret not on argv)" bash -c "grep -- '--passwordbox' /tmp/wt.log | grep -q \"14 74 ''\" && ! grep -qF 's3cr' /tmp/wt.log"
rm -f /tmp/wt.env
start=$SECONDS
on_tty STUB_WT=hang WISHLIST_PROMPT_TIMEOUT=2 WISHLIST_ENV=/tmp/wt.env
eq "whiptail never answered: exits 0" 0 "$?"
check "whiptail first question times out" test $((SECONDS - start)) -lt 15
eq "whiptail timeout means defaults" "admin@example.com" "$(WISHLIST_ENV=/tmp/wt.env wl_settings_get ADMIN_EMAIL)"

# Plain prompts on a terminal: secrets are read without echo. The answer is
# typed after a pause so the terminal is already in its reading mode.
plain_prompt() { # secret|"" -> what the terminal showed
  rm -f /tmp/prompt.ans
  { sleep 2; printf 'typed-SeCrEt\n'; sleep 1; } |
    script -qec "env TERM=dumb bash -c '. $LIB; wl_ask_input \"SMTP password\" \"\" $1 >/tmp/prompt.ans'" /dev/null 2>&1
}
shown_secret="$(plain_prompt secret)"
check "plain secret prompt does not echo" bash -c "! grep -q typed-SeCrEt <<<\"\$1\"" _ "$shown_secret"
eq "plain secret prompt still reads the answer" "typed-SeCrEt" "$(cat /tmp/prompt.ans 2>/dev/null)"
shown_plain="$(plain_prompt "")"
check "plain non-secret prompt echoes (control)" grep -q typed-SeCrEt <<<"$shown_plain"

# The engine's real SIGHUP shield (from the pinned core.func) runs our
# functions with output passed through, exit status kept, SIGHUP ignored.
core_url="${COMMUNITY_SCRIPTS_CORE_URL:-https://raw.githubusercontent.com/community-scripts/core/665635b6aff5cf1486400d47e6c5aa38f9ba333d}"
if curl -fsSL "$core_url/core/core.func" -o /tmp/core.func; then
  sed -n '/^_cs_shield_hangup() {/,/^}/p' /tmp/core.func >/tmp/shield.sh
  check "extracted _cs_shield_hangup from core.func" grep -q 'trap .. HUP' /tmp/shield.sh
  shield_out="$(bash -c '
    . "$1"; . "$2"
    declare -F _cs_shield_hangup >/dev/null || exit 99
    long_step() { echo "to stdout"; echo "to stderr" >&2; kill -HUP "$BASHPID"; echo "survived"; return 3; }
    out="$(wl_shielded long_step 2>/tmp/shield.err)"; rc=$?
    printf "%s|%s|%s" "$rc" "${out//$'"'"'\n'"'"'/,}" "$(cat /tmp/shield.err)"
  ' _ /tmp/shield.sh "$LIB")"
  eq "real shield runs a function: status, stdout, stderr, survives SIGHUP" "3|to stdout,survived|to stderr" "$shield_out"
else
  bad "could not download core.func from $core_url"
fi

# Default address: first IPv4, else bracketed IPv6.
mkdir -p /tmp/fakehost
printf '#!/bin/sh\necho "$FAKE_I"\n' >/tmp/fakehost/hostname
chmod +x /tmp/fakehost/hostname
eq "container IP prefers IPv4" "10.0.0.7" "$(PATH=/tmp/fakehost:$PATH FAKE_I='2001:db8::5 10.0.0.7' wl_container_ip)"
eq "IPv6-only container IP is bracketed" "[2001:db8::5]" "$(PATH=/tmp/fakehost:$PATH FAKE_I='2001:db8::5' wl_container_ip)"
check "bracketed IPv6 BASE_URL is valid" wl_validate BASE_URL "http://[2001:db8::5]:8000"

# Locking: a held lock blocks settings writes.
before="$(sha256sum "$ENV_FILE")"
flock /run/wishlist/maintenance.lock sleep 6 &
locker=$!
sleep 0.5
out="$(WL_LOCK_TIMEOUT=1 wishlist-settings --set ADMIN_NAME Locked 2>&1)"
rc=$?
check "--set waits for the lock and gives up" test "$rc" -ne 0
check "lock message" grep -q "maintenance task" <<<"$out"
eq "locked: file unchanged" "$before" "$(sha256sum "$ENV_FILE")"
wait "$locker"
eq "lock dir is root-only" "root 700" "$(stat -c '%U %a' /run/wishlist)"
eq "lock file is root-only" "root 600" "$(stat -c '%U %a' /run/wishlist/maintenance.lock)"
check "app user cannot open the lock file" bash -c "! setpriv --reuid=wishlist --regid=wishlist --init-groups bash -c ': </run/wishlist/maintenance.lock' 2>/dev/null"
chmod 644 /run/wishlist/maintenance.lock
check "lock refuses a lock file with loose permissions" bash -c ". $LIB; ! wl_with_lock true 2>/dev/null"
chmod 600 /run/wishlist/maintenance.lock

# Swap rollback: a failing second move puts the old dir back.
rm -rf /tmp/swapt /tmp/swapt.prev
mkdir /tmp/swapt && echo old >/tmp/swapt/marker
_wl_swap_dirs /tmp/does-not-exist /tmp/swapt 2>/dev/null
rc=$?
check "failed swap returns non-zero" test "$rc" -ne 0
eq "failed swap restores the old dir" "old" "$(cat /tmp/swapt/marker 2>/dev/null)"

# --- 6. Backups ------------------------------------------------------------------
echo "== backup / restore / prune =="
systemctl stop wishlist
sqlite3 /var/lib/wishlist/wishlist.db "CREATE TABLE before_backup (x)"
setpriv --reuid=wishlist --regid=wishlist --init-groups bash -c 'mkdir -p /var/lib/wishlist/images && echo pic >/var/lib/wishlist/images/a.jpg'
echo TOPSECRET-ROOT >/root/secret && chmod 600 /root/secret
setpriv --reuid=wishlist --regid=wishlist --init-groups ln -s /root/secret /var/lib/wishlist/images/leak.jpg

b="$(wl_backup)"
eq "wl_backup exits 0" 0 "$?"
check "backup has the database" test -f "$b/wishlist.db"
check "backup has the settings" test -f "$b/wishlist.env"
check "backup has the version" test -f "$b/wishlist_version.txt"
check "backup has images dir" test -d "$b/images"
eq "backup dir is private" "700" "$(stat -c %a "$b")"
check "backup has the pictures" test -f "$b/images/a.jpg"
check "a symlink in images is backed up as a symlink" test -L "$b/images/leak.jpg"
check "root-only file contents never reach the backup" bash -c "! grep -rq TOPSECRET-ROOT '$b'"
check "backup images not owned by the app user" test -z "$(find "$b" -user wishlist)"
check "no root-owned files left in the data dir" test -z "$(find /var/lib/wishlist -user root)"
sqlite3 /var/lib/wishlist/wishlist.db "DROP TABLE before_backup; CREATE TABLE after_backup (x)"
# The app user swaps images for a symlink to a root-owned dir: restore must
# replace the link, not write through it.
mkdir -p /root/link-target
setpriv --reuid=wishlist --regid=wishlist --init-groups bash -c 'rm -rf /var/lib/wishlist/images && ln -s /root/link-target /var/lib/wishlist/images'
wl_restore "$b"
eq "wl_restore exits 0" 0 "$?"
check "restore replaced the images symlink with a dir" bash -c "[ -d /var/lib/wishlist/images ] && [ ! -L /var/lib/wishlist/images ]"
eq "restored picture" "pic" "$(cat /var/lib/wishlist/images/a.jpg 2>/dev/null)"
eq "restored pictures owned by wishlist" "wishlist" "$(stat -c %U /var/lib/wishlist/images/a.jpg 2>/dev/null)"
check "symlink target untouched" test -z "$(ls -A /root/link-target)"
eq "restored database content" "before_backup" \
  "$(sqlite3 /var/lib/wishlist/wishlist.db "SELECT name FROM sqlite_master WHERE name LIKE '%_backup'")"
eq "restored database owned by wishlist" "wishlist" "$(stat -c %U /var/lib/wishlist/wishlist.db)"
for _ in 1 2 3 4; do wl_backup >/dev/null; done
wl_prune_backups
eq "prune keeps 3" 3 "$(find /var/lib/wishlist-backups -mindepth 1 -maxdepth 1 -type d | wc -l)"
systemctl start wishlist
eq "healthy after restore" 0 "$(wl_health 60 >/dev/null 2>&1; echo $?)"

# --- 7. Re-install into the live dir (Task 2's update path) --------------------
echo "== reinstall and swap =="
systemctl stop wishlist
out="$(WISHLIST_SOURCE_DIR=$REPO wl_install_ref local /opt/wishlist 2>&1)"
eq "wl_install_ref exits 0" 0 "$?"
eq "wl_install_ref prints nothing on success" "" "$out"
check "previous code kept as /opt/wishlist.prev" test -x /opt/wishlist.prev/.venv/bin/python
check "no staging dir left" test ! -e /opt/wishlist.new
systemctl start wishlist
eq "moved venv still runs the app" 0 "$(wl_health 60 >/dev/null 2>&1; echo $?)"
mkdir -p /tmp/broken-src
echo "not a project" >/tmp/broken-src/README
ls -id /opt/wishlist >/tmp/inode.before
WISHLIST_SOURCE_DIR=/tmp/broken-src wl_install_ref local /opt/wishlist >/dev/null 2>&1
rc=$?
check "failed install exits non-zero" test "$rc" -ne 0
eq "failed install leaves the live dir alone" "$(cat /tmp/inode.before)" "$(ls -id /opt/wishlist)"
check "failed install cleans up staging" test ! -e /opt/wishlist.new
check "bad ref rejected" bash -c ". $LIB; ! wl_install_ref 'bad ref;x' /tmp/x 2>/dev/null"

# --- 7b. Re-run the install on a terminal, pressing Esc at every question -----
echo "== install re-run with Esc everywhere (a few minutes) =="
base_before="$(wl_settings_get BASE_URL)"
secret_before="$(wl_settings_get SECRET_KEY)"
rm -f /tmp/wt.log
script -qec "env PATH=/tmp/fakewt:\$PATH TERM=xterm STUB_WT=esc WISHLIST_SOURCE_DIR=$REPO \
  bash $REPO/proxmox/install/wishlist-install.sh" /dev/null >/tmp/install2.out 2>&1
eq "re-run with Esc: install exits 0" 0 "$?"
check "re-run with Esc: whiptail was used" grep -q -- '--inputbox' /tmp/wt.log
eq "re-run with Esc: healthy" 0 "$(wl_health 60 >/dev/null 2>&1; echo $?)"
eq "re-run keeps BASE_URL" "$base_before" "$(wl_settings_get BASE_URL)"
eq "re-run keeps SECRET_KEY" "$secret_before" "$(wl_settings_get SECRET_KEY)"
check "re-run kept the previous code as .prev" test -x /opt/wishlist.prev/.venv/bin/python
check "re-run kept the creds file" grep -q '^Admin link: http://' /root/wishlist.creds

# A re-run whose code install fails brings the old app back up.
echo "== failed install re-run =="
rm -rf /tmp/broken-src2 && mkdir -p /tmp/broken-src2
cp -r "$REPO/proxmox" /tmp/broken-src2/
ls -id /opt/wishlist >/tmp/inode.before
WISHLIST_SOURCE_DIR=/tmp/broken-src2 WISHLIST_UNATTENDED=1 \
  bash "$REPO/proxmox/install/wishlist-install.sh" </dev/null >/tmp/install3.out 2>&1
rc=$?
check "failed re-run exits non-zero" test "$rc" -ne 0
check "failed re-run says so" grep -q "previous installation was left in place" /tmp/install3.out
eq "failed re-run leaves the code dir alone" "$(cat /tmp/inode.before)" "$(ls -id /opt/wishlist)"
eq "failed re-run: old app is up again" 0 "$(wl_health 60 >/dev/null 2>&1; echo $?)"

# --- 8. wl_latest_ref against a fake GitHub API ------------------------------------
echo "== wl_latest_ref =="
F=/tmp/fakeapi
mkdir -p "$F/repos/o/r/commits" "$F/repos/o/r/releases" "$F/repos/o/norel"
echo '{"sha": "0123456789abcdef0123456789abcdef01234567", "commit": {}}' >"$F/repos/o/r/commits/main"
echo '{"tag_name": "v1.2.0", "name": "x"}' >"$F/repos/o/r/releases/latest"
echo '[{"name": "v0.9.1", "commit": {}}, {"name": "v0.9.0"}]' >"$F/repos/o/norel/tags"
python3 -m http.server 8765 --bind 127.0.0.1 --directory "$F" >/dev/null 2>&1 &
srv=$!
sleep 1
eq "main channel gives the commit sha" "0123456789abcdef0123456789abcdef01234567" \
  "$(WISHLIST_GITHUB_API=http://127.0.0.1:8765 WISHLIST_REPO=o/r wl_latest_ref main)"
eq "releases channel gives the latest release tag" "v1.2.0" \
  "$(WISHLIST_GITHUB_API=http://127.0.0.1:8765 WISHLIST_REPO=o/r wl_latest_ref releases)"
eq "releases channel falls back to the latest tag" "v0.9.1" \
  "$(WISHLIST_GITHUB_API=http://127.0.0.1:8765 WISHLIST_REPO=o/norel wl_latest_ref releases)"
check "unknown channel fails" bash -c ". $LIB; ! wl_latest_ref nightly 2>/dev/null"
kill "$srv"

finish
