# Proxmox Community-Style Install Script Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Install Wish List on Proxmox as an LXC with the community-scripts engine, configurable at install and anytime after, and upgradable with `update`.

**Architecture:** `proxmox/ct/wishlist.sh` (runs on the Proxmox host, sources the pinned community-scripts engine, defines `update_script`) and `proxmox/install/wishlist-install.sh` (runs inside the new container: packages, user, code, venv, settings, systemd). Shared container-side logic lives in `proxmox/misc/wishlist-lib.sh` (install code at a ref, settings read/write, backup/rollback), installed into the container as `/usr/local/lib/wishlist/wishlist-lib.sh` and used by the install script, `wishlist-settings` and `update`.

**Tech Stack:** bash, whiptail, systemd, community-scripts core engine (pinned), Debian 13 LXC, existing app (gunicorn, `python -m wishlist.bootstrap`).

**Spec:** `docs/superpowers/specs/2026-11-13-proxmox-script-design.md`

## Global Constraints

- Paths (exact): code `/opt/wishlist`, venv `/opt/wishlist/.venv`, data `/var/lib/wishlist`, settings `/etc/wishlist/wishlist.env` (root:wishlist, 640), lib `/usr/local/lib/wishlist/wishlist-lib.sh`, commands `/usr/local/bin/wishlist-settings`, version file `/opt/wishlist_version.txt`, creds `/root/wishlist.creds` (600), backups `/var/lib/wishlist-backups/` (keep last 3), unit `/etc/systemd/system/wishlist.service`, system user `wishlist`, port 8000.
- Env written to the settings file: `BASE_URL SECRET_KEY DATABASE_URL=sqlite:////var/lib/wishlist/wishlist.db DATA_DIR=/var/lib/wishlist ADMIN_NAME ADMIN_EMAIL DEFAULT_LANGUAGE SMTP_HOST SMTP_PORT SMTP_SECURITY SMTP_USER SMTP_PASSWORD SMTP_FROM GSMSERVICE_TOKEN GSMSERVICE_SENDER FETCH_DENY_CIDRS` plus `WISHLIST_CHANNEL` (`main`|`releases`, default `main`) and `WISHLIST_REPO=butthe4d/wish-list`. Values are written shell-safe (single-quoted with `'\''` escaping) and read without `eval` of untrusted input (systemd `EnvironmentFile` + a safe parser in the lib).
- Defaults when "configure later": BASE_URL `http://<container-IP>:8000`, ADMIN_NAME `Admin`, ADMIN_EMAIL `admin@example.com`, DEFAULT_LANGUAGE `en`, no SMTP/SMS. SECRET_KEY always generated (`openssl rand -hex 32`).
- Container defaults in the ct script: `var_cpu=2 var_ram=1024 var_disk=4 var_os=debian var_version=13 var_unprivileged=1 var_tags=wishlist`.
- Engine pin: `COMMUNITY_SCRIPTS_CORE_URL` default `https://raw.githubusercontent.com/community-scripts/core/665635b6aff5cf1486400d47e6c5aa38f9ba333d`; `COMMUNITY_SCRIPTS_URL` default `https://raw.githubusercontent.com/butthe4d/wish-list/main/proxmox`; both overridable by env exactly like upstream ct scripts. The `/usr/bin/update` the engine writes must point at our ct script (verify in the engine's `lxc/install.func`; if it doesn't carry our URL into the container, the install script rewrites `/usr/bin/update` itself).
- Update: compare installed ref with GitHub (`main` → `GET /repos/{repo}/commits/main` sha; `releases` → latest release tag, falling back to latest tag); if equal, say "already up to date" and exit 0. Otherwise: backup (sqlite `.backup` + `images/` + settings + version) → stop → install new ref into a fresh dir, swap → `bootstrap` (migrations) → start → health check `curl -fsS http://127.0.0.1:8000/healthz` within 30 s; on any failure restore previous code dir + DB backup, restart, report error, exit non-zero.
- `wishlist-settings`: whiptail menu listing every setting with current value (SMTP_PASSWORD/GSMSERVICE_TOKEN shown masked); editing validates (BASE_URL http(s)://, email has @, language en|pl, port numeric, security starttls|ssl|none, channel main|releases); "Regenerate SECRET_KEY" behind a yes/no warning; on save writes the file and `systemctl restart wishlist`; if the app fails to start (e.g. invalid FETCH_DENY_CIDRS → ConfigError) restore the previous file and show the error from `journalctl -u wishlist -n 20`.
- Install prompt: "Configure Wish List now?" (yes/no); with no TTY or `WISHLIST_UNATTENDED=1`, use defaults. Admin link captured from bootstrap output (`Admin link: …`) and saved to `/root/wishlist.creds`; shown at the end of install.
- shellcheck clean (`shellcheck -x`, severity warning) for all scripts.

## Review Focus

1. **Settings values with special characters** (password with `'`, `$`, spaces, `#`) → stored and read back exactly; no shell execution. Test in Task 1.
2. **Failed update** (bad commit that crashes on start, GitHub unreachable, rate-limited API) → app keeps running the previous version; clear message. Test in Task 2.
3. **Re-running install/`update` when already current, or twice in a row** → idempotent, no duplicate users/units/backups beyond retention. Test in Task 2.
4. **Invalid setting entered** (bad CIDR, bad URL) → rejected or rolled back; service still up. Test in Task 1.
5. **Non-interactive run** (no TTY, `WISHLIST_UNATTENDED=1`) → completes with defaults, no hang on a prompt. Test in Task 1.

---

### Task 1: Container side — library, install script, settings command, test harness

**Files:**
- Create: `proxmox/misc/wishlist-lib.sh`, `proxmox/install/wishlist-install.sh`, `proxmox/misc/wishlist-settings`, `proxmox/misc/wishlist.service`, `proxmox/tests/run-container-tests.sh`, `proxmox/tests/stub-functions.sh`

**Interfaces (bash functions in wishlist-lib.sh):**
- `wl_install_ref <ref> <target_dir>` — download `https://github.com/$WISHLIST_REPO/archive/<ref>.tar.gz` into target, create venv, `pip install .`, compile translations; prints nothing on success, non-zero on failure.
- `wl_settings_get <KEY>` / `wl_settings_set <KEY> <VALUE>` — safe read/write of `/etc/wishlist/wishlist.env` (path overridable via `WISHLIST_ENV` for tests).
- `wl_validate <KEY> <VALUE>` — returns 0/1 with a message on stderr.
- `wl_backup` → prints backup dir; `wl_restore <backup_dir>`; `wl_prune_backups` (keep 3).
- `wl_health [timeout_s]` — polls `/healthz`.
- `wl_latest_ref <channel>` — prints sha or tag (Task 2 uses it).

- [ ] **Step 1:** Write `proxmox/tests/run-container-tests.sh`: starts `debian:13` via docker (use a docker-container buildx-free approach: `docker run` with `--security-opt apparmor=unconfined` if needed on this host), mounts the repo read-only, installs a stub `systemctl` (runs the unit's ExecStartPre/ExecStart in background, stop kills it) and `stub-functions.sh` (msg_info/msg_ok/msg_error/$STD etc. no-ops/echo), sets `WISHLIST_SOURCE_DIR=/repo` so `wl_install_ref` can install from the mounted repo instead of GitHub (`ref=local`), and runs assertions:
  - unattended install completes; `/healthz` returns `ok`; `/root/wishlist.creds` contains `Admin link: http://`; settings file perms 640 root:wishlist; app runs as `wishlist`.
  - `wl_settings_set SMTP_PASSWORD "p'a\$s #x"` then `wl_settings_get` returns it byte-for-byte; systemd-style parse yields the same value.
  - `wishlist-settings --set FETCH_DENY_CIDRS garbage` (non-interactive flag for tests) → rejected by validation; forcing an app-level failure restores the previous file and the service is up.
  - interactive path driven with scripted answers (`WISHLIST_ANSWERS` file or here-doc into the read fallback) sets BASE_URL/admin/language.
- [ ] **Step 2:** Run it → FAIL (scripts missing).
- [ ] **Step 3:** Implement the lib, install script (follows community-scripts install-script shape: `source /dev/stdin <<<"$FUNCTIONS_FILE_PATH"`, `color`, `verb_ip6`, `catch_errors`, `setting_up_container`, `network_check`, `update_os`, then app steps, `motd_ssh`, `customize`, `cleanup_lxc`), the settings command (whiptail menu + `--get/--set` flags), the unit (User=wishlist, EnvironmentFile, ExecStartPre bootstrap, ExecStart gunicorn `-c python:wishlist.gunicorn_conf "wishlist:create_app()"`, Restart=on-failure).
- [ ] **Step 4:** Run harness + `shellcheck -x` (via `docker run --rm -v "$PWD:/mnt" koalaman/shellcheck:stable` if not installed) → pass.
- [ ] **Step 5: Commit** `feat(proxmox): container install, settings command and test harness`.

---

### Task 2: Host side — ct script with update routine

**Files:** Create `proxmox/ct/wishlist.sh`, `proxmox/tests/test-update.sh` (extends the harness); Modify `proxmox/install/wishlist-install.sh` only if `/usr/bin/update` needs rewriting.

**Interfaces:** Consumes Task 1 lib functions. Produces `update_script()` per Global Constraints.

- [ ] **Step 1:** Tests in the harness: (a) "already up to date" when `wl_latest_ref` (stubbed) equals the version file → exit 0, no backup; (b) upgrade to a new local ref (a copy of the repo with a trivial change) → version file updated, healthz ok, one backup created; (c) upgrade to a broken ref (app raises on import) → previous version restored, healthz ok, non-zero exit; (d) GitHub unreachable (stub returns error) → clear message, nothing changed; (e) four upgrades → only 3 backups kept.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement `ct/wishlist.sh` in the exact upstream shape (see `ct/actualbudget.sh` pattern: `_cs_boot` line honouring `COMMUNITY_SCRIPTS_CORE_DIR/URL`, APP/var_* defaults, `header_info`, `variables`, `color`, `catch_errors`, `update_script`, `start`, `build_container`, `description`, final messages showing `http://${IP}:8000` and "Admin link: run `cat /root/wishlist.creds` in the container"). `update_script` sources the container lib and calls it; keep logic in the lib so the harness can test it without the engine.
- [ ] **Step 4:** Run harness + shellcheck → pass. Also run `bash -n` and a dry check that `ct/wishlist.sh` resolves the pinned engine URL (curl HEAD on `${COMMUNITY_SCRIPTS_CORE_URL}/core/build.func` → 200).
- [ ] **Step 5: Commit** `feat(proxmox): ct script and update routine`.

---

### Task 3: Docs and CI

**Files:** Modify `README.md` (new "Proxmox (community-script style)" section after "Install for real": the one-line command, what it asks, `wishlist-settings`, `update`, channels, where data/settings/creds live, backups, how to bump the engine pin, note "not part of the official community-scripts collection"), `.github/workflows/ci.yml` (shellcheck job on `proxmox/**`), `CLAUDE.md` (short Proxmox section).

- [ ] **Step 1:** Write docs; CI job uses `ludeeus/action-shellcheck` or `apt-get install shellcheck`.
- [ ] **Step 2:** Run shellcheck locally the same way CI will; full pytest stays green.
- [ ] **Step 3: Commit** `docs(proxmox): README section and shellcheck CI`.
