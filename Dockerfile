FROM ubuntu:latest

COPY ./geolibs.sh /
RUN apt-get update && \
  apt-get install -y --no-install-recommends \
  bash \
  patch \
  git \
  gcc \
  g++ \
  make \
  libc-dev \
  musl-dev \
  libpng-dev \
  libxslt-dev \
  libxml2-dev \
  libffi-dev \
  nodejs \
  npm \
  nodejs-legacy \
  python \
  python-setuptools \
  python-pip \
  python-dev \
  build-essential \
  vim \
  wget \
  libpq-dev \
  lib32ncurses5-dev \
  libgeos-dev && \
  sh /geolibs.sh \
  rm -rf /var/lib/apt/lists/* && \
  npm install -g less && \
  npm install -g coffee-script


ENV C_FORCE_ROOT True
ENV UWSGI_WSGI_FILE=temba/wsgi.py UWSGI_HTTP=:8000 UWSGI_MASTER=1 UWSGI_WORKERS=8 UWSGI_HARAKIRI=20
RUN mkdir rapidpro

ADD . rapidpro/

WORKDIR rapidpro

RUN cp temba/settings.py.dev temba/settings.py && \
  pip install --upgrade pip && \
  pip install --upgrade setuptools && \
  pip install -r pip-freeze.txt


EXPOSE 8000

CMD ["./start.sh"]
