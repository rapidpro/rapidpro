from django.conf.urls import patterns
from .views import *
from django.conf.urls import patterns, url

urlpatterns = FlowCRUDL().as_urlpatterns()
urlpatterns += RuleCRUDL().as_urlpatterns()
urlpatterns += FlowLabelCRUDL().as_urlpatterns()

urlpatterns += patterns('',
                        url(r'^partials/(?P<template>[a-z0-9\-_]+)$', PartialTemplate.as_view(), name='flows.partial_template'),
)
