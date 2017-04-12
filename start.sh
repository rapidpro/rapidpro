#!/usr/bin/env bash
ldconfig
bower install --allow-root
python manage.py collectstatic --noinput --no-post-process
python manage.py compress --extension=".haml" --force -v0
python manage.py migrate --fake-initial
#################  Celery configuration  #################
nohup celery --beat --app=temba  worker --loglevel=INFO --queues=celery,msgs,flows,handler --concurrency=10 >/cel.out 2>&1&
celery -A temba flower --port=5555 --url_prefix=celery &
#################       End Celery       #################
/usr/local/bin/uwsgi --http-auto-chunked --http-keepalive
