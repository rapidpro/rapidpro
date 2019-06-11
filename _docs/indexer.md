---
layout: docs
title: Indexer
permalink: /docs/indexer/
---

# Indexer

[RP-Indexer](https://github.com/nyaruka/rp-indexer) is a simple Golang application
that takes care of creating and keeping your ElasticSearch indexes up to date with
changes for the contacts in RapidPro. It is meant to run continuously in the background,
it will query for changed contacts and update the indexes appropriately.

Refer to the [README](https://github.com/nyaruka/rp-indexer/blob/master/README.md) for the options available
when running the indexer.
