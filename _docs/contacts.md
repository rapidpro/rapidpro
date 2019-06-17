---
layout: docs
title: Contacts
permalink: /docs/contacts/
---

# Contacts Module

The Contacts module contains all logic related to users which are interacting
with the system via a Channel.

## ContactURN

A ContactURN represents an address for a contact. As all URN's, ContactURNs
are composed of a scheme and path which varies depending on the type of address.
For SMS and IVR interactions, the scheme is ```tel``` and a typical ContactURN
might look like ```tel:+250788123123```

Internally RapidPro stores all numbers in E164 format and uses the convention
that any number that starts with a ```+``` is a number that has been deemed
valid by the [phonenumbers](https://github.com/daviddrysdale/python-phonenumbers)
library.

Other scheme types supported by RapidPro include `facebook`, `twitterid`,
`viber`, `line`, `telegram`, `external`, `jiochat`, `wechat` and `whatsapp`.

## Contact

A Contact represents a logical user that interacts with the system. A single
contact may have more than one ContactURN of differing schemes, such as one
phone number and one Twitter handle.

## ContactField

Users can choose to add fields to the Contact on their organization, those fields
are represented by this Model, which stores the label of the field, it's key
(which is used in Flows) and type.

## ContactGroup

ContactGroups represent a grouping of Contacts with a label by the user.
ContactGroup may either by defined by the user adding or removing
users to it or by a query.
