#!/usr/bin/env bash
ldconfig
#################  Celery configuration  #################
nohup celery --beat --app=temba  worker --loglevel=INFO --queues=celery,msgs,flows,handler --concurrency=10 -Ofair>/cel.out 2>&1&
celery -A temba flower --port=5555 
#################       End Celery       #################
