#!/bin/bash
set -e

case $1 in
    supervisor)
        /usr/bin/supervisord -n -c supervisor-app.conf
    ;;
    celery)
        /usr/bin/supervisord -n -c supervisor-celery.conf
    ;;
        
esac

exec "$@"