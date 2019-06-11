---
layout: docs
title: Flows
permalink: /docs/flows/
---

# Flows Module

The Flows module contains all logic pertaining to storing flows within RapidPro.
Note that all running of flows is now taken care of in Mailroom so this package
is mostly used for the saving of flow definitions, management of
different flow revisions and the setting up of triggers and schedules of flows.

## FlowSession

A FlowSession represents the entire session for a contact passing through a set
of flows before exiting the session either via expiration or because they were
interrupted somehow. (by say sending another keyword) FlowSessions are used
by Mailroom to track the state of a contact in a flow and are very verbose. They
are only kept in the database for a short period while the flow is active. More
permanent storage is done via the FlowRun model. Note that there may be more
than one FlowRun per FlowSession because of subflows.

## FlowRun

A FlowRun represents a contact's run through a flow. This model contains the results
that have been collected, the path the contact took through a flow as well
as a subset of the events that were created during that flow. Note that a FlowRun
is specific to a single flow, so a session may contain multiple FlowRuns.
