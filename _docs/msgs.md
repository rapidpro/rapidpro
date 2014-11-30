---
layout: docs
title: Msgs
permalink: /docs/msgs/
---

# Msgs Module

The Msgs Module contains all models associated with storing individual incoming
and outgoing messages with users.

## Broadcast

A Broadcast represents an outgoing message to a set of Contacts, either created
by a user or encompassing more than one Contact. A Broadcast does not represent
any individual message or message content, but rather represents the intent
of the user to send a message to a set of Contacts.

## Msg

The Msg model represents a single incoming or outgoing message to a Contact. It is
tied to a particular Contact, ContactURN and Channel and it maintains the state
of the message within RapidPro. (whether it has been handled yet, sent yet, etc)

Msg is a logical representation of the message, ie, even if the message spans
multiple physical SMS messages, it will be stored as a single Msg object by
RapidPro.

It also contains timing information on when the Msg was created, sent, delivered
as well as any external ids tracked during the sending process.

## Call

The Call model represents an incoming or outgoing Call event. These are created
by the Android relayer when phone calls or made or received on the handset.

## Label

Users may choose to group Msgs using a Label, this Model acts as the foreign key
for those labels.
