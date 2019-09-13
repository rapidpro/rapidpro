FROM greatnonprofits/ccl-base:v2.0

RUN wget https://s3.amazonaws.com/rds-downloads/rds-combined-ca-bundle.pem \
    -O /usr/local/share/ca-certificates/rds.crt
RUN update-ca-certificates

RUN wget https://bitbucket.org/ariya/phantomjs/downloads/phantomjs-2.1.1-linux-x86_64.tar.bz2
RUN tar xvjf phantomjs-2.1.1-linux-x86_64.tar.bz2 -C /usr/local/share/
RUN ln -sf /usr/local/share/phantomjs-2.1.1-linux-x86_64/bin/phantomjs /usr/local/bin

RUN mkdir /rapidpro
WORKDIR /rapidpro

COPY pip-freeze.txt /rapidpro/pip-freeze.txt
RUN pip3 install --upgrade pip setuptools
RUN pip3 install -r pip-freeze.txt

COPY . /rapidpro
COPY docker/docker.settings /rapidpro/temba/settings.py

RUN npm install && bower install --allow-root

RUN python3.6 manage.py collectstatic --noinput

RUN echo "daemon off;" >> /etc/nginx/nginx.conf

RUN rm -f /etc/nginx/sites-enabled/default

RUN rm -f /rapidpro/temba/settings.pyc

COPY docker/entrypoint.sh /
RUN chmod +x /entrypoint.sh

RUN rm -rf /tmp/* /var/tmp/*[~]$

EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]

CMD ["supervisor"]
