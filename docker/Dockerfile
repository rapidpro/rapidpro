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
# && mkdir -p /var/www \
 && chown -R $PROJECT_USER:$PROJECT_GROUP $PROJECT_PATH \
 && chown -R $PROJECT_USER:$PROJECT_GROUP $NPM_CONFIG_PREFIX
# && chown -R $PROJECT_USER:$PROJECT_GROUP /var/www

RUN rm /etc/nginx/sites-enabled/default

COPY ./docker/pip-freeze.txt /tmp/dep/
COPY ./pip-freeze.txt ./docker/nginx.site.conf /tmp/
RUN pip install --no-cache-dir -r /tmp/dep/pip-freeze.txt

RUN cp /tmp/nginx.site.conf /etc/nginx/sites-available/$PROJECT.conf \
 && ln -s /etc/nginx/sites-available/$PROJECT.conf /etc/nginx/sites-enabled/$PROJECT.conf

#RUN envsubst < /tmp/nginx.conf > $PROJECT_CONF/nginx.conf \
# && envsubst < /tmp/nginx.site.conf > $PROJECT_CONF/$PROJECT.conf \
# && chown -R $PROJECT_USER:$PROJECT_GROUP $PROJECT_CONF

COPY . $PROJECT_PATH
RUN chown -R $PROJECT_USER:$PROJECT_GROUP $PROJECT_PATH

#COPY --chown=$PROJECT_USER:$PROJECT_GROUP ./docker/nginx.start.sh /etc/init.d/$PROJECT
#RUN chmod +x /etc/init.d/$PROJECT \
# && cat /etc/init.d/$PROJECT \
# && update-rc.d /etc/init.d/$PROJECT enable
#RUN update-rc.d /etc/init.d/nginx enable

WORKDIR $PROJECT_PATH
#USER $PROJECT_USER

RUN su $PROJECT_USER -c "npm install --global \
  coffeescript \
  less \
  yarn"

RUN su $PROJECT_USER -c "npm install \
 && npm prune --production"

RUN su $PROJECT_USER -c "ln -s $PROJECT_PATH/temba/settings.py.prod $PROJECT_PATH/temba/settings.py"

EXPOSE 8000
ENTRYPOINT ["sh", "./docker/start"]
