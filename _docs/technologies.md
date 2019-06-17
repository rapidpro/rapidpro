---
layout: docs
title: Technologies
permalink: /docs/technologies/
---

# Technologies

We lean on some fantastic frameworks and technologies to make RapidPro work,
here's a brief overview of the largest ones we use and how. Although knowledge
of every last technology is not required to contribute to RapidPro, a cursory
understanding of each will help you understand the system as a whole.

# Frontend

## Python / Django

The entire front end of RapidPro is built on [Django](https://www.djangoproject.com/).
 We try to keep up with the latest releases within six months of them coming out.

## Smartmin

We make heavy use of Nyaruka's [Smartmin](https://github.com/nyaruka/smartmin)
library in order to simplify the wiring of views, permissions and other
Django niceties. You will need to familiarize yourself with this framework to
understand most UI portions of the codebase.

## HAML

In order to simplify our markup and make sure it is always valid, our templates
make heavy use of [Hamlpy](https://github.com/jessemiller/HamlPy) instead of
writing raw HTML templates. We've found this to greatly increase the quality
and clarify of our templates.

## CoffeeScript (though moving to Typescript)

In a similar vein, we use [CoffeeScript](http://coffeescript.org/) for most of
our front end Javascript as it leads to more readable code that is compatible
across all browsers.

## LESS

For writing stylesheets, we use [LESS](http://lesscss.org/) as a preprocessor.
This lets us easily use variables for colors and fonts and leads to more readable selectors.

## React

Our new editor has been rewritten from AngularJS to the more modern
[React Framework](https://reactjs.org/). This has provided us a big increase
in the performance of the editor as well as allowing us to add new features
with more confidence of not breaking existing functionality.

# Backend

## PostgreSQL

We depend on [PostgreSQL](http://www.postgresql.org/) as a SQL server.
We do not support any other RDBMS. This is primarily due to the PostGIS support
provided by PostgreSQL.

## ElasticSearch

In order to enable performant searching across millions of contacts, we index
them in [ElasticSearch](https://www.elastic.co/).

## Celery

For backgrounds tasks and batch jobs we lean on a [Celery](http://www.celeryproject.org/) 
distributed task queue.

## Redis

We are big, big, fans of [Redis](http://redis.io/) as a fast, reliable and lightweight
store. We use it as a backend for Celery processes, as a caching framework and as
a locking mechanism for our distributed systems.

## Golang

For the highest performance pieces of RapidPro we use [Golang](https://golang.org/),
which we have found to be a perfect fit for us.

# Android

The Android channel for RapidPro which allows syncing of messages to and from
your handset obviously uses the [Android](https://android.com/) framework. As this component is meant to
be as lightweight as possible we do not lean on any significant Android Java
libraries to implement the client.
