from __future__ import unicode_literals

from django.conf.urls import url
from .views import FlowCRUDL, RuleCRUDL, FlowLabelCRUDL, FlowRunCRUDL, PartialTemplate, FlowAssets

urlpatterns = FlowCRUDL().as_urlpatterns()
urlpatterns += RuleCRUDL().as_urlpatterns()
urlpatterns += FlowLabelCRUDL().as_urlpatterns()
urlpatterns += FlowRunCRUDL().as_urlpatterns()
urlpatterns += [
    url(r'^partials/(?P<template>[a-z0-9\-_]+)$', PartialTemplate.as_view(), name='flows.partial_template'),
    url(r'^flow_assets/(?P<org>\d+)/(?P<type>\w+)$', FlowAssets.as_view(), name='flows.flow_assets'),
    url(r'^flow_assets/(?P<org>\d+)/(?P<type>\w+)/(?P<uuid>[a-z0-9-]{36})$', FlowAssets.as_view(), name='flows.flow_asset'),
]
