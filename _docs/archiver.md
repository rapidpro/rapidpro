---
layout: docs
title: Archiver
permalink: /docs/archiver/
---

# Archiver

[RP-Archiver](https://github.com/nyaruka/rp-archiver) is a simple Golang application
that will create archives of messages and runs that are older than 90 days. Although
this is an optional component, we highly recommend running it with the `delete` option
set to true for installations that are dealing with large scale as otherwise
 the database will grow unbounded and query performance will suffer.

Refer to the [README](https://github.com/nyaruka/rp-archiver) for the options available
when running the archiver.
