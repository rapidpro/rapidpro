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

# Web Services

## Django Web Service

At its heart, RapidPro is a Django application, so most of the logic for editing
and running the pieces of the system are handled by the Django application.

For the time being, this includes all API calls, incoming messages
and all their associated callbacks. (sent, delivered, errors, etc..) In time
Message Mage will take over these tasks as it can perform them more efficiently
and allow for schema changes without any loss in handling messages.

## Courier

[Courier](/rapidpro/docs/courier) is the service responsible for sending
and receiving messages on RapidPro. It is a golang application that exposes endpoints
for aggregators to hit when delivering messages as well as background processes which
take care of sending messages to said services.

Refer to the courier README for more information on configuration Courier for use with
RapidPro. See the [Courier](/rapidpro/docs/courier) page for more information on running Courier with RapidPro.

## RP-Indexer

[RP-Indexer](https://github.com/nyaruka/rp-indexer) is a simple golang service which indexes
contacts to enable contact seaches in RapidPro. It requires an ElasticSearch instance
to index against it. See the repo for more information on running this service against your
RapidPro instance.

## RP-Archiver

[RP-Archiver](https://github.com/nyaruka/rp-archiver) is a simple golang service which
creates flat file archives of messages and runs that are older than 90 days and optionally
deletes these records from the database. See the repo for more information on running this
service against your RapidPro instance.

# Celery Queues

We use four different celery queues in order to both scale processes independently
of jobs and provide different response levels per task type.

## Default Queue

The default celery queue is used for general background tasks. This includes long
running tasks such as exports of flow results or contacts, which can be time consuming,
as well as various cron tasks.

Timed events such as campaign triggers are also run in this queue, so it is important
that there are enough workers available at any time to keep these responsive.

## Flow Queue

This queue is used when creating a large number of flow runs. When a user starts
a flow with many thousands of recipients, the job of creating the flow runs and
starting the first steps is broken into chunks of 500 contacts each. Those batches
are then added to the flow queue for processing.

## Msgs Queue

This queue is responsible for sending messages out. Each outgoing messages has a
task associated with it. A lot of effort is put into the sending tasks in this
queue to execute without touching the database. (it will be hit precisely once
to record whether the message was successfully sent)

## Handler Queue

This queue is responsible for handling incoming messages, in most cases this
results in running a particular flow. Messages are handled strictly in a first
come, first served basis (across organizations).

<div class="note">
<h2>Fair Queuing</h2>

<p>A challenge inherit in a system like RapidPro that serves many organizations at
once is to make sure that one organization's use does not impede upon others. By
default first-in, first-out queues can cause a very busy organization to impede
on the performance of others.</p>

<p>This is easier to understand by illustration. A large organization may have
100,000 contacts it wishes to send a message to. Each message must be delivered
to its channel independently, so that means at least 100,000 calls to an aggregator
or operator to send those messages. Though we can use many celery workers to
accomplish this task more quickly, celery still processes tasks on a first-come,
first-served basis, so if another organization sends a message immediately after
the fist large organization sends its own, it would have to wait for all 100,000
messages to be sent before its single message.</p>

<p>To work around this problem, the flow and msgs queues use a system of fair
queueing using Redis priority queues. Each organization has a sorted set containing
the messages it has to send, these are ordered by message priority, bulk vs individual.
Additionally, another Redis set maintains which organizations have any messages
to send.</p>

<p>When an organization has a message to send, it first adds that message to
its sorted set of outgoing messages, then schedules a <code>send_msg_task</code> for
Celery to work on. Critically, that task does not reference a single message,
instead when it runs, it randomly picks one of the outgoing queues and pops off
the highest priority message to send. This system lets us use all available
resources for a single organization while still balancing and serving smaller
organizationas as needed.</p>

<p>For more details, check out <code>temba/utils/queues.py</code></p>
</div>

# Android Channel

In circumstances where no aggregator or operator is available to integrate with,
RapidPro also provides an [Android Channel](https://github.com/rapidpro/android-channel).
This Android app listens for incoming messages on the handset and then delivers
those message from RapidPro using an HTTP API.

RapidPro in turn communicates new outgoing messages to the Android device by
notifying it to sync with it using Google Cloud Message (GCM). This lets the
server 'push' a ping to the client to call home. Note that for maximum
simplicity this message does not contain the contents of the message to send, but
rather acts as a trigger for the client to sync using the normal HTTP API.

To make sure regular syncing continues even if GCM messages are not coming through,
the client also regularly polls the server (with varying frequencies) for new
messages.
