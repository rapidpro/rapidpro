# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from django.conf.urls import url
from .views import CallHandler

urlpatterns = [
    url(r'^handle/(?P<pk>\d+)/$', CallHandler.as_view(), name='ivr.ivrcall_handle'),
]
