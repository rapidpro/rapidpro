#!/usr/bin/env bash
ldconfig
npm install --only=dev
bower install --allow-root
python3 manage.py collectstatic --noinput --no-post-process
python3 manage.py compress --extension=".haml" --force -v0
#python manage.py migrate --fake-initial --noinput
/usr/local/bin/uwsgi --http-auto-chunked --http-keepalive
#python manage.py runserver 0.0.0.0:8000
