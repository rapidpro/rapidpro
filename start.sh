#!/usr/bin/env bash
ldconfig
#python manage.py collectstatic --noinput
python manage.py collectstatic --noinput --no-post-process
#python manage.py compress --extension=.html,.haml
python manage.py compress --extension=".haml" --force -v0
#python manage.py syncdb
python manage.py migrate --fake-initial
#nohup ./manage.py celery --beat --app=temba  worker --loglevel=INFO --queues=celery,msgs,flows,handler  >/cel.out 2>&1&
nohup celery --beat --app=temba  worker --loglevel=INFO --queues=celery,msgs,flows,handler >/cel.out 2>&1&
#nohup ./manage.py celery worker -B -E --loglevel=INFO --concurrency=10 >/cel.out 2>&1&
#uwsgi --http-auto-chunked --http-keepalive
python manage.py runserver 0.0.0.0:8000
