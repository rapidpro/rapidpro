#!/usr/bin/env bash
python manage.py collectstatic --noinput
python manage.py compress --extension=.html,.haml
python manage.py syncdb
nohup ./manage.py celery worker -B -E --loglevel=INFO --concurrency=10 >/cel.out 2>&1&
python manage.py runserver 0.0.0.0:8000
