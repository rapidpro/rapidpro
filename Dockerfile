FROM greatnonprofits/ccl-base:v2.0

RUN wget https://s3.amazonaws.com/rds-downloads/rds-combined-ca-bundle.pem \
    -O /usr/local/share/ca-certificates/rds.crt
RUN update-ca-certificates

RUN mkdir /rapidpro
WORKDIR /rapidpro

RUN virtualenv -p /usr/bin/python3.6 env
RUN . env/bin/activate

ADD pip-freeze.txt /rapidpro/pip-freeze.txt
RUN pip install --upgrade pip
RUN pip install -r pip-freeze.txt --upgrade

ADD . /rapidpro
COPY docker.settings /rapidpro/temba/settings.py

RUN cd /rapidpro && npm install && bower install --allow-root

RUN python manage.py collectstatic --noinput
RUN python manage.py compress --extension=.haml --force

RUN echo "daemon off;" >> /etc/nginx/nginx.conf

RUN rm -f /etc/nginx/sites-enabled/default
RUN ln -sf /rapidpro/nginx.conf /etc/nginx/sites-enabled/

RUN rm -f /rapidpro/temba/settings.pyc

COPY entrypoint.sh /

RUN rm -rf /tmp/* /var/tmp/*[~]$

EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]

CMD ["supervisor"]
