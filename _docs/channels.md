---
layout: docs
title: Channels
permalink: /docs/channels/
---

# Channels Module

The Channels module contains the logic required to send and receive messages,
both to the Android devices and outside aggregators.

## Channel

A Channel represents a way of sending or receiving messages with RapidPro. An
organization can have more than one Channel, each with it's own type and
configuration. Channels are in charge of defining how messages are delivered and
received, for example, they implement the proper format to deliver a message
to Twilio or Nexmo as well as other aggregators.

## ChannelLog

The ChannelLog simply provides a way of logging outgoing messages so that
providers can debug why messages may be having trouble being sent. As keeping
a full record of every request and response to Channels would be prohibitive, only
the most recent events are stored at any point in time.

## SyncEvent

The SyncEvent model stores the most recent statistics around Android Channels
syncing with the RapidPro service. This includes the battery level of the device,
it's network connectivity and number of pending messages.

## Alert

The Alert model is used to keep track of e-mail alerts sent to users when Channels
experience errors sending messages.
