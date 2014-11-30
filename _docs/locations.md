---
layout: docs
title: Locations
permalink: /docs/locations/
---

# Locations Module

The Locations stores logic and models associated with administrative boundaries
as used in RapidPro.

## AdminBoundary

The AdminBoundary model represents a single administrative boundary. This can
either by a Country, or one of the two smaller boundaries defined within a country,
what we call States and Districts. All three are represented in this model
along with both their full and simplified geometries for use during
rendering.

## BoundaryAlias

While all organizations use the same AdminBoundary objects for each country, each
may define a set of aliases for those boundaries to ease entry by users. These
are recorded in the BoundaryAlias model.
