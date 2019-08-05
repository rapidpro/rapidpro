FROM python:3.6-alpine

ARG RAPIDPRO_VERSION
ENV PIP_RETRIES=120 \
    PIP_TIMEOUT=400 \
    PIP_DEFAULT_TIMEOUT=400 \
    C_FORCE_ROOT=1 \
    PIP_EXTRA_INDEX_URL="https://alpine-3.wheelhouse.praekelt.org/simple"

COPY . /rapidpro
COPY pip-requires.txt /app/requirements.txt
WORKDIR /rapidpro
RUN set -ex \
        && apk add --no-cache --virtual .build-deps \
                --repository http://dl-cdn.alpinelinux.org/alpine/edge/testing \
                --repository http://dl-cdn.alpinelinux.org/alpine/edge/main \
                bash \
                patch \
                git \
		gcc \
                g++ \
                make \
                libc-dev \
                musl-dev \
                linux-headers \
                postgresql-dev \
                libjpeg-turbo-dev \
                libpng-dev \
                freetype-dev \
                libxslt-dev \
                libxml2-dev \
                zlib-dev \
                libffi-dev \
                pcre-dev \
                readline \
                readline-dev \
                ncurses \
                ncurses-dev \
                libzmq \
		gdal \
                gdal-dev \
                geos-dev \
		python3-dev \
		rsync

ARG RAPIDPRO_VERSION
ARG RAPIDPRO_REPO=POLLSTERPRO_REPO
ENV RAPIDPRO_VERSION=${RAPIDPRO_VERSION:-master}
ENV RAPIDPRO_REPO=${RAPIDPRO_REPO:-istresearch/rapidpro}
ENV GITHUB_USER=${GITHUB_USER}
ENV GITHUB_TOKEN=${GITHUB_TOKEN}

WORKDIR /rapidpro
RUN set -ex \
		&& apk add --no-cache --virtual .build-deps \
               	rsync \
		&& rsync -a /usr/lib/* /usr/local/lib \
                && pip install -U virtualenv \
		&& pip install -r /app/requirements.txt \
                && virtualenv /venv 

# Build Python virtualenv
COPY pip-requires.txt /app/requirements.txt
RUN LIBRARY_PATH=/lib:/usr/lib /bin/sh -c "/venv/bin/pip install setuptools==41.0.1" \
    && LIBRARY_PATH=/lib:/usr/lib /bin/sh -c "/venv/bin/pip install -r /app/requirements.txt" \
    && runDeps="$( \
      scanelf --needed --nobanner --recursive /venv \
              | awk '{ gsub(/,/, "\nso:", $2); print "so:" $2 }' \
              | sort -u \
              | xargs -r apk info --installed \
              | sort -u \
    )" \
    && apk --no-cache add --virtual .python-rundeps $runDeps \
    && apk del .build-deps && rm -rf /var/cache/apk/*

RUN set -ex \
		&& apk add --no-cache --virtual .build-deps \
                rsync \
                && rsync -a /usr/lib/* /usr/local/lib \
                && pip install -U virtualenv \
                && pip install -r /app/requirements.txt \
                && virtualenv /venv

RUN set -ex \
  && apk add --no-cache nodejs-lts nodejs-npm openssl tar \
  && npm install -g coffee-script less bower

# TODO should this be in startup.sh?
RUN  npm install npm@latest && npm install && bower install --allow-root

# Install `psql` command (needed for `manage.py dbshell` in stack/init_db.sql)
# Install `libmagic` (needed since rapidpro v3.0.64)
RUN apk add --no-cache postgresql-client libmagic

ENV UWSGI_VIRTUALENV=/venv UWSGI_WSGI_FILE=temba/wsgi.py UWSGI_HTTP=:8000 UWSGI_MASTER=1 UWSGI_WORKERS=8 UWSGI_HARAKIRI=20
# Enable HTTP 1.1 Keep Alive options for uWSGI (http-auto-chunked needed when ConditionalGetMiddleware not installed)
# These options don't appear to be configurable via environment variables, so pass them in here instead
ENV STARTUP_CMD="/venv/bin/uwsgi --http-auto-chunked --http-keepalive"
EXPOSE 8000
LABEL org.label-schema.name="RapidPro" \
      org.label-schema.description="RapidPro allows organizations to visually build scalable interactive messaging applications." \
      org.label-schema.url="https://www.rapidpro.io/" \
      org.label-schema.vcs-url="https://github.com/$RAPIDPRO_REPO" \
      org.label-schema.vendor="Nyaruka, UNICEF, and individual contributors." \
      org.label-schema.version=$RAPIDPRO_VERSION \
      org.label-schema.schema-version="1.0"
