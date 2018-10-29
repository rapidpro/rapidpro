# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from django.conf.urls import url
from .views import NotificationViews


urlpatterns = [
    url(r'^notification/list/$', NotificationViews.NotificationList.as_view(), {}, 'flows.notification_list'),
    url(r'^notification/accepted$', NotificationViews.NotificationAccepted.as_view(), {}, 'flows.notification_accepted'),
    url(r'^notification/rejected$', NotificationViews.NotificationRejected.as_view(), {}, 'flows.notification_rejected'),
    url(r'^notification/add_note_to_admin/(?P<pk>[0-9]+)$', NotificationViews.AddNoteToAdmin.as_view(), {}, 'flows.notification_add_note_to_admin'),
    url(r'^notification/add_note_to_user/(?P<pk>[0-9]+)$', NotificationViews.AddNoteToUser.as_view(), {}, 'flows.notification_add_note_to_user'),
    url(r'^notification/add_note_to_admin/$', NotificationViews.AddNoteToAdmin.as_view(), {}, 'flows.notification_add_note_to_admin'),
    url(r'^notification/add_note_to_user/$', NotificationViews.AddNoteToUser.as_view(), {}, 'flows.notification_add_note_to_user'),
    url(r'^notification/flow_changes/(?P<pk>[0-9]+)$', NotificationViews.FlowChanges.as_view(), {}, 'flows.notification_flow_changes'),
    url(r'^notification/flow_changes/$', NotificationViews.FlowChanges.as_view(), {}, 'flows.notification_flow_changes'),
    url(r'^notification/campaign_changes/(?P<pk>[0-9]+)$', NotificationViews.CampaignChanges.as_view(), {}, 'flows.notification_campaign_changes'),
    url(r'^notification/campaign_changes/$', NotificationViews.CampaignChanges.as_view(), {}, 'flows.notification_campaign_changes'),
    url(r'^notification/trigger_changes/(?P<pk>[0-9]+)$', NotificationViews.TriggerChanges.as_view(), {}, 'flows.notification_trigger_changes'),
    url(r'^notification/trigger_changes/$', NotificationViews.TriggerChanges.as_view(), {}, 'flows.notification_trigger_changes')
]
