
ARG APP_NAME=rapidpro
ARG WORKDIR=/usr/src/rapidpro
ARG REQUIREMENTS=${APP_NAME}/pip-freeze.txt

FROM python:3.6.5  
ARG APP_NAME
ARG WORKDIR
ARG REQUIREMENTS

WORKDIR ${WORKDIR}


COPY ./${APP_NAME} ./${APP_NAME}
COPY ./manage.py ./
COPY ./start.sh ./

RUN chmod +x ./start.sh
RUN pip install -r ${REQUIREMENTS}
RUN npm install && npm install less -g && npm install coffeescript -g

EXPOSE 8000

ARG RAPIDPRO_VERSION
ENV PIP_RETRIES=120 \
    PIP_TIMEOUT=400 \
    PIP_DEFAULT_TIMEOUT=400 \
    C_FORCE_ROOT=1 \
    PIP_EXTRA_INDEX_URL="https://alpine-3.wheelhouse.praekelt.org/simple"

# # TODO determine if a more recent version of Node is needed
# # TODO extract openssl and tar to their own upgrade/install line
# RUN set -ex \
#   && apk add --no-cache nodejs-lts nodejs-npm openssl tar \
#   && npm install -g coffee-script less bower

# # WORKDIR /rapidpro

# ARG RAPIDPRO_VERSION
# ARG RAPIDPRO_REPO
# ENV RAPIDPRO_VERSION=${RAPIDPRO_VERSION:-master}
# ENV RAPIDPRO_REPO=${RAPIDPRO_REPO:-rapidpro/rapidpro}
# RUN echo "Downloading RapidPro ${RAPIDPRO_VERSION} from https://github.com/$RAPIDPRO_REPO/archive/${RAPIDPRO_VERSION}.tar.gz" && \
#     wget -O rapidpro.tar.gz "https://github.com/$RAPIDPRO_REPO/archive/${RAPIDPRO_VERSION}.tar.gz" && \
#     tar -xf rapidpro.tar.gz --strip-components=1 && \
#     rm rapidpro.tar.gz

# # Build Python virtualenv

# ENV VIRTUAL_ENV=/opt/venv
# RUN python3 -m virtualenv --python=/usr/bin/python3 $VIRTUAL_ENV
# ENV PATH="$VIRTUAL_ENV/bin:$PATH"
# WORKDIR /root/rapidpro
# # Install dependencies:
# # COPY pip-freeze.txt /root/rapidpro
# RUN pip install -r pip-freeze.txt

# Run the application:
# COPY manage.py .
# CMD python manage.py runserver

# COPY pip-freeze.txt /root/rapidpro/pip-freeze.txt
# RUN LIBRARY_PATH=/lib:/usr/lib /bin/sh -c "/venv/bin/pip install setuptools==33.1.1" \
#     && LIBRARY_PATH=/lib:/usr/lib /bin/sh -c "/venv/bin/pip install -r /root/rapidpro/pip-freeze.txt" \
#     && runDeps="$( \
#       scanelf --needed --nobanner --recursive /venv \
#               | awk '{ gsub(/,/, "\nso:", $2); print "so:" $2 }' \
#               | sort -u \
#               | xargs -r apk info --installed \
#               | sort -u \
#     )" \
#     && apk --no-cache add --virtual .python-rundeps $runDeps \
#     && apk del .build-deps && rm -rf /var/cache/apk/*

# # TODO should this be in startup.sh?
# # RUN cd /rapidpro && npm install npm@latest &&


RUN apk add --no-cache postgresql-client libmagic

# ENV UWSGI_VIRTUALENV=/venv UWSGI_WSGI_FILE=temba/wsgi.py UWSGI_HTTP=:8000 UWSGI_MASTER=1 UWSGI_WORKERS=8 UWSGI_HARAKIRI=20
# # Enable HTTP 1.1 Keep Alive options for uWSGI (http-auto-chunked needed when ConditionalGetMiddleware not installed)
# # These options don't appear to be configurable via environment variables, so pass them in here instead
# ENV STARTUP_CMD="/venv/bin/uwsgi --http-auto-chunked --http-keepalive"
# ENV CELERY_CMD="/venv/bin/celery --beat --app=temba worker --loglevel=INFO --queues=celery,msgs,flows,handler"
# # COPY settings.py /rapidpro/temba/
# # 500.html needed to keep the missing template from causing an exception during error handling
# # COPY stack/500.html /rapidpro/templates/
# # COPY stack/init_db.sql /rapidpro/
# # COPY stack/clear-compressor-cache.py /rapidpro/
# # COPY Procfile /rapidpro/
# # COPY Procfile /
# EXPOSE 8000
# # COPY stack/startup.sh /

# LABEL org.label-schema.name="RapidPro" \
#       org.label-schema.description="RapidPro allows organizations to visually build scalable interactive messaging applications." \
#       org.label-schema.url="https://www.rapidpro.io/" \
#       org.label-schema.vcs-url="https://github.com/$RAPIDPRO_REPO" \
#       org.label-schema.vendor="Nyaruka, UNICEF, and individual contributors." \
#       org.label-schema.version=$RAPIDPRO_VERSION \
#       org.label-schema.schema-version="1.0"

# CMD python manage.py migrate

# CMD python manage.py runserver


