#!/usr/bin/env bash
ldconfig
bower install --allow-root
python manage.py collectstatic --noinput --no-post-process
python manage.py compress --extension=".haml" --force -v0
#python manage.py migrate --fake-initial --noinput
/usr/local/bin/uwsgi --http-auto-chunked --http-keepalive
#python manage.py runserver 0.0.0.0:8000
