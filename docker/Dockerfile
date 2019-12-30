FROM python:3.6-buster

ARG COMPRESS_ENABLED
ARG BRANDING_ENABLED

ENV settings $SETTINGS_FILE
ENV PYTHONUNBUFFERED 1
WORKDIR /app

RUN apt-get update -y && \
    apt-get upgrade -y

RUN apt-get install \
  gcc python3-dev musl-dev \
  postgresql-client postgresql libxml2-dev libxslt-dev gettext \
  libjpeg-dev zlib1g-dev libfreetype6-dev liblcms2-2 libopenjp2-7-dev libtiff-dev  tk-dev tcl-dev \
  libffi-dev python-cffi gettext nodejs npm git build-essential libgdal20 libgdal-dev \
  libgeos-dev libgeos-c1v5 libgeos-dev libproj-dev binutils libproj-dev gdal-bin gdal-data python-gdal -y

RUN useradd -ms /bin/bash django \
    && adduser -a django django

RUN npm install --global --unsafe-perm \
  coffeescript \
  less \
  yarn

COPY ./package.json /app
RUN npm install

COPY ./pip-freeze.txt /app
RUN pip install --no-cache-dir -r pip-freeze.txt

COPY . .
RUN ln -s /app/temba/$settings /app/temba/settings.py

COPY ./docker/start /start
RUN sed -i 's/\r$//g' /start
RUN chmod +x /start
RUN chown django /start

RUN chown -R django /app
USER django

EXPOSE 8000