---
layout: docs
title: Schedules
permalink: /docs/schedules/
---

# Schedules Module

The Schedules module provides a model to schedule events for sometime in the
future.

## Schedule

The Schedule model records when the user would like an event to take place, as
well as any repetition schedule that the user has picked. It also takes care of
calculating the next time an event should fire (if any).

The Schedule model does not in of itself perform any action, rather it is
referred to by Triggers and Broadcasts.
