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
# atomically, keeping the file root:wishlist 640.
wl_settings_set() {
  local key="$1" value="$2" dir tmp line done=0
  wl_validate "$key" "$value" || return 1
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
# the terminal, for scripted runs and tests.
# ---------------------------------------------------------------------------

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
  [[ -n "${WL_ANSWER_FD:-}" ]] && return 0
  local src
  src="$(wl_answer_source)" || return 1
  exec {WL_ANSWER_FD}<"$src"
}

wl_read_line() { # varname [silent]
  wl_open_answers || return 1
  local _line
  if [[ "${2:-}" == silent && -z "${WISHLIST_ANSWERS:-}" ]]; then
    IFS= read -r -s -u "$WL_ANSWER_FD" _line || return 1
    echo >&2
  else
    IFS= read -r -u "$WL_ANSWER_FD" _line || return 1
  fi
  printf -v "$1" '%s' "$_line"
}

WL_BACKTITLE="Wish List"

# wl_ask_yesno QUESTION [default y|n]: status 0 for yes.
wl_ask_yesno() {
  local q="$1" def="${2:-n}" ans
  if wl_use_whiptail; then
    local -a dflt=()
    [[ "$def" == n ]] && dflt=(--defaultno)
    whiptail --backtitle "$WL_BACKTITLE" --title "Wish List" "${dflt[@]}" --yesno "$q" 12 70 </dev/tty >/dev/tty 2>&1
    return
  fi
  while :; do
    printf '%s [%s] ' "$q" "$([[ "$def" == y ]] && echo Y/n || echo y/N)" >&2
    wl_read_line ans || { wl_err "No answer available."; return 2; }
    case "${ans,,}" in
    "") [[ "$def" == y ]]; return ;;
    y | yes) return 0 ;;
    n | no) return 1 ;;
    *) wl_err "Please answer y or n." ;;
    esac
  done
}

# wl_ask_input PROMPT DEFAULT [secret]: prints the answer; status 1 on cancel.
wl_ask_input() {
  local prompt="$1" def="$2" secret="${3:-}" ans
  if wl_use_whiptail; then
    local box=--inputbox
    [[ "$secret" == secret ]] && box=--passwordbox
    ans="$(whiptail --backtitle "$WL_BACKTITLE" --title "Wish List" "$box" "$prompt" 12 74 "$def" 2>&1 >/dev/tty </dev/tty)" ||
      return 1
    printf '%s' "$ans"
    return 0
  fi
  if [[ "$secret" == secret && -n "$def" ]]; then
    printf '%s [keep current]: ' "$prompt" >&2
  else
    printf '%s [%s]: ' "$prompt" "$def" >&2
  fi
  wl_read_line ans "$secret" || { wl_err "No answer available."; return 1; }
  [[ -z "$ans" ]] && ans="$def"
  printf '%s' "$ans"
}

# wl_ask_setting KEY PROMPT DEFAULT [secret]: asks until wl_validate accepts;
# prints the value. Status 1 on cancel / no more answers.
wl_ask_setting() {
  local key="$1" prompt="$2" def="$3" secret="${4:-}" v msg
  while :; do
    v="$(wl_ask_input "$prompt" "$def" "$secret")" || return 1
    if msg="$(wl_validate "$key" "$v" 2>&1)"; then
      printf '%s' "$v"
      return 0
    fi
    if wl_use_whiptail; then
      whiptail --backtitle "$WL_BACKTITLE" --title "Invalid value" --msgbox "$msg" 10 70 </dev/tty >/dev/tty 2>&1
    else
      wl_err "$msg"
    fi
  done
}

wl_container_ip() {
  local ip=""
  ip="$(hostname -I 2>/dev/null | awk '{print $1}')" || true
  [[ -z "$ip" ]] && ip="$(ip -4 -o addr show scope global 2>/dev/null | awk '{split($4, a, "/"); print a[1]; exit}')"
  printf '%s' "${ip:-127.0.0.1}"
}

wl_is_unattended() {
  [[ "${WISHLIST_UNATTENDED:-0}" == 1 || "${PHS_SILENT:-0}" == 1 ]] && return 0
  [[ "${var_unattended:-}" =~ ^(yes|true|1)$ || "${UNATTENDED:-}" =~ ^(yes|true|1)$ ]] && return 0
  ! wl_can_prompt
}

# wl_collect_settings: write the complete settings file, asking
# "Configure Wish List now?" first unless unattended (defaults otherwise).
# Existing SECRET_KEY is kept; a new one is generated otherwise.
wl_collect_settings() {
  local ip base_url name email lang channel repo secret
  local smtp_host="" smtp_port=587 smtp_sec=starttls smtp_user="" smtp_pass="" smtp_from=""
  local sms_token="" sms_sender="" cidrs=""
  ip="$(wl_container_ip)"
  base_url="http://$ip:$WL_PORT"
  name=Admin
  email=admin@example.com
  lang=en
  channel="${WISHLIST_CHANNEL:-main}"
  repo="${WISHLIST_REPO:-$WL_DEFAULT_REPO}"

  if ! wl_is_unattended && wl_open_answers && wl_ask_yesno "Configure Wish List now?

No: use defaults (address $base_url, admin admin@example.com, English, no email or SMS). Everything can be changed later with: wishlist-settings" y; then
    base_url="$(wl_ask_setting BASE_URL "Address people use to open the app (BASE_URL)" "$base_url")" || return 1
    name="$(wl_ask_input "First admin's name" "$name")" || return 1
    email="$(wl_ask_setting ADMIN_EMAIL "First admin's email" "$email")" || return 1
    lang="$(wl_ask_setting DEFAULT_LANGUAGE "Default language (en or pl)" "$lang")" || return 1
    if wl_ask_yesno "Send email (links, notifications) through an SMTP server?" n; then
      smtp_host="$(wl_ask_input "SMTP host" "")" || return 1
      smtp_port="$(wl_ask_setting SMTP_PORT "SMTP port" "$smtp_port")" || return 1
      smtp_sec="$(wl_ask_setting SMTP_SECURITY "SMTP security (starttls, ssl or none)" "$smtp_sec")" || return 1
      smtp_user="$(wl_ask_input "SMTP user (empty for none)" "")" || return 1
      smtp_pass="$(wl_ask_input "SMTP password" "" secret)" || return 1
      smtp_from="$(wl_ask_setting SMTP_FROM "Sender address (empty = SMTP user)" "")" || return 1
    fi
    if wl_ask_yesno "Send SMS through gsmservice.pl?" n; then
      sms_token="$(wl_ask_input "gsmservice.pl API token" "" secret)" || return 1
      sms_sender="$(wl_ask_input "SMS sender name" "")" || return 1
    fi
    cidrs="$(wl_ask_setting FETCH_DENY_CIDRS "Extra networks the picture fetcher must never reach, e.g. your public IP as 203.0.113.7/32 (empty for none)" "")" || return 1
  fi

  secret="$(wl_settings_get SECRET_KEY 2>/dev/null)" || secret=""
  [[ -n "$secret" ]] || secret="$(openssl rand -hex 32)" || return 1

  local -A v=(
    [BASE_URL]="$base_url" [SECRET_KEY]="$secret"
    [DATABASE_URL]="sqlite:///$WL_DATA_DIR/wishlist.db" [DATA_DIR]="$WL_DATA_DIR"
    [ADMIN_NAME]="$name" [ADMIN_EMAIL]="$email" [DEFAULT_LANGUAGE]="$lang"
    [SMTP_HOST]="$smtp_host" [SMTP_PORT]="$smtp_port" [SMTP_SECURITY]="$smtp_sec"
    [SMTP_USER]="$smtp_user" [SMTP_PASSWORD]="$smtp_pass" [SMTP_FROM]="$smtp_from"
    [GSMSERVICE_TOKEN]="$sms_token" [GSMSERVICE_SENDER]="$sms_sender"
    [FETCH_DENY_CIDRS]="$cidrs" [WISHLIST_CHANNEL]="$channel" [WISHLIST_REPO]="$repo"
  )
  local k
  for k in "${WL_KEYS[@]}"; do
    wl_settings_set "$k" "${v[$k]}" || return 1
  done
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

# wl_apply_settings NEW_FILE: install NEW_FILE as the settings file, restart
# and health-check; on failure put the previous file back, restart, print
# the service log and return 1.
wl_apply_settings() {
  local new="$1" old log
  old="$(mktemp "$(dirname "$WISHLIST_ENV")/.wishlist.env.prev.XXXXXX")" || return 1
  cp -p "$WISHLIST_ENV" "$old" || return 1
  wl_secure_env_file "$new" && mv -f "$new" "$WISHLIST_ENV" || { rm -f "$old"; return 1; }
  if systemctl restart "$WL_SERVICE" && wl_health; then
    rm -f "$old"
    return 0
  fi
  log="$(journalctl -u "$WL_SERVICE" -n 20 --no-pager 2>&1)"
  mv -f "$old" "$WISHLIST_ENV"
  systemctl restart "$WL_SERVICE" || true
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

# wl_install_ref REF TARGET_DIR: fetch REF (commit sha or tag) from GitHub, or
# copy $WISHLIST_SOURCE_DIR (tests; REF is then only recorded), build a fresh
# venv with the app and compiled translations in TARGET_DIR.new, then swap it
# in; the previous TARGET_DIR is kept as TARGET_DIR.prev. Prints nothing on
# success; on failure prints the build log tail and leaves TARGET_DIR alone.
# Run the app from the moved venv with `.venv/bin/python -m ...`: console
# scripts in .venv/bin keep the .new path in their shebang.
wl_install_ref() {
  local ref="$1" target="${2%/}" stage log repo rc=0
  if [[ ! "$ref" =~ ^[A-Za-z0-9][A-Za-z0-9._/-]*$ || "$ref" == *..* ]]; then
    wl_err "Invalid ref: $ref"
    return 1
  fi
  [[ -n "$target" && "$target" == /* ]] || { wl_err "Target must be an absolute path."; return 1; }
  stage="$target.new"
  rm -rf "$stage" && mkdir -p "$stage" || return 1
  log="$(mktemp)" || return 1
  {
    if [[ -n "${WISHLIST_SOURCE_DIR:-}" ]]; then
      tar -C "$WISHLIST_SOURCE_DIR" --exclude=./.git --exclude=./.venv --exclude='*.egg-info' \
        --exclude=__pycache__ --exclude=./.superpowers --exclude='*.db' --exclude='*.mo' -cf - . |
        tar -C "$stage" -xf -
    else
      repo="$(wl_repo)"
      curl -fsSL --retry 3 "https://github.com/$repo/archive/$ref.tar.gz" |
        tar -xz --strip-components=1 -C "$stage"
    fi
  } >>"$log" 2>&1 || rc=1
  if [[ $rc == 0 && ! -f "$stage/pyproject.toml" ]]; then
    echo "No pyproject.toml in the downloaded source." >>"$log"
    rc=1
  fi
  if [[ $rc == 0 ]]; then
    (
      set -e
      export PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_INPUT=1
      python3 -m venv "$stage/.venv"
      "$stage/.venv/bin/python" -m pip install -q babel
      "$stage/.venv/bin/python" -m babel.messages.frontend compile -d "$stage/wishlist/translations"
      "$stage/.venv/bin/python" -m pip install -q "$stage"
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
  chown -R root:root "$stage" && chmod 755 "$stage" || return 1
  if [[ -e "$target" ]]; then
    rm -rf "$target.prev" && mv "$target" "$target.prev" || return 1
  fi
  mv "$stage" "$target"
}

# wl_latest_ref CHANNEL: newest commit sha on main (main), or newest release
# tag, falling back to the newest tag (releases).
wl_latest_ref() {
  local channel="$1" api="${WISHLIST_GITHUB_API:-https://api.github.com}" repo json ref
  repo="$(wl_repo)"
  case "$channel" in
  main)
    json="$(curl -fsSL --max-time 20 -H 'Accept: application/vnd.github+json' "$api/repos/$repo/commits/main")" || {
      wl_err "Could not read the latest commit of $repo from GitHub."
      return 1
    }
    ref="$(python3 -I -c 'import json, sys; print(json.load(sys.stdin)["sha"])' <<<"$json" 2>/dev/null)"
    ;;
  releases)
    if json="$(curl -fsSL --max-time 20 -H 'Accept: application/vnd.github+json' "$api/repos/$repo/releases/latest" 2>/dev/null)"; then
      ref="$(python3 -I -c 'import json, sys; print(json.load(sys.stdin)["tag_name"])' <<<"$json" 2>/dev/null)"
    elif json="$(curl -fsSL --max-time 20 -H 'Accept: application/vnd.github+json' "$api/repos/$repo/tags")"; then
      ref="$(python3 -I -c 'import json, sys; print(json.load(sys.stdin)[0]["name"])' <<<"$json" 2>/dev/null)"
    else
      wl_err "Could not read releases or tags of $repo from GitHub."
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

# wl_backup: print the new backup directory.
wl_backup() {
  local dir db
  db="$(wl_db_path)"
  mkdir -p "$WL_BACKUP_ROOT" && chmod 700 "$WL_BACKUP_ROOT" || return 1
  dir="$(mktemp -d "$WL_BACKUP_ROOT/$(date -u +%Y%m%dT%H%M%SZ).XXXX")" || return 1
  if [[ -f "$db" ]]; then
    sqlite3 "$db" ".backup '$dir/wishlist.db'" || { wl_err "Database backup failed."; rm -rf "$dir"; return 1; }
  fi
  if [[ -d "$WL_DATA_DIR/images" ]]; then
    cp -a "$WL_DATA_DIR/images" "$dir/images" || { rm -rf "$dir"; return 1; }
  else
    mkdir "$dir/images"
  fi
  [[ -f "$WISHLIST_ENV" ]] && { cp -p "$WISHLIST_ENV" "$dir/wishlist.env" || return 1; }
  [[ -f "$WL_VERSION_FILE" ]] && { cp -p "$WL_VERSION_FILE" "$dir/wishlist_version.txt" || return 1; }
  printf '%s\n' "$dir"
}

# wl_restore DIR: put a backup's database, images, settings and version back.
# Stop the service first.
wl_restore() {
  local dir="${1%/}" db
  [[ -d "$dir" ]] || { wl_err "No such backup: $dir"; return 1; }
  if [[ -f "$dir/wishlist.env" ]]; then
    cp -p "$dir/wishlist.env" "$WISHLIST_ENV" && wl_secure_env_file "$WISHLIST_ENV" || return 1
  fi
  [[ -f "$dir/wishlist_version.txt" ]] && { cp -p "$dir/wishlist_version.txt" "$WL_VERSION_FILE" || return 1; }
  db="$(wl_db_path)"
  if [[ -f "$dir/wishlist.db" ]]; then
    rm -f "$db-wal" "$db-shm" "$db-journal"
    install -o "$WL_USER" -g "$WL_USER" -m 640 "$dir/wishlist.db" "$db" || return 1
  fi
  if [[ -d "$dir/images" ]]; then
    rm -rf "$WL_DATA_DIR/images" && cp -a "$dir/images" "$WL_DATA_DIR/images" || return 1
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
