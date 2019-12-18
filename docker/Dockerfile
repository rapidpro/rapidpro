FROM python:3.6

ARG COMPRESS_ENABLED
ARG BRANDING_ENABLED

ENV PYTHONUNBUFFERED 1
WORKDIR /app

RUN apt-get install \
  build-deps gcc python3-dev musl-dev \
  postgresql-client postgresql-dev libxml2-dev libxslt-dev gettext-dev \
  jpeg-dev zlib-dev freetype-dev lcms2-dev openjpeg-dev tiff-dev tk-dev tcl-dev \
  libffi-dev py-cffi gettext nodejs npm git build-essential libgdal20 libgdal-dev \
  libgeos-3.6.2 libgeos-c1v5 libgeos-dev libproj-dev binutils libproj-dev gdal-bin gdal-data python-gdal -y

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
