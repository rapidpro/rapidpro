#!/usr/bin/env bash
ldconfig
#################  Celery configuration  #################
if [ -z "$CELERY_BEAT" ]; then
  echo "Celery worker"
  celery  --app=temba  worker --loglevel=INFO --queues=$CELERY_QUEUE --concurrency=$CELERY_WORKERS
else
  nohup celery  --beat --app=temba  worker --loglevel=INFO --queues=celery --concurrency=$CELERY_WORKERS>/cel.out 2>&1&
  echo "Celery beat"
  celery -A temba flower --port=5555 
fi
#################       End Celery       #################
