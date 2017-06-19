#!/usr/bin/env bash
ldconfig
#################  Celery configuration  #################
nohup celery  --app=temba  worker --loglevel=INFO --queues=$CELERY_QUEUE --concurrency=$CELERY_WORKERS>/cel.out 2>&1&
celery -A temba flower --port=5555 
#################       End Celery       #################
