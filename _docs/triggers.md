---
layout: docs
title: Triggers
permalink: /docs/triggers/
---

# Triggers Module

The Triggers module contains all logic pertaining to starting or scheduling
flows within RapidPro.

## Trigger

A Trigger represents an intent by a user to perform an action given a set of
conditions. From a user's perspective that action can either be sending a message
or starting a flow, but internally these are the same things. (simple one message
flows are created for the message cases)

Triggers are activated given a particular condition, one of:

 * Keyword - An incoming message starts with a particular word
 * Schedule - A particular point in time has been reached (optionally with repetition)
 * Missed Call - An incoming call was missed
 * Catchall - A incoming message was not handled by another trigger or flow
 * Follow - A Twitter user followed the Twitter Channel
