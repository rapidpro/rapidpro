---
layout: docs
title: Development
permalink: /docs/development/
---

# RapidPro Development Server

RapidPro comes with everything you need to quickly get started with
development. Note that development and deployment has only been tested on OSX
and Ubuntu, you'll likely need to modify the directions below if using Windows.

## Prerequisites

You'll need the following to get started:

 * PostgreSQL 9.3 or later along with the PostGIS extensions. You probably want
   to refer to Django's [installation instructions](https://docs.djangoproject.com/en/dev/ref/contrib/gis/install/postgis/)
   to help get this working.
 * [Redis](https://redis.io) 2.8 or later installed and listening on localhost.
   By default the development server uses database 15.
 * [lessc](http://lesscss.org), the Less compiler.
 * [coffee](http://coffeescript.org), the Coffee script compiler.

## Create temba user for PostgreSQL

{% highlight bash %}
$ sudo apt-get install postgresql-client postgresql-server-dev-9.3 postgis*
$ sudo -u postgres createuser temba --pwprompt -d
Enter password for new role:
Enter it again:
$ sudo adduser temba
{% endhighlight %}

## Create temba database, add PostGIS

Create the database as temba user:
{% highlight bash %}
$ sudo -u temba psql --user=temba postgres
postgres=> create database temba;
CREATE DATABASE
\q
{% endhighlight %}

Now connect as a superuser that can install extensions:
{% highlight bash %}
$ sudo -u postgres psql
postgres=# \c temba
You are now connected to database "temba" as user "psql".
temba=# create extension postgis;
CREATE EXTENSION
temba=# create extension postgis_topology;
CREATE EXTENSION
temba=# create extension hstore;
CREATE EXTENSION
{% endhighlight %}

## Clone RapidPro

Now clone the RapidPro repository and link up the development settings:

{% highlight bash %}
$ git clone git@github.com:rapidpro/rapidpro.git
$ cd rapidpro/temba
$ ln -s settings.py.dev settings.py
{% endhighlight %}

##Install Node 
{% highlight bash %}
$ sudo apt-get install node npm coffee-script
$ sudo npm install -g less
{% endhighlight %}

## Build virtual environment

You should always use a virtual environment to run your RapidPro installation. The
pinned dependencies for RapidPro can be found in ```pip-freeze.txt```. You can
build the needed environment as follows (from the root rapidpro directory):

{% highlight bash %}
$ sudo apt-get install python-virtualenv postgresql-server-dev-9.3 python-dev ncurses-dev
$ virtualenv env
$ source env/bin/activate
(env) $ pip install -r pip-freeze.txt
{% endhighlight %}

## Sync your database

You should now be able to run all the migrations and initialize your development
server. This takes a little while on RapidPro as syncdb also creates and
initializes all the user groups and permissions.

{% highlight bash %}
$ python manage.py syncdb
{% endhighlight %}

## Run development server

At this point you'll be able to run the development server and run RapidPro. It
will be available at ```http://localhost:8000```

{% highlight bash %}
$ python manage.py runserver
{% endhighlight %}

See these [instructions](https://docs.djangoproject.com/en/1.7/ref/django-admin/#runserver-port-or-address-port) if you wish to change the port.

