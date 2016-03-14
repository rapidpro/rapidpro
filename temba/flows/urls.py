from __future__ import unicode_literals

from django.conf.urls import patterns, url
from .views import FlowCRUDL, RuleCRUDL, FlowLabelCRUDL, PartialTemplate

urlpatterns = FlowCRUDL().as_urlpatterns()
urlpatterns += RuleCRUDL().as_urlpatterns()
urlpatterns += FlowLabelCRUDL().as_urlpatterns()

urlpatterns += patterns('', url(r'^partials/(?P<template>[a-z0-9\-_]+)$', PartialTemplate.as_view(), name='flows.partial_template'))
