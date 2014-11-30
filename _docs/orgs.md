---
layout: docs
title: Orgs
permalink: /docs/orgs/
---

# Orgs Module

The orgs (short for organizations) module contains models related to the
organization (what can be thought of as workspace) for a set of particular
RapidPro users. Since RapidPro is a multi-tenant system, Organizations act as
a way of tying together a set of channels, contacts, messages etc..

## Org

The Org model captures basic attributes around an organization, including the
name of the organization, the languages it uses, locale and timezone as well
as what users are granted permissions to view, edit and administer the
organization.

## TopUp

TopUps represent the number of credits that have been added to an organization.
Since RapidPro has roots in being a SaaS platform, TopUps are used as a way of
monetizing use. The granting of TopUps via purchases is left up to service
providers to implement.
