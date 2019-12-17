FROM python:3.6-alpine

ARG COMPRESS_ENABLED
ARG BRANDING_ENABLED

ENV PYTHONUNBUFFERED 1
WORKDIR /app

RUN apk update \
  && apk add --virtual build-deps gcc python3-dev musl-dev \
  && apk add postgresql-client postgresql-dev libxml2-dev libxslt-dev gettext-dev \
  && apk add jpeg-dev zlib-dev freetype-dev lcms2-dev openjpeg-dev tiff-dev tk-dev tcl-dev \
  && apk add libffi-dev py-cffi gettext nodejs npm git

LABEL org.label-schema.name = "gdal-python-alpine"
LABEL org.label-schema.description = "Alpine-based image with Python and GDAL/OGR, compiled with selected additional drivers."
LABEL org.label-schema.vcs-url = "https://github.com/petr-k/gdal-python-alpine"
LABEL org.label-schema.vendor = "Petr Krebs"

ARG GDAL_VERSION=v2.2.4
ARG LIBKML_VERSION=1.3.0

RUN \
  apk update && \
  apk add --virtual build-dependencies \
    # to reach GitHub's https
    openssl ca-certificates \
    build-base cmake musl-dev linux-headers \
    # for libkml compilation
    zlib-dev minizip-dev expat-dev uriparser-dev boost-dev && \
  apk add \
    # libkml runtime
    zlib minizip expat uriparser boost && \
  update-ca-certificates && \
  mkdir /build && cd /build && \
  apk --update add tar && \
  # libkml
  wget -O libkml.tar.gz "https://github.com/libkml/libkml/archive/${LIBKML_VERSION}.tar.gz" && \
  tar --extract --file libkml.tar.gz && \
  cd libkml-${LIBKML_VERSION} && mkdir build && cd build && cmake .. && make && make install && cd ../.. && \
  # gdal
  wget -O gdal.tar.gz "https://github.com/OSGeo/gdal/archive/${GDAL_VERSION}.tar.gz" && \
  tar --extract --file gdal.tar.gz --strip-components 1 && \
  cd gdal && \
  ./configure --prefix=/usr \
    --with-libkml \
    --without-bsb \
    --without-dwgdirect \
    --without-ecw \
    --without-fme \
    --without-gnm \
    --without-grass \
    --without-grib \
    --without-hdf4 \
    --without-hdf5 \
    --without-idb \
    --without-ingress \
    --without-jasper \
    --without-mrf \
    --without-mrsid \
    --without-netcdf \
    --without-pcdisk \
    --without-pcraster \
    --without-webp \
  && make && make install && \
  # gdal python bindings
  pip install gdal --no-cache-dir && \
  # cleanup
  apk del build-dependencies && \
  cd / && \
  rm -rf build && \
  rm -rf /var/cache/apk/* && \
  rm -rf /usr/lib/python2.7

RUN addgroup -S django \
  && adduser -S -G django django

RUN npm install --global --unsafe-perm \
  coffeescript \
  less \
  yarn

COPY ./package.json /app
RUN npm install

COPY ./pip-freeze.txt /app
RUN pip install --no-cache-dir -r pip-freeze.txt

COPY . .
RUN ln -s /app/temba/settings.py.prod /app/temba/settings.py

COPY ./docker/start /start
RUN sed -i 's/\r$//g' /start
RUN chmod +x /start
RUN chown django /start

RUN chown -R django /app
USER django

EXPOSE 8000
ENTRYPOINT ["/start"]
