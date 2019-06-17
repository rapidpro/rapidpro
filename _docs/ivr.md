---
layout: docs
title: IVR
permalink: /docs/ivr/
---

# IVR Module

The IVR module contains models associated with recording the path a contact
takes through an IVR call. Note that most of the logic used during IVR calls
is managed by Mailroom.

## IVRCall

The IVRCall model represents an incoming or outgoing IVR call. This is used
as a way of grouping the individual actions that took place on the call as
well as track the external id of the call as stored by the VOIP provider.
