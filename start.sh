#!/usr/bin/env bash
python manage.py collectstatic --noinput
python manage.py compress --extension=.html,.haml
python manage.py syncdb
python manage.py runserver 0.0.0.0:8000
