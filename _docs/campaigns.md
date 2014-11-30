---
layout: docs
title: Campaigns
permalink: /docs/campaigns/
---

# Campaigns Module

Campaigns represent both the definition and logging of personalized timed
actions with Contacts. Campaigns are often used for DRIP-like communications
such as sensitivity messaging or maternity reminders.

## Campaign

The Campaign model defines the name of the campaign, as well as the ContactGroup
it operates on. Otherwise it simply acts as a way of grouping the events within it.

## CampaignEvent

A CampaignEvent represents an action that is performed at a time relative to a
datetime on a Contact. As such, it must define the ContactField it is relative to,
the offset to that field, as well as the action to perform.

Just like Triggers, though the user is presented with two types of CampaignEvent
actions (flow or message), both are implemented as Flows internally.

## EventFire

The EventFire model captures both past and future schedules of individual
CampaignEvents on Contacts. The next firing time for CampaignEvents are
calculated on every change of a Contact and EventFire objects are created to
track these future events.

Every minute a celery task runs to fire any expired EventFire events. Once fired
EventFire objects are marked as complete.
