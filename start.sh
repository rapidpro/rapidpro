#!/usr/bin/env bash
ldconfig
#POSTGRES_ENV_TEMBAPASSWD=$POSTGRES_ENV_TEMBAPASSWD POSTGRES_PORT_5432_TCP_ADDR=$POSTGRES_PORT_5432_TCP_ADDR POSTGRES_PORT_5432_TCP_PORT=$POSTGRES_PORT_5432_TCP_PORT EMAIL_HOST_USER=$EMAIL_HOST_USER EMAIL_HOST_PASSWORD=$EMAIL_HOST_PASSWORD DEFAULT_LANGUAGE=$DEFAULT_LANGUAGE SECRET_KEY=$SECRET_KEY REDIS_PORT_6379_TCP_ADDR=$REDIS_PORT_6379_TCP_ADDR REDIS_PORT_6379_TCP_PORT=$REDIS_PORT_6379_TCP_PORT SEND_MESSAGES=$SEND_MESSAGES SEND_WEBHOOKS=$SEND_WEBHOOKS SEND_MAIL=$SEND_MAIL POSTGRES_PORT_5432_TCP_PORT=$POSTGRES_PORT_5432_TCP_PORT supervisord -c /etc/supervisor/conf.d/temba_work.conf
#service supervisor start
#supervisorctl reread
#supervisorctl update
#supervisorctl start tembacelery
python manage.py collectstatic --noinput --no-post-process
python manage.py compress --extension=".haml" --force -v0
python manage.py migrate --fake-initial
nohup celery --beat --app=temba  worker --loglevel=INFO --queues=celery,msgs,flows,handler --concurrency=10 >/cel.out 2>&1&
celery -A temba flower --port=5555 --url_prefix=celery &
#nohup celery --beat --app=temba  worker --loglevel=INFO --queues=celery,msgs,flows,handler >/cel.out 2>&1&
#nohup ./manage.py celery worker -B -E --loglevel=INFO --concurrency=10 >/cel.out 2>&1&
python manage.py runserver 0.0.0.0:8000
