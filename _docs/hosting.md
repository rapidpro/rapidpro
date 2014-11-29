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

<img src="/images/hosting.png" widht="100%">
