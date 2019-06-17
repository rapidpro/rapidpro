---
layout: docs
title: Components
permalink: /docs/components/
---

# RapidPro Components

RapidPro consists of two main classes of components, web services and celery
worker queues. The web services provide externally visible endpoints to users
and APIs to edit flows, view results and inject messages, while the workers
take care of running both long running jobs and the millions of small tasks
required in running large scale deployments.

## Django

At its heart, RapidPro is a Django application, so most of the logic for creating
flows, managing contacts and other UI is managed through standard Django views.

## Celery

The default Django celery queue is used for general background tasks. This includes long
running tasks such as exports of flow results or contacts, which can be time consuming,
as well as various cron tasks.

## Courier

[Courier](/rapidpro/docs/courier) is the service responsible for sending
and receiving messages on RapidPro. It is a Golang application that exposes endpoints
for aggregators to hit when delivering messages as well as background processes which
take care of sending messages to said services.

Refer to the Courier README for more information on configuring Courier for use with
RapidPro.

## Mailroom

[Mailroom](/rapidpro/docs/mailroom) is the Golang service responsible for starting
and handling flows. It is also used for the sending of large message broadcasts and
additionally handles incoming IVR callbacks and Surveyor submissions.

Refer to the Mailroom README for more information on configuration Mailroom for use with
RapidPro.

## RP-Indexer

[RP-Indexer](https://github.com/nyaruka/rp-indexer) is a simple Golang service which indexes
contacts to enable contact seaches in RapidPro. It requires an ElasticSearch instance
to index against it. See the repo for more information on running this service against your
RapidPro instance.

## RP-Archiver

[RP-Archiver](https://github.com/nyaruka/rp-archiver) is a simple Golang service which
creates flat file archives of messages and runs that are older than 90 days and optionally
deletes these records from the database. See the repo for more information on running this
service against your RapidPro instance.

## Flow Editor

The [Flow Editor](https://github.com/nyaruka/floweditor) is a React
application that is used to author and edit flows within RapidPro. It is packaged
as a dependency for RapidPro so does not need any kind of manual installation.

## GoFlow

[GoFlow](https://github.com/nyaruka/goflow) is a standalone GoLang library that
is the engine used to validate, migrate and execute RapidPro flows. It is embedded
within Mailroom to do the heavy lifting of running flows.

## Android Channel

In circumstances where no aggregator or operator is available to integrate with,
RapidPro also provides an [Android Channel](https://github.com/rapidpro/android-channel).
This Android app listens for incoming messages on the handset and then delivers
those message from RapidPro using an HTTP API.

## Android Surveyor

RapidPro not only supports messaging based flows but also offline execution of
flows on handsets in the form of the [Surveyor](https://github.com/rapidpro/surveyor)
Android application. Agents can download flows when they have an internet connection, then
go to remote areas with no connectivity and run through those flows, later uploading
the results to RapidPro.
