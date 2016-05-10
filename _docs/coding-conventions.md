---
layout: docs
title: Coding Conventions
permalink: /docs/coding-conventions/
---

# Coding Conventions

Overall we take a pragmatic approach to coding conventions, trying to adhere
to the practices set forth by the Django, Python, Java and Android communities.

## PEP8

Code formatting, variable naming and layout should adhere to
the [PEP8 standard](https://www.python.org/dev/peps/pep-0008).

## DRY - Do not Repeat Yourself

Code blocks should exist once and only once in the system, repetition should
be avoided at all costs.

## KISS - Keep It Simple Stupid

Overall the goal of code should be to communicate it's intent first and foremost,
optimizations for speed and or brevity should only be taken when absolutely
necessary. In a system as complex as RapidPro it is critically important to
only add complexity when absolutely necessary.

## Naming Conventions

In general we attempt to keep the names of models and objects in the code the
same as what is represented to the users, even if that requires significant
refactors and renames as time goes on. This greatly reduces the cognitive load
when trying to understand portions of the codebase. Names should err on the side
of clarify over brevity.

## Locality of Code

In general, try to keep related functionality together if at all possible. The
bias should be towards having related views and forms together as opposed to grouping
all forms and views together by type. This has led to some large files in the
codebase which need to be refactored into smaller components of related functionality
but this is still preferred over having one file containing all forms etc..

## Convention over Configuration

RapidPro makes heavy use of [Smartmin](https://github.com/nyaruka/smartmin) and 
as such heavily relies on convention over configuration. The use of CRUDL objects, 
automatic permission and URL naming greatly reduces the complexity of the system. 
As opportunity arises to create new conventions that yield similar simplifications, 
we should adopt them.

## Code Reviews & Pull Requests

All committed code must be code reviewed via a formal pull request. Whether it is
new feature work or bug fixes, all code should be written on a branch apart from
master, then a pull request should be opened to review those changes before
committing to master. Any code on the master branch is considered ready for live
deployment.

## Test Coverage

RapidPro is built as a hosted platform that continues to evolve on a daily basis.
The two largest deployments of RapidPro deploy new software to their live servers
two to three times per week. This is only possible due to having a very high
coverage rate for our unit tests. All new functionality should have associated
unit tests with full coverage and all bug fixes need to have an associated
unit test demonstration the failure. Though this can feel like a tiring policy,
it has allowed us to continue to evolve the platform and perform the necessary
large refactors with confidence that the system remain stable.
