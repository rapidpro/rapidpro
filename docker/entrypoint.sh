#!/bin/bash
set -e

case $1 in
    supervisor)
        python3.6 manage.py compress --extension=.haml --force
        python3.6 docker/clear-compressor-cache.py
        python3.6 manage.py migrate --noinput
        /usr/bin/supervisord -n -c docker/supervisor-app.conf
    ;;
        
esac

exec "$@"