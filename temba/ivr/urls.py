from __future__ import unicode_literals

from django.conf.urls import patterns, url
from .views import CallHandler

urlpatterns = patterns('ivr.views',
                       url(r'^handle/(?P<pk>\d+)/$', CallHandler.as_view(), name='ivr.ivrcall_handle'))
