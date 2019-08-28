FROM greatnonprofits/ccl-base:v2.0

RUN mkdir /rapidpro
WORKDIR /rapidpro

RUN virtualenv -p /usr/bin/python3.6 env
RUN . env/bin/activate

ADD pip-freeze.txt /rapidpro/pip-freeze.txt
RUN pip install --upgrade pip
RUN pip install -r pip-freeze.txt --upgrade

ADD . /rapidpro
COPY docker.settings /rapidpro/temba/settings.py

RUN cd /rapidpro && npm install npm@latest && npm install && bower install --allow-root
RUN python manage.py collectstatic --noinput
RUN python manage.py compress --extension=.haml --force

RUN touch `echo $RANDOM`.txt
