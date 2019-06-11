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

 * [Python](https://www.python.org/) 3.6.5 or later
 * [PostgreSQL](https://www.postgresql.org/) 9.6 or later along with the PostGIS extensions
 * [Redis](https://redis.io) 3.2 or later installed and listening on localhost
 * [NPM](https://www.npmjs.com/) which handles our JS dependencies

## Create temba user for PostgreSQL

Create a temba user with password temba:
{% highlight bash %}
$ createuser temba --pwprompt -d
Enter password for new role: (enter temba)
Enter it again: (enter temba)
{% endhighlight %}

## Create temba database, add PostGIS

Create the database making temba the owner:
{% highlight bash %}
$ createdb temba
{% endhighlight %}

Now connect as a superuser that can install extensions and install postgis, hstore and uuid extensions:
{% highlight bash %}
$ psql
postgres=# \c temba
You are now connected to database "temba" as user "psql".
temba=# create extension postgis;
CREATE EXTENSION
temba=# create extension postgis_topology;
CREATE EXTENSION
temba=# create extension hstore;
CREATE EXTENSION
temba=# create extension "uuid-ossp";
CREATE EXTENSION
{% endhighlight %}

## Clone RapidPro

Now clone the RapidPro repository and link up the development settings:

{% highlight bash %}
$ git clone git@github.com:rapidpro/rapidpro.git
$ cd rapidpro/temba
$ ln -s temba/settings.py.dev settings.py
$ cd ..
{% endhighlight %}

## Build virtual environment

You should always use a virtual environment to run your RapidPro installation. The
pinned dependencies for RapidPro can be found in ```pip-freeze.txt```. You can
build the needed environment as follows (from the root rapidpro directory):

{% highlight bash %}
$ virtualenv -p python3 env
$ source env/bin/activate
(env) $ pip install -r pip-freeze.txt
{% endhighlight %}

## Sync your database

You should now be able to run all the migrations and initialize your development
server. This takes a little while on RapidPro as migrate also creates and
initializes all the user groups and permissions.

{% highlight bash %}
$ python manage.py migrate
{% endhighlight %}

## Install javascript dependencies

Before you can run your server, you will need the Javascript dependencies. You
can install them using NPM:

{% highlight bash %}
$ npm install
{% endhighlight %}

## Install lessc and coffeescript

Because our templates and CSS files need compilation, you'll need to use NPM
to install `coffeescript` and `lessc` globally:

{% highlight bash %}
$ sudo npm install less -g
$ sudo npm install coffeescript -g
{% endhighlight %}

## Start Django server

At this point you'll be able to run the development server and run RapidPro. It
will be available at ```http://localhost:8000```

{% highlight bash %}
$ python manage.py runserver
{% endhighlight %}

## Start Mailroom

If you wish to edit and run flows in your development environment, you will also
need to run mailroom locally.

You can do so by just downloading the latest mailroom version from the Mailroom
[releases](https://github.com/nyaruka/mailroom/releases) and running the
executable. The default options should work without any changes for your development
server. (you will see warnings about S3 buckets but these can be ignored for
  development)

{% highlight bash %}
$ ./mailroom
{% endhighlight %}


# Testing with the RapidPro SMS Channel Android app

## Configure to connect to your development server

There is a hidden feature of the [RapidPro SMS Channel Android app](https://github.com/rapidpro/android-channel) for testing your
RapidPro development instance on a local network.

If you tap the rapidpro logo in the app 11 times you can unlock the advanced settings,
which will let you enter any an IP address. The app will attempt to connect to RapidPro
using the given IP address on port 8000 so you can claim the relayer and test
sending/receiving with real SMS messages. If you need to use a different port, you can
append it to the IP address like: ```192.168.1.15:80```.

Android only allows a single app to send a certain number of messages per hour.
However, you can increase your message throughput by installing "SMS Channel Pack" apps,
which effectively raise the allowed number of messages for the RapidPro SMS Channel Android app.
On the RapidPro SMS Channel app's page in the Play Store, click on 'More by Nyaruka Ltd.' and
install up to 9 of the SMS Channel Packs to increase your message volume.
