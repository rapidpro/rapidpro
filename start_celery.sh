#!/usr/bin/env bash
ldconfig
#################  Celery configuration  #################
if [ -z "$CELERY_BEAT" ]; then
  echo "Celery worker"
  celery  --app=temba  worker --loglevel=INFO --queues=$CELERY_QUEUE --concurrency=$CELERY_WORKERS --logfile=/cel.out
else
  nohup celery  --beat --app=temba  worker --loglevel=INFO --queues=celery --concurrency=$CELERY_WORKERS --logfile=/cel.out  &
  echo "Celery beat"
  celery -A temba flower --port=5555 --url_prefix=celery
fi
#################       End Celery       #################
