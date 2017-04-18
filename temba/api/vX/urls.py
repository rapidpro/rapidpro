from __future__ import absolute_import, unicode_literals

from django.conf.urls import url
from .views import FlowEndpoint

urlpatterns = [
    url(r'^flow/$', FlowEndpoint.as_view(), name='api.vX.flow'),
]

