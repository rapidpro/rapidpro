 apk add --no-cache nodejs-lts nodejs-npm openssl tar \
  && npm install -g coffee-script less bower
apk add --no-cache --virtual .build-deps \
                bash \
                patch \
                git \
                gcc \
                g++ \
                make \
                libc-dev \
                musl-dev \
                linux-headers \
                postgresql-dev \
                libjpeg-turbo-dev \
                libpng-dev \
                freetype-dev \
                libxslt-dev \
                libxml2-dev \
                zlib-dev \
                libffi-dev \
                pcre-dev \
                readline \
                readline-dev \
                ncurses \
                ncurses-dev \
                libzmq \
                && pip install -U virtualenv \
                && virtualenv /venv
python manage.py migrate
python manage.py runserver 0.0.0.0:80000