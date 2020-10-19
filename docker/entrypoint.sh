#!/bin/bash
set -e

case $1 in
    supervisor)
        python3.6 manage.py compress --extension=.haml --force
        python3.6 docker/clear-compressor-cache.py
        python3.6 manage.py migrate --noinput
        envsubst '${NGINX_COURIER_URL} ${NGINX_MAILROOM_URL}' < docker/nginx.conf > /etc/nginx/sites-enabled/nginx.conf
        sed -i "/worker_connections\s/c\    worker_connections 1024;" /etc/nginx/nginx.conf
        sed -i "/worker_processes\s/c\worker_processes 16;" /etc/nginx/nginx.conf
        /usr/local/bin/supervisord -n -c docker/supervisor-app.conf
    ;;

esac

exec "$@"