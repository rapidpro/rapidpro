---
layout: docs
title: Flows
permalink: /docs/flows/
---

# Flows Module

The Flows module contains all logic pertaining to storing and running flows
within RapidPro. As this is the core of the system, it is by far the largest
package, with the most amount of logic.

## Action

The Action base class represents some action that will be performed at that
point in a flow. For example, an action may reply to the Contact within a flow,
or it may send a message to someone else.

An Action can serialize itself to and from JSON.

## ActionSet

An ActionSet is a grouping of one or more Actions along with a link to its
destination step once it is complete. ActionSets are stored in the database,
the Actions contained within serialized as JSON.

## Test

The Test base class represents a Test that can be performed at a step in a flow.
An example Test might be whether the incoming message contains the word "mama".

The Test interface contains methods that return either True or False given an
input of a contact and message.

A Test can serialize itself to and from JSON.

## Rule

A Rule represents the pairing of a Test and an ActionSet destination. This
essentially is the "cause and effect" of a flow, if a Test evaluates to True
then the flow will perform the actions in the linked ActionSet.

## RuleSet

A RuleSet is a grouping of one or more Rules. It represents the branching component
of a Flow. RuleSets are stored in the database, the Rules contained within
serialized as JSON.

## Flow

A Flow represents the grouping of a set of interconnected RuleSets and ActionSets.
Each ActionSet and RuleSet contains a foreign key to its containing flow, along
with their coordinates. In sum those ActionSets and RuleSets make up the
definition of the Flow.

## FlowStep

A FlowStep represents an individual step that a Contact takes through a Flow. A
FlowStep is recorded for every progression through an ActionSet or RuleSet.

## FlowRun

A FlowRun represents the single run of a Contact through a Flow. FlowRuns act
as a foreign key to group individual FlowSteps. A Contact will have one FlowRun
per invocation of a Flow.
