FROM python:3.9-slim as build-poetry

WORKDIR /app

COPY ./pyproject.toml .
COPY ./poetry.lock .
COPY ./docker/pip-requires.txt .

RUN python -m pip install -U poetry \
 && poetry cache clear -n --all pypi \
 && poetry add -n --lock $(cat pip-requires.txt) \
 && poetry export --without-hashes --output requirements.txt

FROM python:3.9-slim

ARG COMPRESS_ENABLED
ARG BRANDING_ENABLED
 
ARG RAPIDPRO_APPS_GIT_URL
ARG RAPIDPRO_APPS_GIT_BRANCH
ARG FLOWEDITOR_VERSION
ARG FLOWEDITOR_REPO
ARG FLOWEDITOR_BRANCH


ENV PYTHONUNBUFFERED 1
ENV DEBIAN_FRONTEND  noninteractive

ENV PROJECT           rapidpro
ENV PROJECT_PATH      /app
ENV PROJECT_USER      $PROJECT
ENV PROJECT_GROUP     $PROJECT_USER
ENV PROJECT_CONF      ${PROJECT_PATH}-conf
ENV NPM_CONFIG_PREFIX /opt/npm-globals
ENV PATH              "${NPM_CONFIG_PREFIX}/bin:${PATH}"
ENV FLOWEDITOR_TGZ "nyaruka-flow-editor-${FLOWEDITOR_VERSION}.tgz"

RUN apt-get update \
 && apt-get install --no-install-recommends --no-install-suggests -y apt-utils \
 && apt-get install --no-install-recommends --no-install-suggests -y gcc bzip2 git curl nginx libpq-dev gettext \
    libgdal-dev python3-cffi python3-gdal vim

RUN curl -sL https://deb.nodesource.com/setup_12.x | bash - \
 && apt-get install --no-install-recommends --no-install-suggests -y nodejs

RUN rm -rf /var/lib/apt/lists/*

RUN useradd -Ums /bin/bash $PROJECT_USER \
 && usermod -aG adm $PROJECT_USER

RUN mkdir $PROJECT_PATH \
 && mkdir $PROJECT_CONF \
 && mkdir $NPM_CONFIG_PREFIX \
 && chown -R $PROJECT_USER:$PROJECT_GROUP $PROJECT_PATH \
 && chown -R $PROJECT_USER:$PROJECT_GROUP $NPM_CONFIG_PREFIX

RUN rm /etc/nginx/sites-enabled/default

COPY --from=build-poetry /app/requirements.txt /tmp/dep/
COPY ./docker/pip-freeze.txt /tmp/dep/
COPY ./docker/nginx.site.conf /tmp/
RUN pip install --no-cache-dir -r /tmp/dep/requirements.txt

RUN cp /tmp/nginx.site.conf /etc/nginx/sites-available/$PROJECT.conf \
 && ln -s /etc/nginx/sites-available/$PROJECT.conf /etc/nginx/sites-enabled/$PROJECT.conf

COPY . $PROJECT_PATH
RUN chown -R $PROJECT_USER:$PROJECT_GROUP $PROJECT_PATH

WORKDIR $PROJECT_PATH

RUN su $PROJECT_USER -c "npm install --global \
  coffeescript \
  less \
  yarn"

RUN su $PROJECT_USER -c "npm install"

RUN su $PROJECT_USER -c "git clone --branch ${FLOWEDITOR_BRANCH} ${FLOWEDITOR_REPO}"

RUN su $PROJECT_USER -c "cd floweditor && yarn install && yarn run build && npm pack && cd .. && npm install ./floweditor/${FLOWEDITOR_TGZ}"

RUN su $PROJECT_USER -c "npm prune --production"

RUN su $PROJECT_USER -c "ln -s $PROJECT_PATH/temba/settings.py.prod $PROJECT_PATH/temba/settings.py"

EXPOSE 8000
EXPOSE 8001
ENTRYPOINT ["sh", "./docker/start"]
