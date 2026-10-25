#!/bin/sh
set -e
python -m wishlist.bootstrap
exec gunicorn -c python:wishlist.gunicorn_conf "wishlist:create_app()"
