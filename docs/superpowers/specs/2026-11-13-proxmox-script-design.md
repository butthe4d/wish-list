# Proxmox Community-Style Install Script — Design

Date: 2026-11-13
Status: approved (user: "we're good, let's start coding")

## Intent

Install and run Wish List on Proxmox VE the same way as the community-scripts
apps: one command in the Proxmox shell creates an LXC container with the app
running, and `update` inside the container upgrades it after new pushes to
GitHub. The scripts live only in this repo (not submitted upstream).

## Goals

- `bash -c "$(curl -fsSL https://raw.githubusercontent.com/butthe4d/wish-list/main/proxmox/ct/wishlist.sh)"`
  on the Proxmox host shows the standard community-scripts menus (default /
  advanced: CPU, RAM, disk, network…) and creates a Debian 13 LXC.
- During installation the script asks **"Configure Wish List now?"**:
  - Yes → asks BASE_URL (default `http://<container-IP>:8000`), admin name,
    admin email, default language (en/pl), then optionally email (SMTP host,
    port, security, user, password, from) and SMS (gsmservice token, sender),
    and FETCH_DENY_CIDRS.
  - No → sensible defaults (BASE_URL = container IP, admin "Admin" /
    `admin@example.com`, language en, no email/SMS).
  - Either way: generates SECRET_KEY, starts the app, and shows the **admin
    link** at the end (also saved to `/root/wishlist.creds`, mode 600).
- **`wishlist-settings`** command inside the container: a whiptail menu showing
  every setting with its current value (all of the above plus the update
  channel); edits are saved to the settings file and the service is restarted.
  Nothing is sealed after install. SECRET_KEY can be regenerated only behind an
  explicit warning (logs everyone out, breaks share links).
- **`update`** inside the container (community-scripts convention) checks
  GitHub for a newer version on the chosen channel — default `main` (latest
  commit), optional `releases` (latest tag/release) — and if newer: backs up the
  database and pictures, installs the new code, runs migrations, restarts, and
  rolls back to the previous code + backup if the new version fails to start.

## Non-goals

- Submitting to community-scripts/ProxmoxVE; VM install; Docker inside the LXC.
- HTTPS/reverse proxy setup (README "Exposing the app" still applies).

## Approaches

One sensible way: use the official community-scripts engine
(`community-scripts/core` `build.func`), which now supports third-party script
repos via `COMMUNITY_SCRIPTS_URL`; our repo provides `proxmox/ct/wishlist.sh` and
`proxmox/install/wishlist-install.sh` in the same shape as official scripts, so
menus, container creation, `update` and the MOTD behave identically.
Rejected: writing our own `pct create` script (different UX, much more code);
vendoring a copy of the engine (stale fast, large); Docker inside LXC (against
community-scripts norms, extra layer).

## How (decided)

- Native install in the LXC: Debian 13, apt `python3 python3-venv git sqlite3
  curl whiptail`; system user `wishlist`; code in `/opt/wishlist` (git checkout
  of the chosen ref), venv in `/opt/wishlist/.venv`, translations compiled;
  data in `/var/lib/wishlist` (`DATABASE_URL=sqlite:////var/lib/wishlist/wishlist.db`,
  `DATA_DIR=/var/lib/wishlist`); settings in `/etc/wishlist/wishlist.env`
  (root:wishlist 640); systemd unit `wishlist.service` running bootstrap
  (migrations) then gunicorn with `wishlist/gunicorn_conf.py` on port 8000;
  installed version recorded in `/opt/wishlist_version.txt`.
- Engine pinning: the ct script defaults `COMMUNITY_SCRIPTS_CORE_URL` to a
  specific known-good commit of community-scripts/core (documented how to bump)
  so upstream engine changes can't break installs unexpectedly; overridable by
  env like upstream.
- Prompts use the same style as community scripts (whiptail, with plain `read`
  fallback when no TTY).
- README gets a "Proxmox (community-script style)" section; repo layout
  `proxmox/ct/`, `proxmox/install/`, `proxmox/misc/` (settings script, unit).

## Testing constraints

No Proxmox host is available to the builder: verify with `shellcheck`, a
harness that runs the install script inside a plain `debian:13` Docker
container with stubbed engine functions and `systemctl`, checking that the app
starts and answers `/healthz`, that `wishlist-settings` edits persist, and that
the update routine upgrades and rolls back. The final real-Proxmox run is the
user's.

## Left to the builder

Exact prompt wording (English), whiptail layouts, backup retention (keep last
3), how the update compares versions, container defaults (1 vCPU, 512 MB RAM,
3 GB disk, unprivileged; reduced from 2 vCPU / 1 GB / 4 GB on 2026-11-14 after measuring ~260 MB RSS).
