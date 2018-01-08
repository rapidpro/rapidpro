from __future__ import unicode_literals

from django.conf.urls import url
from .views import Home, MessageHistory, RangeDetails

urlpatterns = [
    url(r'^dashboard/home/$', Home.as_view(), {}, 'dashboard.dashboard_home'),
    url(r'^dashboard/message_history/$', MessageHistory.as_view(), {}, 'dashboard.dashboard_message_history'),
    url(r'^dashboard/range_details/$', RangeDetails.as_view(), {}, 'dashboard.dashboard_range_details'),
]
