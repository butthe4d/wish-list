#!/bin/sh
set -e
if [ "$(id -u)" = "0" ]; then
    # A fresh bind mount is created by Docker as root; hand it to the app user, then drop privileges.
    mkdir -p /data
    [ "$(stat -c %u /data)" = "$(id -u app)" ] || chown app:app /data
    export HOME=/home/app
    exec setpriv --reuid=app --regid=app --init-groups "$0" "$@"
fi
python -m wishlist.bootstrap
exec gunicorn -c python:wishlist.gunicorn_conf "wishlist:create_app()"
