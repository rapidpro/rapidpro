FROM ubuntu:latest

RUN apt-get update && \
  apt-get install -y --no-install-recommends \
  nodejs \
  npm \
  nodejs-legacy \
  python \
  python-setuptools \
  python-pip \
  python-dev \
  build-essential \
  git \
  libpq-dev \
  lib32ncurses5-dev \
  libgeos-dev && \
  rm -rf /var/lib/apt/lists/* && \
  npm install -g less && \
  npm install -g coffee-script

RUN mkdir rapidpro

ADD . rapidpro/

WORKDIR rapidpro

RUN cp temba/settings.py.dev temba/settings.py && \
  pip install --upgrade pip && \
  pip install --upgrade setuptools && \
  pip install -r pip-freeze.txt


EXPOSE 8000

CMD ["./start.sh"]
