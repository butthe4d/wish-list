#!/usr/bin/env bash
# Test harness for the container side of the Proxmox installer.
#
#   proxmox/tests/run-container-tests.sh
#
# 1. Starts a plain debian:13 container with this repo mounted read-only at
#    /repo and runs container-tests.sh in it: the real install script with the
#    community-scripts engine functions and systemctl stubbed, installing the
#    app from /repo (WISHLIST_SOURCE_DIR) instead of GitHub. Then, in the same
#    container, test-update.sh exercises the update routine on that install
#    (and ct/wishlist.sh's update path through the real, pinned engine).
# 2. Starts a second debian:13 container with real systemd as PID 1 and runs
#    systemd-tests.sh: systemd's own EnvironmentFile parser must read values
#    written by wl_settings_set byte-for-byte, the unit must verify, and
#    quick settings changes must survive the unit's start limit.
#    Needs --privileged; set WL_SKIP_SYSTEMD=1 to skip this part.
#
# Needs docker and network access (apt, PyPI). Containers are removed on exit.
# WL_DOCKER_RUN_OPTS overrides the extra `docker run` options (default:
# --security-opt apparmor=unconfined, needed on hosts whose AppArmor profile
# blocks the default one). WL_KEEP=1 keeps the containers for debugging.
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
image="${WL_TEST_IMAGE:-debian:13}"
read -r -a run_opts <<<"${WL_DOCKER_RUN_OPTS:---security-opt apparmor=unconfined}"
ct="wl-ct-test-$$"
sd="wl-sd-test-$$"

cleanup() {
  if [[ "${WL_KEEP:-0}" == "1" ]]; then
    echo "Kept containers: $ct $sd"
  else
    docker rm -f "$ct" "$sd" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

status=0

echo "### Install test in $image"
docker run -d --name "$ct" "${run_opts[@]}" -v "$repo:/repo:ro" "$image" sleep infinity >/dev/null
docker exec "$ct" bash -c 'apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl ca-certificates >/dev/null'
docker exec "$ct" bash /repo/proxmox/tests/container-tests.sh || status=1
echo "### Update tests in $image (on the installed app, several minutes)"
docker exec "$ct" bash /repo/proxmox/tests/test-update.sh || status=1

if [[ "${WL_SKIP_SYSTEMD:-0}" == "1" ]]; then
  echo "### SKIPPED: real systemd checks (WL_SKIP_SYSTEMD=1)"
else
  echo "### systemd checks in $image"
  docker run -d --name "$sd" "${run_opts[@]}" --privileged --cgroupns=host \
    -v /sys/fs/cgroup:/sys/fs/cgroup:rw --tmpfs /run --tmpfs /run/lock \
    -v "$repo:/repo:ro" "$image" \
    bash -c 'apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq systemd python3 curl ca-certificates >/dev/null && exec /lib/systemd/systemd' >/dev/null
  booted=0
  for _ in $(seq 1 60); do
    state="$(docker exec "$sd" systemctl is-system-running 2>/dev/null || true)"
    if [[ "$state" == running || "$state" == degraded ]]; then
      booted=1
      break
    fi
    sleep 2
  done
  if [[ $booted == 1 ]]; then
    docker exec "$sd" bash /repo/proxmox/tests/systemd-tests.sh || status=1
  else
    echo "FAIL: systemd did not boot in the test container"
    status=1
  fi
fi

if [[ $status == 0 ]]; then echo "ALL CONTAINER TESTS PASSED"; else echo "CONTAINER TESTS FAILED"; fi
exit "$status"
