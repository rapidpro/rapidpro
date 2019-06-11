---
layout: docs
title: Mailroom
permalink: /docs/mailroom/
---

# Mailroom

[Mailroom](https://github.com/nyaruka/mailroom) is a high performance golang
application which wraps the core RapidPro flow engine, [GoFlow](https://github.com/nyaruka/goflow).
It takes care of handling messages received by Courier and executing flows, generating
any new messages or events as needed.

You will need a running Mailroom instance in order for any flows to be executed.
See the [Mailroom README](https://github.com/nyaruka/mailroom/blob/master/README.md)
for notes on running and configuring Mailroom for your install.

# HTTP Redirects

Although most of the work done by Mailroom is done in the background, Mailroom
also exposes some HTTP endpoints that are needed to run RapidPro.

You will need to redirect all URLs that begin with `/mr/` to the the running
Mailroom instance for your installation.

We provide some sample stanzas for use in nginx to accomplish this, but note that every
configuration is different and you should validate these against your config.

You will first need to define the upstream Mailroom server as below:

```
upstream mailroom_server {
  server 127.0.0.1:8090 fail_timeout=60;
}
```

An example nginx rewrite rule to include in your server directive to forward all
URLs with `/mr/` in them might be:

```
# all Mailroom URLs go to Mailroom
location ~ /mr/ {
  proxy_set_header Host $http_host;
  proxy_pass http://mailroom_server;
  break;
}
```
