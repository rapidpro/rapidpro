FROM mvilchis/rapidpro-base:v1.0
MAINTAINER Miguel Vilchis "mvilchis@ciencias.unam.mx"


#################      Enviroment variables     #################
ENV C_FORCE_ROOT True
ENV UWSGI_WSGI_FILE=temba/wsgi.py
ENV UWSGI_HTTP=:8000
ENV UWSGI_MASTER=1
ENV UWSGI_WORKERS=8
ENV UWSGI_HARAKIRI=20
ENV LANG C.UTF-8
ENV TZ=America/Los_Angeles
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone


#################         Install packages      #################
RUN apt-get update && \
    apt-get install -y software-properties-common && \
    add-apt-repository ppa:jonathonf/python-3.6
RUN add-apt-repository ppa:deadsnakes/ppa
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      bash \
      patch \
      git \
      gcc \
      g++ \
      make \
      curl \
      libc-dev \
      musl-dev \
      libpng-dev \
      libxslt-dev \
      libxml2-dev \
      libffi-dev \
      ncurses-dev \
      nodejs \
      npm \
      nodejs-legacy \
      python3.6 \
      idle3 \
      python3.6-dev \
      build-essential \
      vim \
      wget \
      libpq-dev \
      file \
      lib32ncurses5-dev \
      libgeos-dev && \
      rm -rf /var/lib/apt/lists/* && \
      npm install -g less && \
      npm install -g coffee-script && \
      npm install -g bower

RUN curl https://bootstrap.pypa.io/get-pip.py | python3.6

#################         Work directory        #################
RUN mkdir rapidpro
WORKDIR /rapidpro
COPY pip-freeze.txt /rapidpro
COPY pip-requires.txt /rapidpro


#################   Install requirements        #################
RUN  pip3 install --no-cache-dir --upgrade pip
#RUN pip install --upgrade pip==9.0.3 && \
RUN python3.6 -m pip install --upgrade setuptools
RUN python3.6 -m   pip install -r pip-freeze.txt


#################   Remove pyhthon3.5 link      #################
RUN rm /usr/bin/python3
RUN ln -s /usr/bin/python3.6 /usr/bin/python3


#################          Add files            #################
ADD . /rapidpro
RUN cp temba/settings.py.dev temba/settings.py


#################       Set configuration       #################
RUN sed -i 's/sitestatic\///' /rapidpro/static/brands/rapidpro/less/style.less
RUN sed -i '/                - for obj in object_list/c\                - for obj in object_list\n \                  - if obj.id >= 0' templates/smartmin/list.haml


EXPOSE 8000
EXPOSE 5555

CMD ["./start.sh"]
