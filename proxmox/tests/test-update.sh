#!/usr/bin/env bash
# Runs inside the install test container (started by run-container-tests.sh)
# AFTER container-tests.sh, on the Wish List it installed. Checks the update
# routine (wl_update): up to date, upgrade, broken versions with rollback of
# code + database + pictures, GitHub unreachable, incomplete backups, locking,
# backup pruning; the /usr/bin/update entrypoint the install writes; and the
# ct script's update path run through the real (pinned) engine.
#
# "GitHub" is a local fake API (WISHLIST_GITHUB_API) and each "commit" is a
# modified copy of the repo (WISHLIST_SOURCE_DIR), so nothing is downloaded
# from GitHub except the pinned engine files.
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
  kill "${api_pid:-}" "${web_pid:-}" 2>/dev/null
  echo
  echo "update tests: $PASS passed, $FAIL failed"
  [[ $FAIL -eq 0 ]]
  exit
}

CORE_PIN="https://raw.githubusercontent.com/community-scripts/core/665635b6aff5cf1486400d47e6c5aa38f9ba333d"
OUR_SCRIPTS="https://raw.githubusercontent.com/butthe4d/wish-list/main/proxmox"
LIB=/usr/local/lib/wishlist/wishlist-lib.sh
DB=/var/lib/wishlist/wishlist.db
BK=/var/lib/wishlist-backups

if [[ "$(curl -fsS --max-time 5 http://127.0.0.1:8000/healthz 2>/dev/null)" != ok ]]; then
  bad "precondition: Wish List installed and healthy (run container-tests.sh first)"
  finish
fi
# The repo's lib (what is under test); the installed copy came from the same
# checkout during container-tests.sh.
# shellcheck source-path=SCRIPTDIR source=../misc/wishlist-lib.sh
. "$REPO/proxmox/misc/wishlist-lib.sh"

health() { curl -fsS --max-time 5 http://127.0.0.1:8000/healthz 2>/dev/null; }
app_pid() { cat /run/stub-systemd/wishlist.pid 2>/dev/null; }
n_backups() { find "$BK" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l; }
version() { cat /opt/wishlist_version.txt; }
inode() { stat -c %i /opt/wishlist; }
alembic() { sqlite3 "$DB" 'SELECT version_num FROM alembic_version'; }

# --- fake GitHub API --------------------------------------------------------
F=/tmp/update-api
rm -rf "$F"
mkdir -p "$F/repos/butthe4d/wish-list/commits"
python3 -m http.server 8766 --bind 127.0.0.1 --directory "$F" >/dev/null 2>&1 &
api_pid=$!
export WISHLIST_GITHUB_API=http://127.0.0.1:8766
set_latest() { printf '{"sha": "%s", "commit": {}}\n' "$1" >"$F/repos/butthe4d/wish-list/commits/main"; }
sleep 1

# --- "commits": modified copies of the repo -----------------------------------
mk_src() { # name -> /tmp/src-<name>, a writable copy of the repo
  local d="/tmp/src-$1"
  rm -rf "$d" && mkdir -p "$d"
  tar -C "$REPO" --exclude=./.git --exclude=./.venv --exclude=./.superpowers --exclude=__pycache__ \
    --exclude='*.db' --exclude='*.mo' -cf - . | tar -C "$d" -xf -
  printf '%s' "$d"
}
SRC_V2="$(mk_src v2)"
echo "# update test: v2" >>"$SRC_V2/wishlist/__init__.py"
echo "# update test: helpers v2" >>"$SRC_V2/proxmox/misc/wishlist-lib.sh"
echo "# update test: settings v2" >>"$SRC_V2/proxmox/misc/wishlist-settings"
SRC_V3="$(mk_src v3)"
echo "# update test: v3" >>"$SRC_V3/wishlist/__init__.py"
SRC_V4="$(mk_src v4)"
echo "# update test: v4" >>"$SRC_V4/wishlist/__init__.py"
SRC_IMPORT="$(mk_src import-broken)"
echo 'raise RuntimeError("update test: broken on import")' >>"$SRC_IMPORT/wishlist/__init__.py"
# Imports and migrates fine, then never becomes healthy: the migration runs
# against the live database, so the rollback must restore the database too.
SRC_START="$(mk_src start-broken)"
cat >"$SRC_START/wishlist/migrations/versions/0099_update_test.py" <<'PY'
"""update test: a migration that a rollback must undo"""
from alembic import op
import sqlalchemy as sa

revision = '0099'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('update_test_marker', sa.Column('id', sa.Integer(), primary_key=True))


def downgrade() -> None:
    op.drop_table('update_test_marker')
PY
grep -q "down_revision = '0002'" "$SRC_START/wishlist/migrations/versions/0099_update_test.py" &&
  [[ "$(find "$REPO/wishlist/migrations/versions" -name '00*.py' | sort | tail -n 1)" == */0002_* ]] ||
  bad "test setup: 0002 is no longer the newest migration; update the test migration's down_revision"
sed -i 's/return "ok"/return "broken"/' "$SRC_START/wishlist/__init__.py"
grep -q 'return "broken"' "$SRC_START/wishlist/__init__.py" || bad "test setup: could not break /healthz"

# A picture and a row that must survive every failed update.
setpriv --reuid=wishlist --regid=wishlist --init-groups bash -c \
  'mkdir -p /var/lib/wishlist/images && echo keep-me >/var/lib/wishlist/images/update-test.jpg'
rm -rf "$BK"
start_version="$(version)"

# --- (a) already up to date ----------------------------------------------------
echo "== already up to date =="
set_latest "$start_version"
pid="$(app_pid)"
out="$(wl_update 2>&1)"
eq "up to date: exit 0" 0 "$?"
check "up to date: says so" grep -qi "already up to date" <<<"$out"
eq "up to date: no backup" 0 "$(n_backups)"
eq "up to date: service untouched" "$pid" "$(app_pid)"

# --- (d) GitHub unreachable ------------------------------------------------------
echo "== GitHub unreachable =="
before_inode="$(inode)"
out="$(WISHLIST_GITHUB_API=http://127.0.0.1:1 wl_update 2>&1)"
rc=$?
check "GitHub unreachable: non-zero exit" test "$rc" -ne 0
check "GitHub unreachable: clear message" grep -q "Could not check for a new version" <<<"$out"
check "GitHub unreachable: says nothing changed" grep -qi "nothing was changed" <<<"$out"
eq "GitHub unreachable: version unchanged" "$start_version" "$(version)"
eq "GitHub unreachable: code dir unchanged" "$before_inode" "$(inode)"
eq "GitHub unreachable: no backup" 0 "$(n_backups)"
eq "GitHub unreachable: service untouched" "$pid" "$(app_pid)"

# --- (b) upgrade -----------------------------------------------------------------
echo "== upgrade to a new version (a minute) =="
SHA_V2=2222222222222222222222222222222222222222
set_latest "$SHA_V2"
out="$(WISHLIST_SOURCE_DIR=$SRC_V2 wl_update 2>&1)"
rc=$?
eq "upgrade: exit 0" 0 "$rc"
[[ $rc == 0 ]] || echo "$out" | tail -n 30
eq "upgrade: version file updated" "$SHA_V2" "$(version)"
eq "upgrade: /healthz ok" ok "$(health)"
eq "upgrade: one backup created" 1 "$(n_backups)"
check "upgrade: new code is live" grep -q "update test: v2" /opt/wishlist/wishlist/__init__.py
check "upgrade: previous code kept as .prev" bash -c '! grep -q "update test" /opt/wishlist.prev/wishlist/__init__.py'
check "upgrade: no staging dir left" test ! -e /opt/wishlist.new
b1="$(find "$BK" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
eq "upgrade: backup records the previous version" "$start_version" "$(cat "$b1/wishlist_version.txt" 2>/dev/null)"
check "upgrade: backup has the database" test -s "$b1/wishlist.db"
eq "upgrade: pictures kept" keep-me "$(cat /var/lib/wishlist/images/update-test.jpg 2>/dev/null)"
check "upgrade: helpers refreshed from the new version (lib)" grep -q "update test: helpers v2" "$LIB"
check "upgrade: helpers refreshed from the new version (wishlist-settings)" grep -q "update test: settings v2" /usr/local/bin/wishlist-settings
eq "upgrade: new code readable by everyone (not built under the lock's umask)" "755 644" \
  "$(stat -c %a /opt/wishlist/.venv/bin /opt/wishlist/pyproject.toml | tr '\n' ' ' | sed 's/ $//')"
eq "upgrade: version file mode" 644 "$(stat -c %a /opt/wishlist_version.txt)"
eq "upgrade: refreshed lib mode" "644 root" "$(stat -c '%a %U' "$LIB")"
eq "upgrade: refreshed wishlist-settings mode" "755 root" "$(stat -c '%a %U' /usr/local/bin/wishlist-settings)"
check "upgrade: says it updated" grep -q "Updated Wish List to $SHA_V2" <<<"$out"

# --- (c1) broken version: fails on import (caught while building) -----------------
echo "== broken version: import error (a minute) =="
set_latest 3333333333333333333333333333333333333333
before_inode="$(inode)"
pid="$(app_pid)"
nb="$(n_backups)"
out="$(WISHLIST_SOURCE_DIR=$SRC_IMPORT wl_update 2>&1)"
rc=$?
check "import error: non-zero exit" test "$rc" -ne 0
check "import error: shows the error" grep -q "broken on import" <<<"$out"
eq "import error: version unchanged" "$SHA_V2" "$(version)"
eq "import error: code dir unchanged" "$before_inode" "$(inode)"
eq "import error: /healthz ok" ok "$(health)"
eq "import error: service never stopped" "$pid" "$(app_pid)"
eq "import error: no backup needed" "$nb" "$(n_backups)"
check "import error: no staging dir left" test ! -e /opt/wishlist.new

# A failing build step that is not the last one (translations cannot be
# compiled; the app itself would still import) must fail the build.
echo "== broken version: translations do not compile =="
SRC_I18N="$(mk_src i18n-broken)"
rm -rf "$SRC_I18N/wishlist/translations"
set_latest 3434343434343434343434343434343434343434
before_inode="$(inode)"
out="$(WISHLIST_SOURCE_DIR=$SRC_I18N wl_update 2>&1)"
rc=$?
check "translations: non-zero exit" test "$rc" -ne 0
eq "translations: version unchanged" "$SHA_V2" "$(version)"
eq "translations: code dir unchanged" "$before_inode" "$(inode)"
eq "translations: /healthz ok" ok "$(health)"

# --- (c2) broken version: migrates, then never healthy -> full rollback -----------
echo "== broken version: unhealthy after migrating (a minute) =="
set_latest 4444444444444444444444444444444444444444
before_inode="$(inode)"
before_alembic="$(alembic)"
nb="$(n_backups)"
out="$(WISHLIST_HEALTH_TIMEOUT=15 WISHLIST_SOURCE_DIR=$SRC_START wl_update 2>&1)"
rc=$?
check "unhealthy: non-zero exit" test "$rc" -ne 0
check "unhealthy: reports the rollback" grep -q "rolled back" <<<"$out"
eq "unhealthy: version restored" "$SHA_V2" "$(version)"
eq "unhealthy: previous code dir restored" "$before_inode" "$(inode)"
check "unhealthy: previous code is live" grep -q "update test: v2" /opt/wishlist/wishlist/__init__.py
eq "unhealthy: database restored (migration undone)" "$before_alembic" "$(alembic)"
eq "unhealthy: database restored (no new table)" "" \
  "$(sqlite3 "$DB" "SELECT name FROM sqlite_master WHERE name='update_test_marker'")"
eq "unhealthy: pictures restored" keep-me "$(cat /var/lib/wishlist/images/update-test.jpg 2>/dev/null)"
eq "unhealthy: /healthz ok again" ok "$(health)"
eq "unhealthy: backup made before the change" $((nb + 1)) "$(n_backups)"
check "unhealthy: no leftover dirs" test ! -e /opt/wishlist.new -a ! -e /opt/wishlist.failed
check "unhealthy: helpers not replaced by the broken version" grep -q "update test: helpers v2" "$LIB"

# --- incomplete backup aborts the update before anything changes ------------------
echo "== incomplete backup =="
# A picture the app user cannot read: its tar fails, while the unpacking side
# succeeds with a partial copy.
echo unreadable >/var/lib/wishlist/images/zz-root-only.jpg
chmod 600 /var/lib/wishlist/images/zz-root-only.jpg
nb="$(n_backups)"
# pipefail off, as in a caller that never set it: the check must not rely on it.
out="$(set +o pipefail; wl_backup 2>&1)"
rc=$?
check "incomplete backup: wl_backup fails" test "$rc" -ne 0
check "incomplete backup: says so" grep -q "Backing up the pictures failed" <<<"$out"
eq "incomplete backup: no partial backup left" "$nb" "$(n_backups)"
set_latest 5555555555555555555555555555555555555555
before_inode="$(inode)"
out="$(WISHLIST_SOURCE_DIR=$SRC_V3 wl_update 2>&1)"
rc=$?
check "incomplete backup: update fails" test "$rc" -ne 0
check "incomplete backup: update says it was aborted" grep -qi "backup failed.*nothing was changed" <<<"$out"
eq "incomplete backup: version unchanged" "$SHA_V2" "$(version)"
eq "incomplete backup: code dir unchanged" "$before_inode" "$(inode)"
check "incomplete backup: staging removed" test ! -e /opt/wishlist.new
eq "incomplete backup: app running again" ok "$(health)"
rm -f /var/lib/wishlist/images/zz-root-only.jpg

# --- locking -----------------------------------------------------------------------
echo "== locking =="
flock /run/wishlist/maintenance.lock sleep 5 &
locker=$!
sleep 0.5
out="$(WL_LOCK_TIMEOUT=1 WISHLIST_SOURCE_DIR=$SRC_V3 wl_update 2>&1)"
rc=$?
check "held lock: update gives up" test "$rc" -ne 0
check "held lock: says why" grep -q "maintenance task" <<<"$out"
eq "held lock: version unchanged" "$SHA_V2" "$(version)"
wait "$locker"

# --- (e) four upgrades keep three backups -----------------------------------------
echo "== more upgrades: keep the newest 3 backups (two minutes) =="
SHA_V3=6666666666666666666666666666666666666666
SHA_V4=7777777777777777777777777777777777777777
set_latest "$SHA_V3"
WISHLIST_SOURCE_DIR=$SRC_V3 wl_update >/dev/null 2>&1
eq "upgrade 3: exit 0" "$SHA_V3" "$(version)"
set_latest "$SHA_V4"
WISHLIST_SOURCE_DIR=$SRC_V4 wl_update >/dev/null 2>&1
eq "upgrade 4: exit 0" "$SHA_V4" "$(version)"
eq "four backups made, newest 3 kept" 3 "$(n_backups)"
newest="$(find "$BK" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort | tail -n 1)"
eq "newest backup is the one before the last upgrade" "$SHA_V3" "$(cat "$BK/$newest/wishlist_version.txt")"
eq "healthy after the upgrades" ok "$(health)"

# --- /usr/bin/update entrypoint ----------------------------------------------------
echo "== /usr/bin/update =="
U=/usr/bin/update
check "install wrote /usr/bin/update" test -x "$U"
eq "/usr/bin/update mode" "755 root" "$(stat -c '%a %U' "$U" 2>/dev/null)"
check "slug is ours, not the catalogue's \"wishlist\"" grep -qx 'export SCRIPT_SLUG="butthe4d-wish-list"' "$U"
check "pulls ct/wishlist.sh" grep -qx 'export UPDATE_SCRIPT_NAME="wishlist"' "$U"
check "from our repo" grep -qx "export COMMUNITY_SCRIPTS_URL=\"$OUR_SCRIPTS\"" "$U"
check "with the pinned engine" grep -qx "export COMMUNITY_SCRIPTS_CORE_URL=\"$CORE_PIN\"" "$U"
check "runs the engine's update helper" grep -q 'misc/update.sh' "$U"
check "falls back to a direct pull of our ct script" grep -qF 'bash -c "$(curl -fsSL "${COMMUNITY_SCRIPTS_URL}/ct/${UPDATE_SCRIPT_NAME}.sh")"' "$U"
check "is valid bash" bash -n "$U"

# The real engine (pinned) leaves it alone on update.
if curl -fsSL "$CORE_PIN/core/core.func" -o /tmp/core.func; then
  sed -n '/^_cs_live_base() {/,/^}/p; /^write_update_entrypoint() {/,/^}/p; /^migrate_update_entrypoint() {/,/^}/p' \
    /tmp/core.func >/tmp/entry.sh
  sum="$(sha256sum <"$U")"
  bash -c '. /tmp/entry.sh; declare -F migrate_update_entrypoint >/dev/null || exit 99; NSAPP=wishlist migrate_update_entrypoint'
  eq "engine's migrate_update_entrypoint ran" 0 "$?"
  eq "engine's migrate_update_entrypoint keeps our entrypoint" "$sum" "$(sha256sum <"$U")"
else
  bad "could not download core.func from $CORE_PIN"
fi

# Run an entrypoint end to end: the engine's real update helper (pinned) asks
# the "website" (fake: unknown slug, then unreachable) and pulls our ct script.
W=/tmp/fake-web
rm -rf "$W" && mkdir -p "$W/api" "$W/ct"
printf '#!/usr/bin/env bash\necho "RAN-OUR-CT-SCRIPT slug=$SCRIPT_SLUG"\n' >"$W/ct/wishlist.sh"
echo '{"slug":"butthe4d-wish-list","found":false,"state":"unknown"}' >"$W/api/update-info"
python3 -m http.server 8767 --bind 127.0.0.1 --directory "$W" >/dev/null 2>&1 &
web_pid=$!
sleep 1
COMMUNITY_SCRIPTS_URL=http://127.0.0.1:8767 COMMUNITY_SCRIPTS_CORE_URL="$CORE_PIN" \
  COMMUNITY_SCRIPTS_WEBSITE_URL=http://127.0.0.1:8767 wl_write_update_entrypoint /tmp/update-e2e
out="$(env -i PATH="$PATH" HOME=/root bash /tmp/update-e2e 2>&1)"
check "entrypoint -> engine helper -> our ct script" grep -q "RAN-OUR-CT-SCRIPT slug=butthe4d-wish-list" <<<"$out"
COMMUNITY_SCRIPTS_URL=http://127.0.0.1:8767 COMMUNITY_SCRIPTS_CORE_URL="$CORE_PIN" \
  COMMUNITY_SCRIPTS_WEBSITE_URL=http://127.0.0.1:1 wl_write_update_entrypoint /tmp/update-e2e
out="$(env -i PATH="$PATH" HOME=/root bash /tmp/update-e2e 2>&1)"
check "website unreachable: still runs our ct script" grep -q "RAN-OUR-CT-SCRIPT" <<<"$out"
COMMUNITY_SCRIPTS_URL=http://127.0.0.1:8767 COMMUNITY_SCRIPTS_CORE_URL=http://127.0.0.1:1 \
  wl_write_update_entrypoint /tmp/update-e2e
out="$(env -i PATH="$PATH" HOME=/root bash /tmp/update-e2e 2>&1)"
check "engine unreachable: falls back to our ct script" grep -q "RAN-OUR-CT-SCRIPT" <<<"$out"
check "a URL that could break out of the file is refused" \
  bash -c ". $LIB; ! COMMUNITY_SCRIPTS_URL='http://x\"; touch /tmp/pwned; \"' wl_write_update_entrypoint /tmp/update-bad 2>/dev/null"
check "nothing written for a refused URL" test ! -e /tmp/update-bad

# --- ct script through the real engine (update path inside the container) --------
echo "== ct/wishlist.sh update through the pinned engine =="
# Inside a container (no pveversion) start() runs update_script. PHS_SILENT
# skips the menu; DIAGNOSTICS=no keeps test runs out of upstream telemetry.
ct_run() {
  env PHS_SILENT=1 DIAGNOSTICS=no TERM=dumb COMMUNITY_SCRIPTS_CORE_URL="$CORE_PIN" \
    COMMUNITY_SCRIPTS_URL=http://127.0.0.1:8767 "$@" bash "$REPO/proxmox/ct/wishlist.sh" </dev/null 2>&1
}
cp -p "$U" /tmp/update.saved
out="$(ct_run)"
rc=$?
eq "engine run, up to date: exit 0" 0 "$rc"
check "engine run, up to date: says so" grep -qi "already up to date" <<<"$out"
[[ $rc == 0 ]] || echo "$out" | tail -n 40
out="$(ct_run WISHLIST_GITHUB_API=http://127.0.0.1:1)"
rc=$?
check "engine run, GitHub unreachable: non-zero exit" test "$rc" -ne 0
check "engine run, GitHub unreachable: message" grep -q "Could not check for a new version" <<<"$out"
eq "engine run: version unchanged" "$SHA_V4" "$(version)"
eq "engine run: healthy" ok "$(health)"
check "engine run kept our /usr/bin/update" cmp -s "$U" /tmp/update.saved

finish
