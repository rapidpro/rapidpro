---
layout: docs
title: Values
permalink: /docs/values/
---

# Values Module

The Values module stores the models and logic used to store and summarize
values collected by RapidPro.

## Value

The Value model is used to store the value of both ContactFields on individual
Contacts and the value collected at a RuleStep. As values have types, the Value
model contains different columns to store the values in a database native format,
not all of these columns will always be set.

The Value model also contains methods to retrieve (and internally cache) summary
statistics for the values on a particular ContactField or RuleSet.
