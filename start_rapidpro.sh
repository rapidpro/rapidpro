#!/usr/bin/env bash
ldconfig
npm install --only=dev
bower install --allow-root
python3 manage.py collectstatic --noinput --no-post-process
python3 manage.py compress --extension=".haml" --force -v0
#python manage.py migrate --fake-initial --noinput
/usr/local/bin/uwsgi --http-auto-chunked --http-keepalive --single-interpreter --enable-threads
#python manage.py runserver 0.0.0.0:8000
#./index_stuff/rp-indexer -db postgresql://temba@$POSTGRES_PORT_5432_TCP_ADDR:5432/temba?sslmode=disable -rebuild -elastic-url http://$ELASTICSEARCH_PORT_9200_TCP_ADDR:9200
#./index_stuff/rp-indexer -db postgresql://rapidpro:rapidpro@$POSTGRES_PORT_5432_TCP_ADDR:5432/temba?sslmode=disable -rebuild -elastic-url http://$ELASTICSEARCH_PORT_9200_TCP_ADDR:9200
