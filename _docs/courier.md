---
layout: docs
title: Courier
permalink: /docs/courier/
---

# Courier

[Courier](https://github.com/nyaruka/courier) is a high performance golang application built to send and receive messages
on behalf of RapidPro. You will need a running courier instance in order for any 
messages to be sent or received. See the [Courier README](https://github.com/nyaruka/courier/blob/master/README.md) for notes on running and configuring Courier for your install.

# HTTP Redirects

Courier is responsible for receiving messages from aggregators and messaging services and
thus needs to be exposed to the web on the same domain as you are hosting RapidPro. You
will need to redirect all URLs that begin with `/c/` to the the running Courier instance.

We provide some sample stanzas for use in nginx to accomplish this, but note that every
configuration is different and you should validate these against your config.

You will first need to define the upstream courier server as below:

```
upstream courier_server {
  server 127.0.0.1:8080 fail_timeout=60;
}
```

An example nginx rewrite rule to include in your server directive to forward all 
URLs with `/c/` in them might be:

```
  # all courier URLs go to courier
  location ^~ /c/ {
    proxy_set_header Host $http_host;
    proxy_pass http://courier_server;
    break;
  }
```

If you are moving from legacy handling of messages by RapidPro, you can add the following nginx
redirects in order to properly convert old style `/handlers/` URLs to Courier:

```
  location /handlers/telegram/ {
    rewrite /handlers/telegram/(.*) /c/tg/$1/receive;
    proxy_set_header Host $http_host;
    proxy_pass http://courier_server;
    break;
  }

  location ~ /shaqodoon/received/ {
    rewrite /shaqodoon/received/(.*)/ /c/sq/$1/receive;
    proxy_set_header Host $http_host;
    proxy_pass http://courier_server;
    break;
  }

  location ~ /kannel/receive/ {
    rewrite /kannel/receive/(.*)/(.*) /c/kn/$1/receive$2;
    proxy_set_header Host $http_host;
    proxy_pass http://courier_server;
    break;
  }

  location ~ /kannel/status/ {
    rewrite /kannel/status/(.*)(.*) /c/kn/$1/status$2;
    proxy_set_header Host $http_host;
    proxy_pass http://courier_server;
    break;
  }

  location ~ /handlers/external/received {
    rewrite ^/(.*)/$ /$1;
    rewrite /handlers/external/received/(.*) /c/ex/$1/receive;
    proxy_set_header Host $http_host;
    proxy_pass http://courier_server;
    break;
  }

  location ~ /handlers/external/ {
    rewrite /handlers/external/(.*)/(.*)/(.*) /c/ex/$2/$1$3;
    proxy_set_header Host $http_host;
    proxy_pass http://courier_server;
    break;
  }

  location ~ /handlers/facebook/ {
    rewrite /handlers/facebook/(.*) /c/fb/$1/receive;
    proxy_set_header Host $http_host;
    proxy_pass http://courier_server;
    break;
  }

  location ~ /handlers/start/receive/ {
    rewrite /handlers/start/receive/(.*) /c/st/$1/receive;
    proxy_set_header Host $http_host;
    proxy_pass http://courier_server;
    break;
  }

  location ~ /handlers/dartmedia/receive {
    rewrite /handlers/dartmedia/receive(.?)/(.*) /c/da/$2/receive;
    proxy_set_header Host $http_host;
    proxy_pass http://courier_server;
    break;
  }

  location ~ /handlers/dartmedia/status {
    rewrite /handlers/dartmedia/status/(.*) /c/da/$1/status;
    proxy_set_header Host $http_host;
    proxy_pass http://courier_server;
    break;
  }


  location ~ /handlers/viber_public/ {
    rewrite /handlers/viber_public/(.*)?(.*) /c/vp/$1/receive?$2;
    proxy_set_header Host $http_host;
    proxy_pass http://courier_server;
    break;
  }

  location ~ /handlers/yo/ {
    rewrite /handlers/yo/received/(.*)?(.*) /c/yo/$1/receive?$2;
    proxy_set_header Host $http_host;
    proxy_pass http://courier_server;
    break;
  }

  location ~ /handlers/jasmin/ {
    rewrite /handlers/jasmin/(receive|status)/(.*) /c/js/$2/$1;
    proxy_set_header Host $http_host;
    proxy_pass http://courier_server;
    break;
  }

  location ~ /m3tech/received/ {
    rewrite /m3tech/received/(.*)?(.*) /c/m3/$1/receive$2;
    proxy_set_header Host $http_host;
    proxy_pass http://courier_server;
    break;
  }

  location ~ /handlers/jiochat/ {
    rewrite /handlers/jiochat/(.*) /c/jc/$1;
    proxy_set_header Host $http_host;
    proxy_pass http://courier_server;
    break;
  }


  location ~ /handlers/smscentral/ {
    rewrite /handlers/smscentral/receive/(.*) /c/sc/$1/receive;
    proxy_set_header Host $http_host;
    proxy_pass http://courier_server;
    break;
  }

  location ~ /handlers/globe/ {
    rewrite /handlers/globe/receive/(.*) /c/gl/$1/receive;
    proxy_set_header Host $http_host;
    proxy_pass http://courier_server;
    break;
  }

  location ~ /handlers/line/ {
    rewrite /handlers/line/(.*)/ /c/ln/$1/receive;
    proxy_set_header Host $http_host;
    proxy_pass http://courier_server;
    break;
  }

  location ~ /handlers/hcnx/receive {
    rewrite /handlers/hcnx/receive/(.*)?(.*) /c/hx/$1/receive?$2;
    proxy_set_header Host $http_host;
    proxy_pass http://courier_server;
    break;
  }

  location ~ /handlers/hcnx/status {
    rewrite /handlers/hcnx/status/(.*)?(.*) /c/hx/$1/status?$2;
    proxy_set_header Host $http_host;
    proxy_pass http://courier_server;
    break;
  }


  location ~ /handlers/chikka/ {
    rewrite /handlers/chikka/(.*) /c/ck/$1/receive;
    proxy_set_header Host $http_host;
    proxy_pass http://courier_server;
    break;
  }

  location ~ /handlers/fcm/ {
    rewrite /handlers/fcm/(.*)/(.*)/ /c/fcm/$2/$1;
    proxy_set_header Host $http_host;
    proxy_pass http://courier_server;
    break;
  }

  location ~ /handlers/macrokiosk/ {
    rewrite /handlers/macrokiosk/(.*)/(.*)?(.*) /c/mk/$2/$1?$3;
    proxy_set_header Host $http_host;
    proxy_pass http://courier_server;
    break;
  }
```
