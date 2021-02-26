FROM python:3.6-slim as build-apps

ARG RAPIDPRO_APPS_GIT_URL
ARG RAPIDPRO_APPS_GIT_BRANCH
ARG PROTOBUFFERS_URL

RUN apt-get update \
 && apt-get install --no-install-recommends --no-install-suggests -y apt-utils \
 && apt-get install --no-install-recommends --no-install-suggests -y git

# get protobuffers
WORKDIR /protos
RUN git clone ${PROTOBUFFERS_URL} . \
 && cd rapidpro-org 

WORKDIR /src

# clone rapidpro-apps
RUN git clone -- ${RAPIDPRO_APPS_GIT_URL} . \
 && git checkout ${RAPIDPRO_APPS_GIT_BRANCH}

RUN cp /protos/rapidpro-org/org.proto weni/org_grpc/grpc_gen/ 
RUN pip install grpcio grpcio-tools
RUN python -m grpc_tools.protoc \
 --proto_path=./ \
 --python_out=./ \
 --grpc_python_out=./ \
 ./weni/org_grpc/grpc_gen/org.proto \
 --experimental_allow_proto3_optional

RUN ls -l weni/org_grpc/grpc_gen/

RUN python setup.py sdist


FROM python:3.6-slim

ARG COMPRESS_ENABLED
ARG BRANDING_ENABLED

ENV PYTHONUNBUFFERED 1
ENV DEBIAN_FRONTEND  noninteractive

ENV PROJECT           rapidpro
ENV PROJECT_PATH      /app
ENV PROJECT_USER      $PROJECT
ENV PROJECT_GROUP     $PROJECT_USER
ENV PROJECT_CONF      ${PROJECT_PATH}-conf
ENV NPM_CONFIG_PREFIX /opt/npm-globals
ENV PATH              "${NPM_CONFIG_PREFIX}/bin:${PATH}"

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

COPY ./docker/pip-freeze.txt /tmp/dep/
COPY ./pip-freeze.txt ./docker/nginx.site.conf /tmp/
COPY --from=build-apps /src/dist .
RUN pip install --no-cache-dir -U pip==20.2.2 setuptools==49.6.0 && \
    pip install --no-cache-dir -r /tmp/dep/pip-freeze.txt && \
    pip install --no-cache-dir rapidpro-apps-0.1.tar.gz

RUN cp /tmp/nginx.site.conf /etc/nginx/sites-available/$PROJECT.conf \
 && ln -s /etc/nginx/sites-available/$PROJECT.conf /etc/nginx/sites-enabled/$PROJECT.conf

COPY . $PROJECT_PATH
RUN chown -R $PROJECT_USER:$PROJECT_GROUP $PROJECT_PATH

WORKDIR $PROJECT_PATH

RUN su $PROJECT_USER -c "npm install --global \
  coffeescript \
  less \
  yarn"

RUN su $PROJECT_USER -c "npm install \
 && npm prune --production"

RUN su $PROJECT_USER -c "ln -s $PROJECT_PATH/temba/settings.py.prod $PROJECT_PATH/temba/settings.py"

EXPOSE 8000
EXPOSE 8001
ENTRYPOINT ["sh", "./docker/start"]
