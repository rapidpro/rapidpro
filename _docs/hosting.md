---
layout: docs
title: Hosting
permalink: /docs/hosting/
---

# Hosting

Due to the complexity of properly hosting RapidPro, it is not recommended to try
to host RapidPro yourself for anything but the largest of installations. Instead, use
one of the hosted service providers that have expertise in doing so.

For a highly available installation, you will require:

 * n+1 load balancers routing internet traffic to the Django and Message Mage components
 * n+1 web server front ends running the Django frontend
 * n+1 web servers front ends running the Message Mage frontend
 * A PostgreSQL server with a hot standby
 * A Redis server with a hot standby
 * n+1 Celery instances for each of the RapidPro queues. (celery, msgs, flows, handler)

<img src="{{site.baseurl}}/images/hosting.png" widht="100%">

<div class="note">
<p>Note that hosting your own version of RapidPro is not an easy affair, the codebase
changes daily and you'll want to stay up to date with the latest
changes in order to have the latest features and bug fixes.</p>

<p>Again, unless you are doing a large deployment of your own and have experience
running large software deployments, we do not recommend running RapidPro
yourself.</p>
</div>

## Server Guidelines

Though the hardware required to run RapidPro at scale changes based on various
optimizations made in the code, here are some rough guidelines for running a cluster
capable of handling millions of messages per week.

 * Web and Celery Servers - 3 servers - 4 Xeon CPUs, 16 gigs of RAM
 * Redis Servers - 2 servers - 2 Xeon CPUs, 8 gigs of RAM
 * DB Servers - 8 Xeon CPUs, 30 gigs of RAM

The configuration of gunicorn and celery workers is highly dependent on the kind
of hardware you have, but given the above, these should get you started:

 * Django - 10 gunicorn workers
 * Default Celery Queue - 1-8 dynamic workers
 * Handler Celery Queue - 1-8 dynamic workers
 * Flow Celery Queue - 1-6 dynamic workers
 * Msgs Celery Queue - 1-12 dynamic workers
