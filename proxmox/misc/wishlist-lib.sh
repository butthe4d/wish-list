#!/usr/bin/env bash
# Wish List helper library for the Proxmox LXC install (community-scripts style).
# Installed at /usr/local/lib/wishlist/wishlist-lib.sh and sourced by the
# install script, `wishlist-settings` and the update routine. It only defines
# functions and variables; it never changes shell options.
#
# Settings file format (/etc/wishlist/wishlist.env, read by systemd's
# EnvironmentFile= and by wl_settings_get, never sourced or eval'd here):
#   one KEY=value per line, value quoted as
#   - 'value'  when it contains no single quote: everything literal;
#   - "value"  otherwise, with \ " $ ` escaped by a backslash.
# The usual shell idiom 'it'\''s' is NOT used: systemd (checked on 257) stops
# treating quotes as quotes after the first closing one, so it reads that as
# it''\''s'. Both forms above read identically in systemd and in a POSIX
# shell. Newlines and carriage returns cannot be represented and are rejected.

WISHLIST_ENV="${WISHLIST_ENV:-/etc/wishlist/wishlist.env}"
WL_APP_DIR="${WL_APP_DIR:-/opt/wishlist}"
WL_DATA_DIR="${WL_DATA_DIR:-/var/lib/wishlist}"
WL_BACKUP_ROOT="${WL_BACKUP_ROOT:-/var/lib/wishlist-backups}"
WL_VERSION_FILE="${WL_VERSION_FILE:-/opt/wishlist_version.txt}"
WL_CREDS_FILE="${WL_CREDS_FILE:-/root/wishlist.creds}"
WL_USER="${WL_USER:-wishlist}"
WL_SERVICE="${WL_SERVICE:-wishlist}"
WL_PORT="${WL_PORT:-8000}"
WL_DEFAULT_REPO="butthe4d/wish-list"
WL_KEEP_BACKUPS=3
WL_LIB_FILE="${WL_LIB_FILE:-/usr/local/lib/wishlist/wishlist-lib.sh}"
WL_SETTINGS_CMD="${WL_SETTINGS_CMD:-/usr/local/bin/wishlist-settings}"
WL_UPDATE_ENTRYPOINT="${WL_UPDATE_ENTRYPOINT:-/usr/bin/update}"
WL_UNIT_FILE="${WL_UNIT_FILE:-/etc/systemd/system/wishlist.service}"
# Defaults for /usr/bin/update when the engine did not pass its bases in. The
# engine pin must match ct/wishlist.sh.
WL_SCRIPTS_URL_DEFAULT="https://raw.githubusercontent.com/butthe4d/wish-list/main/proxmox"
WL_CORE_URL_DEFAULT="https://raw.githubusercontent.com/community-scripts/core/665635b6aff5cf1486400d47e6c5aa38f9ba333d"
# Our slug for the community-scripts website/database lookups. NOT "wishlist":
# that slug belongs to a different app in their catalogue (cmintey/wishlist),
# whose disabled/removed state or breaking-change advisories would otherwise
# block or confuse our installs and updates. Unknown slugs update normally.
WL_SCRIPT_SLUG="butthe4d-wish-list"

# Every key the settings file may hold, in file order.
WL_KEYS=(
  BASE_URL SECRET_KEY DATABASE_URL DATA_DIR ADMIN_NAME ADMIN_EMAIL DEFAULT_LANGUAGE
  SMTP_HOST SMTP_PORT SMTP_SECURITY SMTP_USER SMTP_PASSWORD SMTP_FROM
  GSMSERVICE_TOKEN GSMSERVICE_SENDER FETCH_DENY_CIDRS WISHLIST_CHANNEL WISHLIST_REPO
)

wl_err() { printf '%s\n' "$*" >&2; }

wl_is_key() {
  local k
  for k in "${WL_KEYS[@]}"; do [[ "$k" == "$1" ]] && return 0; done
  return 1
}

# ---------------------------------------------------------------------------
# Settings file
# ---------------------------------------------------------------------------

# Quote a value for the settings file (see the format note at the top).
wl_quote() {
  local v="$1" out="" c i
  if [[ "$v" != *"'"* ]]; then
    printf "'%s'" "$v"
    return 0
  fi
  for ((i = 0; i < ${#v}; i++)); do
    c="${v:i:1}"
    case "$c" in
    '\' | '"' | '$' | '`') out+="\\$c" ;;
    *) out+="$c" ;;
    esac
  done
  printf '"%s"' "$out"
}

# Decode the right-hand side of one settings line. Mirrors systemd's parser
# for the forms wl_quote writes, plus bare words (hand edits).
wl_unquote() {
  local raw="$1" out="" c i n
  n=${#raw}
  case "$raw" in
  "'"*)
    raw="${raw#\'}"
    printf '%s' "${raw%%\'*}"
    ;;
  '"'*)
    for ((i = 1; i < n; i++)); do
      c="${raw:i:1}"
      if [[ "$c" == '\' ]]; then
        i=$((i + 1))
        c="${raw:i:1}"
        case "$c" in
        '\' | '"' | '$' | '`') out+="$c" ;;
        *) out+="\\$c" ;;
        esac
      elif [[ "$c" == '"' ]]; then
        break
      else
        out+="$c"
      fi
    done
    printf '%s' "$out"
    ;;
  *)
    # Unquoted: systemd strips surrounding whitespace.
    raw="${raw#"${raw%%[![:space:]]*}"}"
    printf '%s' "${raw%"${raw##*[![:space:]]}"}"
    ;;
  esac
}

# wl_settings_get KEY: print the value; status 1 if the key is not in the file.
wl_settings_get() {
  local key="$1" line
  [[ -r "$WISHLIST_ENV" ]] || return 1
  while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ "$line" == "$key="* ]]; then
      wl_unquote "${line#"$key"=}"
      return 0
    fi
  done <"$WISHLIST_ENV"
  return 1
}

# wl_settings_set KEY VALUE: validate, then replace (or append) the line
# atomically under the settings lock, keeping the file root:wishlist 640.
wl_settings_set() {
  wl_validate "$1" "$2" || return 1
  wl_with_lock _wl_settings_write "$1" "$2"
}

_wl_settings_write() {
  local key="$1" value="$2" dir tmp line done=0
  dir="$(dirname "$WISHLIST_ENV")"
  mkdir -p "$dir" || return 1
  tmp="$(mktemp "$dir/.wishlist.env.XXXXXX")" || return 1
  {
    if [[ -f "$WISHLIST_ENV" ]]; then
      while IFS= read -r line || [[ -n "$line" ]]; do
        if [[ "$line" == "$key="* ]]; then
          [[ $done == 0 ]] && printf '%s=%s\n' "$key" "$(wl_quote "$value")"
          done=1
        else
          printf '%s\n' "$line"
        fi
      done <"$WISHLIST_ENV"
    fi
    [[ $done == 1 ]] || printf '%s=%s\n' "$key" "$(wl_quote "$value")"
  } >"$tmp" || {
    rm -f "$tmp"
    return 1
  }
  wl_secure_env_file "$tmp" && mv -f "$tmp" "$WISHLIST_ENV"
}

# wl_with_lock CMD...: run CMD holding the Wish List maintenance lock
# (flock on /run/wishlist/maintenance.lock; WL_LOCK_FILE overrides). Settings
# writes, backup/restore and code swaps all take it; nested calls in the same
# process tree just run CMD. The lock lives in a root-only directory: any
# user who can open the file could flock it forever and block maintenance.
wl_with_lock() {
  if [[ "${WL_LOCK_HELD:-0}" == 1 ]]; then
    "$@"
    return
  fi
  local lock="${WL_LOCK_FILE:-/run/wishlist/maintenance.lock}" dir
  dir="$(dirname "$lock")"
  (umask 077 && mkdir -p "$dir") || return 1
  if [[ $EUID -eq 0 && ( -L "$dir" || "$(stat -c '%u %a' "$dir")" != "0 700" ) ]]; then
    wl_err "Refusing to use lock directory $dir: it must be a root-owned 0700 directory."
    return 1
  fi
  local caller_umask
  caller_umask="$(umask)"
  (
    # 077 only for creating the lock file: CMD runs with the caller's umask
    # (a venv built under 077 would be unreadable for the app user).
    umask 077
    exec 9>>"$lock" || exit 1
    umask "$caller_umask"
    if [[ -L "$lock" || ! -O "$lock" || "$(stat -c '%a' "$lock")" != 600 ]]; then
      wl_err "Refusing to use lock file $lock: it must be a regular file owned by $(id -un) with mode 600."
      exit 1
    fi
    if ! flock -w "${WL_LOCK_TIMEOUT:-600}" 9; then
      wl_err "Another Wish List maintenance task is running (lock $lock)."
      exit 1
    fi
    export WL_LOCK_HELD=1
    "$@"
  )
}

# wl_as_app_user CMD...: run CMD as the app user (keeps the environment).
# Use it for every write into the data dir, which that user owns: a root
# write there could be redirected by a symlink the app user planted.
wl_as_app_user() {
  (cd / && exec setpriv --reuid="$WL_USER" --regid="$WL_USER" --init-groups "$@")
}

wl_secure_env_file() {
  chmod 640 "$1" || return 1
  if [[ $EUID -eq 0 ]] && getent group "$WL_USER" >/dev/null; then
    chown "root:$WL_USER" "$1" || return 1
  fi
}

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

# wl_validate KEY VALUE: 0 if VALUE is acceptable for KEY, else 1 with a
# message on stderr.
wl_validate() {
  local key="$1" value="$2"
  if ! wl_is_key "$key"; then
    wl_err "Unknown setting: $key"
    return 1
  fi
  if [[ "$value" == *$'\n'* || "$value" == *$'\r'* ]]; then
    wl_err "$key: line breaks are not allowed."
    return 1
  fi
  case "$key" in
  BASE_URL)
    [[ "$value" =~ ^https?://[^[:space:]/]+(/[^[:space:]]*)?$ ]] ||
      { wl_err "BASE_URL must start with http:// or https://, e.g. https://wish.example.com"; return 1; }
    ;;
  ADMIN_EMAIL)
    [[ "$value" =~ ^[^[:space:]@]+@[^[:space:]@]+$ ]] ||
      { wl_err "ADMIN_EMAIL must be an email address (name@example.com)."; return 1; }
    ;;
  SMTP_FROM)
    [[ -z "$value" || "$value" =~ ^[^@]*[^[:space:]@]+@[^[:space:]@]+[^@]*$ ]] ||
      { wl_err "SMTP_FROM must be an email address (or empty)."; return 1; }
    ;;
  DEFAULT_LANGUAGE)
    [[ "$value" == en || "$value" == pl ]] ||
      { wl_err "DEFAULT_LANGUAGE must be en or pl."; return 1; }
    ;;
  SMTP_PORT)
    [[ -z "$value" || ("$value" =~ ^[0-9]{1,5}$ && $((10#$value)) -ge 1 && $((10#$value)) -le 65535) ]] ||
      { wl_err "SMTP_PORT must be a port number (1-65535)."; return 1; }
    ;;
  SMTP_SECURITY)
    [[ -z "$value" || "$value" =~ ^(starttls|ssl|none)$ ]] ||
      { wl_err "SMTP_SECURITY must be starttls, ssl or none."; return 1; }
    ;;
  WISHLIST_CHANNEL)
    [[ "$value" == main || "$value" == releases ]] ||
      { wl_err "WISHLIST_CHANNEL must be main or releases."; return 1; }
    ;;
  WISHLIST_REPO)
    [[ "$value" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] ||
      { wl_err "WISHLIST_REPO must look like owner/repository."; return 1; }
    ;;
  SECRET_KEY)
    [[ ${#value} -ge 32 && "$value" != *[[:space:]]* ]] ||
      { wl_err "SECRET_KEY must be at least 32 characters without spaces."; return 1; }
    ;;
  DATABASE_URL)
    [[ -n "$value" ]] || { wl_err "DATABASE_URL must not be empty."; return 1; }
    ;;
  DATA_DIR)
    [[ "$value" == /* ]] || { wl_err "DATA_DIR must be an absolute path."; return 1; }
    ;;
  FETCH_DENY_CIDRS)
    # Same split and parse as wishlist/config.py.
    local msg
    if ! msg="$(python3 -I -c '
import ipaddress, re, sys
for raw in re.split(r"[\s,]+", sys.argv[1]):
    if raw:
        try:
            ipaddress.ip_network(raw, strict=False)
        except ValueError:
            print(raw)
            sys.exit(1)
' "$value" 2>&1)"; then
      wl_err "FETCH_DENY_CIDRS must be IP networks separated by commas or spaces (e.g. 203.0.113.7/32), got: ${msg:-$value}"
      return 1
    fi
    ;;
  esac
  return 0
}

# ---------------------------------------------------------------------------
# Prompts: whiptail on a terminal, plain `read` otherwise. WISHLIST_ANSWERS
# names a file of answers (one per line, empty line = default) that replaces
# the terminal, for scripted runs and tests. Prompts never fail: Cancel, Esc,
# end of input and timeouts all keep the default, so an unanswered install
# finishes with defaults instead of aborting.
# ---------------------------------------------------------------------------

WL_BACKTITLE="Wish List"
# Timeouts (seconds, read when asking): WISHLIST_PROMPT_TIMEOUT (default 60)
# for "Configure Wish List now?", which then means No; WISHLIST_FIELD_TIMEOUT
# (default 300) for every other question, which then keeps its default.
wl_prompt_timeout() { printf '%s' "${WISHLIST_PROMPT_TIMEOUT:-60}"; }
wl_field_timeout() { printf '%s' "${WISHLIST_FIELD_TIMEOUT:-300}"; }

# Prints where answers come from; status 1 when nobody can answer.
wl_answer_source() {
  if [[ -n "${WISHLIST_ANSWERS:-}" ]]; then
    printf '%s' "$WISHLIST_ANSWERS"
  elif { : </dev/tty; } 2>/dev/null; then
    printf '%s' /dev/tty
  else
    return 1
  fi
}

wl_can_prompt() { wl_answer_source >/dev/null; }

wl_use_whiptail() {
  [[ -z "${WISHLIST_ANSWERS:-}" && "${TERM:-dumb}" != dumb ]] && command -v whiptail >/dev/null 2>&1 &&
    wl_can_prompt
}

# Keep one descriptor open on the answer source so successive reads advance.
# Call it in the main shell before prompting: the prompt helpers run inside
# $(...), and a descriptor opened there would start from the top every time.
wl_open_answers() {
  local src
  src="$(wl_answer_source)" || return 1
  if [[ -n "${WL_ANSWER_FD:-}" ]]; then
    [[ "$src" == "${WL_ANSWER_SRC:-}" ]] && return 0
    exec {WL_ANSWER_FD}<&-
  fi
  exec {WL_ANSWER_FD}<"$src"
  WL_ANSWER_SRC="$src"
}

# wl_read_line VAR [silent] [timeout]: status 1 on end of input or timeout.
wl_read_line() {
  wl_open_answers || return 1
  local _line
  local -a _opts=(-r -u "$WL_ANSWER_FD")
  [[ -n "${3:-}" ]] && _opts+=(-t "$3")
  [[ "${2:-}" == silent && -z "${WISHLIST_ANSWERS:-}" ]] && _opts+=(-s)
  if ! IFS= read "${_opts[@]}" _line; then
    echo >&2
    return 1
  fi
  [[ "${2:-}" == silent && -z "${WISHLIST_ANSWERS:-}" ]] && echo >&2
  printf -v "$1" '%s' "$_line"
}

# wl_whiptail TIMEOUT ARGS...: whiptail on the terminal; prints its answer.
# Status: whiptail's (0 ok, 1 cancel/no, 255 Esc) or 124 on timeout.
wl_whiptail() {
  local t="$1" rc=0
  shift
  timeout --foreground "$t" whiptail --backtitle "$WL_BACKTITLE" --title "Wish List" "$@" \
    2>&1 >/dev/tty </dev/tty || rc=$?
  if [[ $rc == 124 ]]; then
    # whiptail was killed mid-screen: give the terminal back in a sane state.
    stty sane </dev/tty 2>/dev/null || true
    printf '\033[0m\033[H\033[2J' >/dev/tty 2>/dev/null || true
  fi
  return "$rc"
}

# wl_ask_yesno QUESTION [default y|n] [timeout]: status 0 for yes. Cancel,
# Esc, end of input and timeout give the default.
wl_ask_yesno() {
  local q="$1" def="${2:-n}" t="${3:-$(wl_field_timeout)}" ans rc=0
  if wl_use_whiptail; then
    local -a dflt=()
    [[ "$def" == n ]] && dflt=(--defaultno)
    wl_whiptail "$t" "${dflt[@]}" --yesno "$q" 14 74 >/dev/null || rc=$?
    case "$rc" in
    0) return 0 ;;
    1) return 1 ;;
    *) [[ "$def" == y ]]; return ;;
    esac
  fi
  while :; do
    printf '%s [%s] ' "$q" "$([[ "$def" == y ]] && echo Y/n || echo y/N)" >&2
    if ! wl_read_line ans "" "$t"; then
      [[ "$def" == y ]]
      return
    fi
    case "${ans,,}" in
    "") [[ "$def" == y ]]; return ;;
    y | yes) return 0 ;;
    n | no) return 1 ;;
    *) wl_err "Please answer y or n." ;;
    esac
  done
}

# wl_ask_input PROMPT DEFAULT [secret]: prints the answer. "-" clears the
# value. Secret fields never show the current value (empty keeps it).
# Status 1 (still printing DEFAULT) when the default was kept because of
# Cancel, Esc, end of input or timeout.
wl_ask_input() {
  local prompt="$1" def="$2" secret="${3:-}" ans rc=0
  if wl_use_whiptail; then
    if [[ "$secret" == secret ]]; then
      ans="$(wl_whiptail "$(wl_field_timeout)" --passwordbox \
        "$prompt"$'\n\n'"Leave empty to keep the current value; enter - to clear it." 14 74 "")" || rc=$?
      [[ $rc == 0 && -z "$ans" ]] && ans="$def"
    else
      ans="$(wl_whiptail "$(wl_field_timeout)" --inputbox "$prompt"$'\n\n'"Enter - to clear the value." 14 74 "$def")" || rc=$?
    fi
    if [[ $rc != 0 ]]; then
      printf '%s' "$def"
      return 1
    fi
  else
    local hint="$def"
    [[ "$secret" == secret && -n "$def" ]] && hint="keep current"
    [[ -n "$def" ]] && hint+="; - clears"
    printf '%s [%s]: ' "$prompt" "$hint" >&2
    local mode=""
    [[ "$secret" == secret ]] && mode=silent
    if ! wl_read_line ans "$mode" "$(wl_field_timeout)"; then
      printf '%s' "$def"
      return 1
    fi
    [[ -z "$ans" ]] && ans="$def"
  fi
  [[ "$ans" == - ]] && ans=""
  printf '%s' "$ans"
}

# wl_ask_setting KEY PROMPT DEFAULT [secret]: asks until wl_validate accepts;
# prints the value. Cancel/timeout/end of input keep DEFAULT.
wl_ask_setting() {
  local key="$1" prompt="$2" def="$3" secret="${4:-}" v msg
  while :; do
    if ! v="$(wl_ask_input "$prompt" "$def" "$secret")"; then
      printf '%s' "$def"
      return 0
    fi
    if msg="$(wl_validate "$key" "$v" 2>&1)"; then
      printf '%s' "$v"
      return 0
    fi
    if wl_use_whiptail; then
      wl_whiptail "$(wl_field_timeout)" --msgbox "$msg" 10 70 >/dev/null || true
    else
      wl_err "$msg"
    fi
  done
}

# Container address for the default BASE_URL: first IPv4, else first IPv6
# (bracketed), else 127.0.0.1.
wl_container_ip() {
  local -a addrs=()
  local a
  read -r -a addrs <<<"$(hostname -I 2>/dev/null)" || true
  if [[ ${#addrs[@]} -eq 0 ]]; then
    read -r -a addrs <<<"$(ip -o addr show scope global 2>/dev/null | awk '{split($4, a, "/"); printf "%s ", a[1]}')" || true
  fi
  for a in "${addrs[@]}"; do
    [[ "$a" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] && { printf '%s' "$a"; return 0; }
  done
  for a in "${addrs[@]}"; do
    [[ "$a" == *:* ]] && { printf '[%s]' "$a"; return 0; }
  done
  printf '127.0.0.1'
}

wl_is_unattended() {
  [[ "${WISHLIST_UNATTENDED:-0}" == 1 || "${PHS_SILENT:-0}" == 1 ]] && return 0
  [[ "${var_unattended:-}" =~ ^(yes|true|1)$ || "${UNATTENDED:-}" =~ ^(yes|true|1)$ ]] && return 0
  # The engine's own "no questions" modes (MODE is exported into the
  # container). Its METHOD values carry suffixes ("mydefaults-global",
  # ui/defaults.func), so the names match as prefixes.
  [[ "${MODE:-}" =~ ^(mydefaults|userdefaults|appdefaults) || "${MODE:-}" =~ ^(3|4)$ ]] && return 0
  ! wl_can_prompt
}

# wl_collect_settings: write the complete settings file, asking
# "Configure Wish List now?" first unless unattended. Defaults are the
# current settings when the file exists (re-run), else the documented ones.
# The existing SECRET_KEY is kept; a new one is generated otherwise.
wl_collect_settings() {
  local -A v=(
    [BASE_URL]="http://$(wl_container_ip):$WL_PORT" [SECRET_KEY]=""
    [DATABASE_URL]="sqlite:///$WL_DATA_DIR/wishlist.db" [DATA_DIR]="$WL_DATA_DIR"
    [ADMIN_NAME]=Admin [ADMIN_EMAIL]=admin@example.com [DEFAULT_LANGUAGE]=en
    [SMTP_HOST]="" [SMTP_PORT]=587 [SMTP_SECURITY]=starttls [SMTP_USER]="" [SMTP_PASSWORD]="" [SMTP_FROM]=""
    [GSMSERVICE_TOKEN]="" [GSMSERVICE_SENDER]="" [FETCH_DENY_CIDRS]=""
    [WISHLIST_CHANNEL]="${WISHLIST_CHANNEL:-main}" [WISHLIST_REPO]="${WISHLIST_REPO:-$WL_DEFAULT_REPO}"
  )
  local k cur
  for k in "${WL_KEYS[@]}"; do
    if cur="$(wl_settings_get "$k" 2>/dev/null)"; then v[$k]="$cur"; fi
  done

  if ! wl_is_unattended && wl_open_answers && wl_ask_yesno "Configure Wish List now?

No: use defaults (address ${v[BASE_URL]}, admin ${v[ADMIN_EMAIL]}, language ${v[DEFAULT_LANGUAGE]}, no email or SMS). Everything can be changed later with: wishlist-settings

(No answer within $(wl_prompt_timeout)s means No.)" n "$(wl_prompt_timeout)"; then
    v[BASE_URL]="$(wl_ask_setting BASE_URL "Address people use to open the app (BASE_URL)" "${v[BASE_URL]}")"
    v[ADMIN_NAME]="$(wl_ask_input "First admin's name" "${v[ADMIN_NAME]}")" || true
    v[ADMIN_EMAIL]="$(wl_ask_setting ADMIN_EMAIL "First admin's email" "${v[ADMIN_EMAIL]}")"
    v[DEFAULT_LANGUAGE]="$(wl_ask_setting DEFAULT_LANGUAGE "Default language (en or pl)" "${v[DEFAULT_LANGUAGE]}")"
    if wl_ask_yesno "Send email (links, notifications) through an SMTP server?" "$([[ -n "${v[SMTP_HOST]}" ]] && echo y || echo n)"; then
      v[SMTP_HOST]="$(wl_ask_input "SMTP host" "${v[SMTP_HOST]}")" || true
      v[SMTP_PORT]="$(wl_ask_setting SMTP_PORT "SMTP port" "${v[SMTP_PORT]}")"
      v[SMTP_SECURITY]="$(wl_ask_setting SMTP_SECURITY "SMTP security (starttls, ssl or none)" "${v[SMTP_SECURITY]}")"
      v[SMTP_USER]="$(wl_ask_input "SMTP user (empty for none)" "${v[SMTP_USER]}")" || true
      v[SMTP_PASSWORD]="$(wl_ask_input "SMTP password" "${v[SMTP_PASSWORD]}" secret)" || true
      v[SMTP_FROM]="$(wl_ask_setting SMTP_FROM "Sender address (empty = SMTP user)" "${v[SMTP_FROM]}")"
    fi
    if wl_ask_yesno "Send SMS through gsmservice.pl?" "$([[ -n "${v[GSMSERVICE_TOKEN]}" ]] && echo y || echo n)"; then
      v[GSMSERVICE_TOKEN]="$(wl_ask_input "gsmservice.pl API token" "${v[GSMSERVICE_TOKEN]}" secret)" || true
      v[GSMSERVICE_SENDER]="$(wl_ask_input "SMS sender name" "${v[GSMSERVICE_SENDER]}")" || true
    fi
    v[FETCH_DENY_CIDRS]="$(wl_ask_setting FETCH_DENY_CIDRS "Extra networks the picture fetcher must never reach, e.g. your public IP as 203.0.113.7/32 (empty for none)" "${v[FETCH_DENY_CIDRS]}")"
  fi

  [[ -n "${v[SECRET_KEY]}" ]] || v[SECRET_KEY]="$(openssl rand -hex 32)" || return 1
  local tmp
  tmp="$(mktemp "$(dirname "$WISHLIST_ENV")/.wishlist.env.new.XXXXXX")" || return 1
  for k in "${WL_KEYS[@]}"; do
    WISHLIST_ENV="$tmp" wl_settings_set "$k" "${v[$k]}" || { rm -f "$tmp"; return 1; }
  done
  wl_with_lock mv -f "$tmp" "$WISHLIST_ENV"
}

# ---------------------------------------------------------------------------
# Running the app
# ---------------------------------------------------------------------------

# wl_run_as_app CMD...: run CMD as the app user with exactly the settings-file
# environment (like the systemd unit), from the data dir. Values are exported
# by the shell, never passed on a command line (other users can read argv).
wl_run_as_app() {
  local -a keys=() vals=()
  local k v
  for k in "${WL_KEYS[@]}"; do
    if v="$(wl_settings_get "$k")"; then
      keys+=("$k")
      vals+=("$v")
    fi
  done
  (
    cd "$WL_DATA_DIR" || exit 1
    while IFS= read -r k; do unset "$k" 2>/dev/null || true; done < <(compgen -e)
    export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin LANG=C.UTF-8 \
      HOME="$WL_DATA_DIR" USER="$WL_USER" LOGNAME="$WL_USER"
    for k in "${!keys[@]}"; do export "${keys[k]}=${vals[k]}"; done
    exec setpriv --reuid="$WL_USER" --regid="$WL_USER" --init-groups "$@"
  )
}

# wl_shielded CMD...: run CMD (command or function) through the engine's
# SIGHUP shield when it is loaded, so a closed console doesn't kill it.
wl_shielded() {
  if declare -F _cs_shield_hangup >/dev/null; then
    _cs_shield_hangup "$@"
  else
    "$@"
  fi
}

# wl_health [timeout_s]: wait until /healthz answers "ok".
wl_health() {
  local timeout="${1:-${WISHLIST_HEALTH_TIMEOUT:-30}}" end
  end=$((SECONDS + timeout))
  while :; do
    [[ "$(curl -fsS --max-time 3 "http://127.0.0.1:$WL_PORT/healthz" 2>/dev/null)" == ok ]] && return 0
    ((SECONDS >= end)) && break
    sleep 1
  done
  wl_err "Wish List did not answer on http://127.0.0.1:$WL_PORT/healthz within ${timeout}s."
  return 1
}

# wl_service start|restart: start the app after clearing systemd's start
# rate limit (StartLimitBurst=5 per 120 s). Manual starts count towards it,
# so a few quick settings changes would otherwise make systemd refuse the
# next start (start-limit-hit), and the restore would be refused as well.
wl_service() {
  systemctl reset-failed "$WL_SERVICE" 2>/dev/null || true
  systemctl "$1" "$WL_SERVICE"
}

# wl_apply_settings NEW_FILE: install NEW_FILE as the settings file, restart
# and health-check; on failure put the previous file back, restart, print
# the service log and return 1. Holds the lock throughout.
wl_apply_settings() {
  wl_with_lock _wl_apply_settings "$@"
}

_wl_apply_settings() {
  local new="$1" old log
  old="$(mktemp "$(dirname "$WISHLIST_ENV")/.wishlist.env.prev.XXXXXX")" || return 1
  cp -p "$WISHLIST_ENV" "$old" || return 1
  wl_secure_env_file "$new" && mv -f "$new" "$WISHLIST_ENV" || { rm -f "$old"; return 1; }
  if wl_service restart && wl_health; then
    rm -f "$old"
    return 0
  fi
  log="$(journalctl -u "$WL_SERVICE" -n 20 --no-pager 2>&1)"
  mv -f "$old" "$WISHLIST_ENV"
  wl_service restart || true
  wl_err "Wish List failed to start with the new settings; the previous settings were restored."
  wl_err "Last log lines:"
  wl_err "$log"
  wl_health >/dev/null 2>&1 || wl_err "Warning: Wish List is not healthy with the restored settings either."
  return 1
}

# ---------------------------------------------------------------------------
# Code install
# ---------------------------------------------------------------------------

wl_repo() {
  local r="${WISHLIST_REPO:-}"
  [[ -n "$r" ]] || r="$(wl_settings_get WISHLIST_REPO 2>/dev/null)" || r=""
  printf '%s' "${r:-$WL_DEFAULT_REPO}"
}

# wl_install_ref REF TARGET_DIR: wl_build_ref, then swap TARGET_DIR.new in;
# the previous TARGET_DIR is kept as TARGET_DIR.prev. Prints nothing on
# success; on failure prints the build log tail and leaves TARGET_DIR alone.
# Run the app from the moved venv with `.venv/bin/python -m ...`: console
# scripts in .venv/bin keep the .new path in their shebang.
wl_install_ref() {
  wl_build_ref "$@" || return 1
  local target="${2%/}"
  wl_with_lock _wl_swap_dirs "$target.new" "$target"
}

# wl_build_ref REF TARGET_DIR: fetch REF (commit sha or tag) from GitHub, or
# copy $WISHLIST_SOURCE_DIR (tests; REF is then only recorded), and build a
# fresh venv with the app and compiled translations in TARGET_DIR.new. The
# live TARGET_DIR is not touched, so this can run while the app is up.
wl_build_ref() {
  local ref="$1" target="${2%/}" stage log repo rc=0
  if [[ ! "$ref" =~ ^[A-Za-z0-9][A-Za-z0-9._/-]*$ || "$ref" == *..* ]]; then
    wl_err "Invalid ref: $ref"
    return 1
  fi
  [[ -n "$target" && "$target" == /* ]] || { wl_err "Target must be an absolute path."; return 1; }
  stage="$target.new"
  rm -rf "$stage" && mkdir -p "$stage" || return 1
  log="$(mktemp)" || return 1
  # pipefail: a download cut short must fail even if tar is happy with it.
  (
    set -o pipefail
    if [[ -n "${WISHLIST_SOURCE_DIR:-}" ]]; then
      tar -C "$WISHLIST_SOURCE_DIR" --exclude=./.git --exclude=./.venv --exclude='*.egg-info' \
        --exclude=__pycache__ --exclude=./.superpowers --exclude='*.db' --exclude='*.mo' -cf - . |
        tar -C "$stage" -xf -
    else
      repo="$(wl_repo)"
      curl -fsSL --retry 3 "https://github.com/$repo/archive/$ref.tar.gz" |
        tar -xz --strip-components=1 -C "$stage"
    fi
  ) >>"$log" 2>&1 || rc=1
  if [[ $rc == 0 && ! -f "$stage/pyproject.toml" ]]; then
    echo "No pyproject.toml in the downloaded source." >>"$log"
    rc=1
  fi
  if [[ $rc == 0 ]]; then
    # Chained with &&: `set -e` would be ignored here (this runs on the left
    # of ||), so a failed step would not stop the build.
    (
      export PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_INPUT=1 PIP_NO_CACHE_DIR=1
      python3 -m venv "$stage/.venv" &&
        "$stage/.venv/bin/python" -m pip install -q babel &&
        "$stage/.venv/bin/python" -m babel.messages.frontend compile -d "$stage/wishlist/translations" &&
        "$stage/.venv/bin/python" -m pip install -q "$stage" &&
        "$stage/.venv/bin/python" -c "import wishlist"
    ) >>"$log" 2>&1 || rc=1
  fi
  if [[ $rc != 0 ]]; then
    wl_err "Installing Wish List $ref failed:"
    tail -n 30 "$log" >&2
    rm -rf "$stage" "$log"
    return 1
  fi
  rm -f "$log"
  if ! { chown -R root:root "$stage" && chmod 755 "$stage"; }; then
    rm -rf "$stage"
    return 1
  fi
}

# Move TARGET to TARGET.prev and STAGE to TARGET; on failure put TARGET back.
_wl_swap_dirs() {
  local stage="$1" target="$2" had=0
  if [[ -e "$target" ]]; then
    had=1
    if ! { rm -rf "$target.prev" && mv -T "$target" "$target.prev"; }; then
      wl_err "Could not move $target aside."
      rm -rf "$stage"
      return 1
    fi
  fi
  if ! mv -T "$stage" "$target"; then
    wl_err "Could not move the new code into $target."
    if [[ $had == 1 && ! -e "$target" ]]; then mv -T "$target.prev" "$target" || true; fi
    rm -rf "$stage"
    return 1
  fi
}

# _wl_gh_get URL: GET a GitHub API URL and print the body. Status: 0 for
# 200, 4 for 404, 3 when the API rate limit is hit (403/429 with
# x-ratelimit-remaining: 0 or retry-after), 1 for anything else.
_wl_gh_get() {
  local url="$1" body hdr code rc=1
  body="$(mktemp)" || return 1
  hdr="$(mktemp)" || {
    rm -f "$body"
    return 1
  }
  code="$(curl -sSL --max-time 20 -H 'Accept: application/vnd.github+json' \
    -D "$hdr" -o "$body" -w '%{http_code}' "$url" 2>/dev/null)" || code=000
  case "$code" in
  200) cat "$body" && rc=0 ;;
  404) rc=4 ;;
  403 | 429)
    if grep -qiE '^(x-ratelimit-remaining:[[:space:]]*0[[:space:]]*$|retry-after:)' "$hdr"; then rc=3; fi
    ;;
  esac
  rm -f "$body" "$hdr"
  return "$rc"
}

# Message for a failed _wl_gh_get (status $1) about WHAT ($2).
_wl_gh_fail() {
  if [[ "$1" == 3 ]]; then
    wl_err "GitHub API rate limit reached, try again later."
  else
    wl_err "Could not read $2 from GitHub."
  fi
}

# wl_latest_ref CHANNEL: newest commit sha on main (main), or newest release
# tag (releases); a repo without releases (404) falls back to the newest tag.
# Any other error (rate limit, server error, no network) fails: falling back
# then could pick an older tag than the latest release.
wl_latest_ref() {
  local channel="$1" api="${WISHLIST_GITHUB_API:-https://api.github.com}" repo json ref rc=0
  repo="$(wl_repo)"
  case "$channel" in
  main)
    json="$(_wl_gh_get "$api/repos/$repo/commits/main")" || {
      _wl_gh_fail $? "the latest commit of $repo"
      return 1
    }
    ref="$(python3 -I -c 'import json, sys; print(json.load(sys.stdin)["sha"])' <<<"$json" 2>/dev/null)"
    ;;
  releases)
    json="$(_wl_gh_get "$api/repos/$repo/releases/latest")" || rc=$?
    if [[ $rc == 0 ]]; then
      ref="$(python3 -I -c 'import json, sys; print(json.load(sys.stdin)["tag_name"])' <<<"$json" 2>/dev/null)"
    elif [[ $rc == 4 ]]; then
      json="$(_wl_gh_get "$api/repos/$repo/tags")" || {
        _wl_gh_fail $? "the tags of $repo"
        return 1
      }
      ref="$(python3 -I -c 'import json, sys; print(json.load(sys.stdin)[0]["name"])' <<<"$json" 2>/dev/null)"
    else
      _wl_gh_fail "$rc" "the latest release of $repo"
      return 1
    fi
    ;;
  *)
    wl_err "Unknown update channel: $channel (use main or releases)"
    return 1
    ;;
  esac
  [[ -n "${ref:-}" ]] || { wl_err "GitHub answered without a usable version for $repo ($channel)."; return 1; }
  printf '%s\n' "$ref"
}

# ---------------------------------------------------------------------------
# Backups: database (sqlite .backup), images/, settings, version.
# ---------------------------------------------------------------------------

wl_db_path() {
  local url
  url="$(wl_settings_get DATABASE_URL 2>/dev/null)" || url=""
  if [[ "$url" == sqlite:////* ]]; then
    printf '%s' "/${url#sqlite:////}"
  else
    printf '%s' "$WL_DATA_DIR/wishlist.db"
  fi
}

# wl_backup: print the new backup directory (root-only). It also keeps the
# systemd unit (wishlist.service), which an update may replace. The database copy
# is made and read by the app user (sqlite creates WAL files, which must stay
# the app user's; and root must not read a path the app user controls);
# the same goes for the pictures.
wl_backup() { wl_with_lock _wl_backup; }

_wl_backup() {
  local dir db tmp
  db="$(wl_db_path)"
  mkdir -p "$WL_BACKUP_ROOT" && chmod 700 "$WL_BACKUP_ROOT" || return 1
  dir="$(mktemp -d "$WL_BACKUP_ROOT/$(date -u +%Y%m%dT%H%M%SZ).XXXX")" || return 1
  if [[ -f "$db" ]]; then
    tmp="$(dirname "$db")/.wishlist-backup.db"
    # shellcheck disable=SC2016
    if ! wl_as_app_user bash -c 'rm -f "$2" && sqlite3 "$1" ".backup \"$2\""' _ "$db" "$tmp" ||
      ! wl_as_app_user cat "$tmp" >"$dir/wishlist.db"; then
      wl_err "Database backup failed."
      wl_as_app_user rm -f "$tmp"
      rm -rf "$dir"
      return 1
    fi
    wl_as_app_user rm -f "$tmp"
  fi
  if [[ -d "$WL_DATA_DIR/images" ]]; then
    # Archived by the app user (it can only read what it may read, links stay
    # links), unpacked by root into the root-only backup dir. tar does not
    # write through links from the archive (placeholders, no ../ names).
    # pipefail: a picture the app user cannot read fails its tar while the
    # unpacking side happily stores a partial copy; that backup is no backup.
    if ! (
      set -o pipefail
      wl_as_app_user tar -C "$WL_DATA_DIR" -cf - images |
        tar -C "$dir" -xf - --no-same-owner --no-same-permissions --no-overwrite-dir
    ); then
      wl_err "Backing up the pictures failed."
      rm -rf "$dir"
      return 1
    fi
  else
    mkdir "$dir/images"
  fi
  if ! { [[ ! -f "$WISHLIST_ENV" ]] || cp -p "$WISHLIST_ENV" "$dir/wishlist.env"; } ||
    ! { [[ ! -f "$WL_VERSION_FILE" ]] || cp -p "$WL_VERSION_FILE" "$dir/wishlist_version.txt"; } ||
    ! { [[ ! -f "$WL_UNIT_FILE" ]] || cp -p "$WL_UNIT_FILE" "$dir/wishlist.service"; }; then
    wl_err "Backing up the settings failed."
    rm -rf "$dir"
    return 1
  fi
  printf '%s\n' "$dir"
}

# wl_restore DIR: put a backup's database, images, settings and version back.
# Stop the service first. Everything inside the data dir is written by the
# app user (root only reads the backup and pipes it over).
wl_restore() { wl_with_lock _wl_restore "$@"; }

_wl_restore() {
  local dir="${1%/}" db
  [[ -d "$dir" ]] || { wl_err "No such backup: $dir"; return 1; }
  if [[ -f "$dir/wishlist.env" ]]; then
    cp -p "$dir/wishlist.env" "$WISHLIST_ENV" && wl_secure_env_file "$WISHLIST_ENV" || return 1
  fi
  if [[ -f "$dir/wishlist_version.txt" ]]; then cp -p "$dir/wishlist_version.txt" "$WL_VERSION_FILE" || return 1; fi
  db="$(wl_db_path)"
  if [[ -f "$dir/wishlist.db" ]]; then
    # shellcheck disable=SC2016
    wl_as_app_user bash -c 'set -e; umask 027; rm -f "$1-wal" "$1-shm" "$1-journal" "$1.restore"
      cat >"$1.restore"; mv -f "$1.restore" "$1"' _ "$db" <"$dir/wishlist.db" ||
      { wl_err "Restoring the database failed."; return 1; }
  fi
  if [[ -d "$dir/images" ]]; then
    # Unpack fully first (both ends of the pipe must succeed), then swap.
    # shellcheck disable=SC2016
    if ! (
      set -o pipefail
      tar -C "$dir" -cf - images | wl_as_app_user bash -c 'set -e; cd "$1"
        rm -rf .images.restore .images.old; mkdir .images.restore
        tar -C .images.restore --strip-components=1 -xf -' _ "$WL_DATA_DIR"
    ) || ! wl_as_app_user bash -c 'set -e; cd "$1"
        if [ -e images ] || [ -L images ]; then mv -T images .images.old; fi
        mv -T .images.restore images; rm -rf .images.old' _ "$WL_DATA_DIR"; then
      wl_as_app_user rm -rf "$WL_DATA_DIR/.images.restore"
      wl_err "Restoring the pictures failed."
      return 1
    fi
  fi
}

# wl_prune_backups: keep the newest three backups.
wl_prune_backups() {
  [[ -d "$WL_BACKUP_ROOT" ]] || return 0
  local -a all
  mapfile -t all < <(find "$WL_BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort)
  local n=$((${#all[@]} - WL_KEEP_BACKUPS)) i
  for ((i = 0; i < n; i++)); do
    rm -rf "${WL_BACKUP_ROOT:?}/${all[i]}"
  done
}

# ---------------------------------------------------------------------------
# Update (`update` in the container runs ct/wishlist.sh, whose update_script
# calls wl_update)
# ---------------------------------------------------------------------------

# wl_update: update to the newest version on the configured channel. Status 0
# when already up to date or updated. Non-zero when the check, the disk-space
# check or the build failed (nothing was changed), or when the new version
# failed or the update was interrupted and the previous one was put back
# (code, unit, database, pictures, settings, version). Holds the maintenance
# lock throughout and is shielded from console hangups.
#
# Order: check free space -> drop the previous update's WL_APP_DIR.prev ->
# build the new version next to the live one while the app runs -> stop ->
# backup (a consistent copy of the stopped database) -> swap the code ->
# record the version -> install the new unit -> migrate (bootstrap) -> start
# -> health check within WISHLIST_HEALTH_TIMEOUT (30 s) -> on any failure,
# or SIGINT/SIGTERM after the stop, roll back.
wl_update() { wl_shielded wl_with_lock _wl_update; }

# Update state, global so the trap handler (_wl_interrupted) can read it.
_WL_U_PHASE="" _WL_U_BACKUP="" _WL_U_PREVIOUS=""

_wl_update() {
  local channel current latest backup err=""
  if [[ ! -f "$WL_VERSION_FILE" || ! -d "$WL_APP_DIR" ]]; then
    wl_err "No Wish List installation found ($WL_VERSION_FILE, $WL_APP_DIR)."
    return 1
  fi
  current="$(head -n 1 "$WL_VERSION_FILE")"
  channel="$(wl_settings_get WISHLIST_CHANNEL 2>/dev/null)" || channel=""
  channel="${channel:-main}"
  if ! latest="$(wl_latest_ref "$channel")"; then
    wl_err "Could not check for a new version on GitHub ($(wl_repo), channel $channel); nothing was changed."
    return 1
  fi
  if [[ "$latest" == "$current" ]]; then
    echo "Wish List is already up to date ($current)."
    return 0
  fi

  echo "Updating Wish List from $current to $latest (channel $channel)."
  _wl_check_space || return 1
  # The previous update's rollback target is not needed any more, and its
  # space is needed for the build.
  rm -rf "$WL_APP_DIR.prev"
  echo "Building the new version; the app keeps running meanwhile..."
  if ! wl_build_ref "$latest" "$WL_APP_DIR"; then
    wl_err "Update aborted; nothing was changed."
    return 1
  fi

  # From here on an interruption must not leave the app stopped or half
  # updated: the trap puts everything back (once) and exits non-zero.
  _WL_U_PHASE=stopped _WL_U_BACKUP="" _WL_U_PREVIOUS="$current"
  trap '_wl_interrupted 130' INT
  trap '_wl_interrupted 143' TERM
  echo "Stopping Wish List and backing up the database, pictures and settings..."
  if ! systemctl stop "$WL_SERVICE" || ! backup="$(_wl_backup)"; then
    _wl_untrap
    rm -rf "$WL_APP_DIR.new"
    _wl_start_unchanged
    wl_err "Backup failed; update aborted, nothing was changed."
    return 1
  fi
  _WL_U_BACKUP="$backup"
  wl_prune_backups
  _WL_U_PHASE=swapping
  if ! _wl_swap_dirs "$WL_APP_DIR.new" "$WL_APP_DIR"; then
    _wl_untrap
    _wl_start_unchanged
    wl_err "Update aborted; nothing was changed."
    return 1
  fi
  _WL_U_PHASE=swapped
  if ! printf '%s\n' "$latest" >"$WL_VERSION_FILE"; then
    _wl_untrap
    wl_err "Could not write $WL_VERSION_FILE."
    _wl_rollback "$backup" "$current" 1
    return 1
  fi
  if ! _wl_install_unit; then
    _wl_untrap
    _wl_rollback "$backup" "$current" 1
    return 1
  fi

  echo "Migrating the database and starting the new version..."
  # Only stderr is kept: stdout could carry a one-time admin link.
  if err="$(wl_run_as_app "$WL_APP_DIR/.venv/bin/python" -m wishlist.bootstrap 2>&1 >/dev/null)" &&
    wl_service start && wl_health; then
    _wl_untrap
    _wl_refresh_helpers
    # The engine pin only moves forward with main: a release's lib may carry
    # an older pin than the one /usr/bin/update already has.
    if [[ "$channel" == main ]]; then
      _wl_refresh_entrypoint
    fi
    echo "Updated Wish List to $latest. Backup of the previous version: $backup"
    return 0
  fi
  _wl_untrap
  [[ -n "$err" ]] && wl_err "$err"
  wl_err "The new version did not start. Last log lines:"
  wl_err "$(journalctl -u "$WL_SERVICE" -n 20 --no-pager 2>&1)"
  _wl_rollback "$backup" "$current" 1
}

_wl_untrap() {
  trap - INT TERM
  _WL_U_PHASE=""
}

# Trap handler (SIGINT/SIGTERM after the stop): undo what was done so far,
# once, then exit with 128+signal.
_wl_interrupted() {
  local code="$1" phase="$_WL_U_PHASE"
  trap '' INT TERM
  _WL_U_PHASE=""
  [[ -n "$phase" ]] || exit "$code"
  wl_err "Update interrupted; putting the previous version back..."
  if [[ -n "$_WL_U_BACKUP" ]]; then
    # "swapping": the swap either happened (the new code is live, the old one
    # in .prev) or _wl_swap_dirs put the old dir back; .new tells them apart.
    if [[ "$phase" == swapped || ("$phase" == swapping && ! -e "$WL_APP_DIR.new") ]]; then
      _wl_rollback "$_WL_U_BACKUP" "$_WL_U_PREVIOUS" 1
    else
      _wl_rollback "$_WL_U_BACKUP" "$_WL_U_PREVIOUS" 0
    fi
  else
    rm -rf "$WL_APP_DIR.new"
    _wl_start_unchanged
    wl_err "Update interrupted before anything was changed."
  fi
  exit "$code"
}

# _wl_check_space: the update needs room for the new code (built next to the
# old) and a backup of the database and pictures: 2x the code + database +
# pictures + 200 MB, on the code's filesystem (the previous update's .prev,
# which is removed first, counts as free). A separate backup filesystem must
# hold the database + pictures + 100 MB.
_wl_check_space() {
  local code prev data need avail fs bk_fs bk_avail bk_dir
  code="$(_wl_kb "$WL_APP_DIR")"
  prev="$(_wl_kb "$WL_APP_DIR.prev")"
  data=$(($(_wl_kb "$(wl_db_path)") + $(_wl_kb "$WL_DATA_DIR/images")))
  need=$((2 * code + data + 200 * 1024))
  read -r fs avail < <(df -Pk "$(dirname "$WL_APP_DIR")" 2>/dev/null | awk 'NR == 2 {print $1, $4}')
  if [[ ! "${avail:-}" =~ ^[0-9]+$ ]]; then
    wl_err "Could not determine the free disk space; update aborted, nothing was changed."
    return 1
  fi
  if ((avail + prev < need)); then
    wl_err "Not enough free disk space for the update: need $((need / 1024)) MB, $(((avail + prev) / 1024)) MB free. Update aborted, nothing was changed. Grow the container disk (e.g. pct resize <ctid> rootfs +2G) and try again."
    return 1
  fi
  bk_dir="$WL_BACKUP_ROOT"
  [[ -d "$bk_dir" ]] || bk_dir="$(dirname "$WL_BACKUP_ROOT")"
  read -r bk_fs bk_avail < <(df -Pk "$bk_dir" 2>/dev/null | awk 'NR == 2 {print $1, $4}')
  if [[ -n "${bk_fs:-}" && "$bk_fs" != "$fs" && "${bk_avail:-0}" =~ ^[0-9]+$ ]] &&
    ((bk_avail < data + 100 * 1024)); then
    wl_err "Not enough free disk space for the backup in $WL_BACKUP_ROOT: need $(((data + 100 * 1024) / 1024)) MB, $((bk_avail / 1024)) MB free. Update aborted, nothing was changed."
    return 1
  fi
}

# Size of a file or directory in KiB (0 if missing).
_wl_kb() {
  local kb
  kb="$(du -sk "$1" 2>/dev/null | awk '{print $1}')"
  printf '%s' "${kb:-0}"
}

# Install the unit shipped with the (now live) new version, if it has one,
# and reload systemd. The old unit is in the backup (wishlist.service).
_wl_install_unit() {
  local src="$WL_APP_DIR/proxmox/misc/wishlist.service" tmp
  if [[ -f "$src" ]] && ! cmp -s "$src" "$WL_UNIT_FILE"; then
    tmp="$(mktemp "$(dirname "$WL_UNIT_FILE")/.wishlist.service.XXXXXX")" &&
      cat "$src" >"$tmp" && chmod 644 "$tmp" && mv -f "$tmp" "$WL_UNIT_FILE" || {
      rm -f "${tmp:-}"
      wl_err "Could not install the new $WL_UNIT_FILE."
      return 1
    }
  fi
  systemctl daemon-reload || { wl_err "systemctl daemon-reload failed."; return 1; }
}

# Start the untouched installation again after an aborted update.
_wl_start_unchanged() {
  if ! { wl_service start && wl_health >/dev/null 2>&1; }; then
    wl_err "Warning: Wish List did not come back up; check: journalctl -u $WL_SERVICE"
  fi
}

# _wl_rollback BACKUP_DIR PREVIOUS_VERSION SWAPPED: if SWAPPED is 1, put the
# previous code dir (WL_APP_DIR.prev) back, else just drop WL_APP_DIR.new;
# then restore the backup (database, pictures, settings, version, unit),
# start, health-check. Always returns 1 (the update failed either way); says
# whether the rollback worked. SIGINT/SIGTERM are ignored while it runs (an
# interrupted rollback would leave a half-restored, stopped app); the
# previous dispositions are put back afterwards.
_wl_rollback() {
  local saved_traps
  saved_traps="$(trap -p INT TERM)"
  trap '' INT TERM
  _wl_do_rollback "$@"
  trap - INT TERM
  eval "$saved_traps"
  return 1
}

_wl_do_rollback() {
  local backup="$1" previous="$2" swapped="${3:-1}" ok=1
  wl_err "Rolling back to $previous..."
  systemctl stop "$WL_SERVICE" || true
  if [[ "$swapped" == 1 ]]; then
    if [[ -d "$WL_APP_DIR.prev" ]] && rm -rf "$WL_APP_DIR.failed" && mv -T "$WL_APP_DIR" "$WL_APP_DIR.failed"; then
      if mv -T "$WL_APP_DIR.prev" "$WL_APP_DIR"; then
        rm -rf "$WL_APP_DIR.failed"
      else
        mv -T "$WL_APP_DIR.failed" "$WL_APP_DIR" || true
        ok=0
      fi
    else
      ok=0
    fi
  else
    # Not swapped, or interrupted inside the swap: if the live dir was moved
    # aside but the new one not moved in, put the old one back.
    if [[ ! -e "$WL_APP_DIR" && -d "$WL_APP_DIR.prev" ]]; then
      mv -T "$WL_APP_DIR.prev" "$WL_APP_DIR" || ok=0
    fi
    rm -rf "$WL_APP_DIR.new"
  fi
  _wl_restore "$backup" || ok=0
  if [[ -f "$backup/wishlist.service" ]] && ! cmp -s "$backup/wishlist.service" "$WL_UNIT_FILE"; then
    cp "$backup/wishlist.service" "$WL_UNIT_FILE" && chmod 644 "$WL_UNIT_FILE" || ok=0
  fi
  systemctl daemon-reload || ok=0
  if ! { wl_service start && wl_health; }; then ok=0; fi
  if [[ $ok == 1 ]]; then
    wl_err "Update failed; rolled back to $previous (code, database, pictures and settings). Backup kept in $backup."
  else
    wl_err "Update failed and the rollback did not complete. Backup of $previous: $backup. Check: journalctl -u $WL_SERVICE"
  fi
  return 1
}

# After a good update: rewrite /usr/bin/update with the NEW lib (just
# refreshed), so a moved engine pin (WL_CORE_URL_DEFAULT) reaches installed
# containers. The scripts and website bases already in the entrypoint stay.
_wl_refresh_entrypoint() {
  local base="" website=""
  [[ -f "$WL_LIB_FILE" ]] || return 0
  if [[ -f "$WL_UPDATE_ENTRYPOINT" ]]; then
    base="$(sed -n 's/^export COMMUNITY_SCRIPTS_URL="\(.*\)"$/\1/p' "$WL_UPDATE_ENTRYPOINT" | head -n 1)"
    website="$(sed -n 's/^export COMMUNITY_SCRIPTS_WEBSITE_URL="\(.*\)"$/\1/p' "$WL_UPDATE_ENTRYPOINT" | head -n 1)"
  fi
  # shellcheck disable=SC2016
  env -u COMMUNITY_SCRIPTS_CORE_URL \
    COMMUNITY_SCRIPTS_URL="${base:-$WL_SCRIPTS_URL_DEFAULT}" \
    COMMUNITY_SCRIPTS_WEBSITE_URL="${website:-https://community-scripts.org}" \
    bash -c '. "$1" && wl_write_update_entrypoint "$2"' _ "$WL_LIB_FILE" "$WL_UPDATE_ENTRYPOINT" ||
    wl_err "Warning: could not refresh $WL_UPDATE_ENTRYPOINT."
}

# After a good update: install the helper scripts shipped with the new
# version (if it has them), so fixes to the lib and wishlist-settings arrive
# with updates. (The unit file is installed separately, earlier in the update.)
_wl_refresh_helpers() {
  local src="$WL_APP_DIR/proxmox/misc" entry name dest mode tmp
  for entry in "wishlist-lib.sh:$WL_LIB_FILE:644" "wishlist-settings:$WL_SETTINGS_CMD:755"; do
    IFS=: read -r name dest mode <<<"$entry"
    [[ -f "$src/$name" && -d "$(dirname "$dest")" ]] || continue
    tmp="$(mktemp "$(dirname "$dest")/.$name.XXXXXX")" || continue
    if cat "$src/$name" >"$tmp" && chmod "$mode" "$tmp" &&
      { [[ $EUID -ne 0 ]] || chown root:root "$tmp"; } && mv -f "$tmp" "$dest"; then
      :
    else
      rm -f "$tmp"
      wl_err "Warning: could not refresh $dest."
    fi
  done
}

# wl_write_update_entrypoint [FILE]: write /usr/bin/update in the engine's
# format (core.func write_update_entrypoint at the pinned commit), so the
# engine's migrate_update_entrypoint leaves it alone, but with our slug. It
# runs the engine's misc/update.sh, which asks community-scripts.org about
# SCRIPT_SLUG (unknown -> update normally; unreachable -> update anyway) and
# then runs ${COMMUNITY_SCRIPTS_URL}/ct/wishlist.sh; if the helper cannot be
# fetched it runs our ct script directly. The bases come from the engine's
# environment (it exports both into the container), else our defaults.
wl_write_update_entrypoint() {
  local target="${1:-$WL_UPDATE_ENTRYPOINT}" base core website u tmp
  local re='^https?://[^[:space:]"$`\]+$'
  base="${COMMUNITY_SCRIPTS_URL:-$WL_SCRIPTS_URL_DEFAULT}"
  core="${COMMUNITY_SCRIPTS_CORE_URL:-$WL_CORE_URL_DEFAULT}"
  website="${COMMUNITY_SCRIPTS_WEBSITE_URL:-https://community-scripts.org}"
  base="${base%/}" core="${core%/}" website="${website%/}"
  for u in "$base" "$core" "$website"; do
    [[ "$u" =~ $re ]] || { wl_err "Not writing $target: unusable URL: $u"; return 1; }
  done
  tmp="$(mktemp "$(dirname "$target")/.update.XXXXXX")" || return 1
  {
    printf '%s\n' '#!/usr/bin/env bash' \
      '# Wish List update entrypoint, written by the Wish List install in the' \
      "# community-scripts engine's format, with Wish List's own script slug." \
      "export SCRIPT_SLUG=\"$WL_SCRIPT_SLUG\"" \
      'export UPDATE_SCRIPT_NAME="wishlist"' \
      "export COMMUNITY_SCRIPTS_URL=\"$base\"" \
      "export COMMUNITY_SCRIPTS_CORE_URL=\"$core\"" \
      "export COMMUNITY_SCRIPTS_WEBSITE_URL=\"$website\""
    # The rest is the engine's text verbatim (single-quoted: no expansion here).
    # shellcheck disable=SC2016
    printf '%s\n' \
      '# Pull the shared helper; it decides whether this app can still be updated. If it' \
      '# cannot be fetched, fall back to the legacy direct pull so `update` still works.' \
      '_cs_helper="$(curl -fsSL --connect-timeout 10 "${COMMUNITY_SCRIPTS_CORE_URL}/misc/update.sh" 2>/dev/null)"' \
      'if [[ -n "$_cs_helper" ]]; then' \
      '  bash -c "$_cs_helper"' \
      'else' \
      '  echo "Update helper unavailable - falling back to direct update."' \
      '  bash -c "$(curl -fsSL "${COMMUNITY_SCRIPTS_URL}/ct/${UPDATE_SCRIPT_NAME}.sh")"' \
      'fi'
  } >"$tmp" && chmod 755 "$tmp" && mv -f "$tmp" "$target" || {
    rm -f "$tmp"
    return 1
  }
}
