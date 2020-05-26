FROM python:3.6-slim

ARG COMPRESS_ENABLED
ARG BRANDING_ENABLED

ENV PYTHONUNBUFFERED 1
ENV DEBIAN_FRONTEND  noninteractive

ENV PROJECT           rapidpro
ENV PROJECT_PATH      /app
ENV NPM_CONFIG_PREFIX /opt/npm-globals
ENV PATH              "${NPM_CONFIG_PREFIX}/bin:${PATH}"

RUN apt-get update \
 && apt-get -y install apt-utils curl nginx libpq-dev gettext \
 && apt-get -y install libgdal-dev python3-cffi python3-gdal
# libxml2-dev libxslt1-dev

RUN curl -sL https://deb.nodesource.com/setup_12.x | bash - \
 && apt-get install -y nodejs

RUN rm -rf /var/lib/apt/lists/*

RUN mkdir $PROJECT_PATH \
 && mkdir $NPM_CONFIG_PREFIX

COPY ./docker/nginx.conf /etc/nginx/sites-available/$PROJECT.conf
RUN ln -s /etc/nginx/sites-available/$PROJECT.conf /etc/nginx/sites-enabled/$PROJECT.conf

ADD . $PROJECT_PATH

RUN useradd -Ums /bin/bash $PROJECT \
 && chown -R $PROJECT:$PROJECT $PROJECT_PATH \
 && chown -R $PROJECT:$PROJECT $NPM_CONFIG_PREFIX

WORKDIR $PROJECT_PATH

RUN pip install --no-cache-dir -r docker/pip-freeze.txt

COPY ./docker/start /start
RUN sed -i 's/\r$//g' /start
RUN chmod +x /start
RUN chown $PROJECT /start

USER $PROJECT

RUN npm install --global \
  coffeescript \
  less \
  yarn

RUN npm install \
 && npm prune --production

RUN ln -s $PROJECT/temba/settings.py.prod $PROJECT/temba/settings.py

EXPOSE 8000
ENTRYPOINT ["/start"]
