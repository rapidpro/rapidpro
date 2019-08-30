#!/bin/bash
set -e

case $1 in
    supervisor)
        python3.6 manage.py compress --extension=.haml --force
        /usr/bin/supervisord -n -c docker/supervisor-app.conf
    ;;
    celery)
        /usr/bin/supervisord -n -c docker/supervisor-celery.conf
    ;;
        
esac

exec "$@"