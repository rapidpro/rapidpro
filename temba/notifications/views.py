# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import json
import re
from django.shortcuts import render
from django import forms
from django.core.urlresolvers import reverse

from temba.orgs.views import ModalMixin, OrgObjPermsMixin, OrgPermsMixin
from smartmin.views import (
    SmartListView,
    SmartUpdateView,
    SmartReadView)

from .models import Notification
# Create your views here.

class NotificationViews(OrgObjPermsMixin):

    class NotificationBaseList(SmartListView):
        model = Notification
        title = "Notifications"
        default_order = ('-created_on',)
        refresh = 100000
        fields = ('flow', '')
        default_template = 'notifications/notification_list.html'
        search_fields = ('flow__name__icontains',)
        actions = ('archive', 'label')

        def derive_queryset(self, *args, **kwargs):
            return super(NotificationViews.NotificationBaseList, self).derive_queryset(*args, **kwargs)

        def get_context_data(self, **kwargs):
                context = super(NotificationViews.NotificationBaseList, self).get_context_data(**kwargs)
                context['folders'] = self.get_folders()
                context['object_list'] = self.derive_queryset()
                context['is_validating'] = False
                context['CAMPAIGN_TYPE'] = Notification.CAMPAIGN_TYPE
                context['EVENT_TYPE'] = Notification.EVENT_TYPE
                context['FLOW_TYPE'] = Notification.FLOW_TYPE
                context['TRIGGER_TYPE'] = Notification.TRIGGER_TYPE
                return context

        def get_folders(self):
            org = self.request.user.get_org()
            return [
                dict(label="In process to validate", url=reverse('notifications.notification_list'),
                     count=Notification.objects.filter(reviewed = False, org_orig=org, auto_migrated = False).count()),
                dict(label="Automatic Accepted", url=reverse('notifications.notification_autoaccepted'),
                     count=Notification.objects.filter(reviewed = False,
                                                       org_orig=org,
                                                       auto_migrated=True).count()),
                dict(label="Accepted", url=reverse('notifications.notification_accepted'),
                     count=Notification.objects.filter(reviewed = True,
                                                       org_orig=org,
                                                       accepted=True).count()),
                dict(label="Rejected", url=reverse('notifications.notification_rejected'),
                     count=Notification.objects.filter(reviewed = True,
                                                       org_orig=org,
                                                       accepted=False).count())
            ]


    class NotificationList(NotificationBaseList):
        title = "Notifications"

        def get_context_data(self, **kwargs):
            context = super(NotificationViews.NotificationList, self).get_context_data(**kwargs)
            context['is_validating'] = True
            return context

        def derive_queryset(self, *args, **kwargs):
            org = self.request.user.get_org()
            return super(NotificationViews.NotificationList, self).derive_queryset(*args, **kwargs).filter(
                        reviewed = False,
                        org_orig=org,
                        auto_migrated = False).order_by("created_on")


    class NotificationAccepted(NotificationBaseList):
        default_order = ('-created_on',)

        def derive_queryset(self, *args, **kwargs):
            org = self.request.user.get_org()
            return super(NotificationViews.NotificationAccepted, self).derive_queryset(*args, **kwargs).filter(reviewed = True,
                            org_orig=org,
                            accepted=True).order_by("created_on")


    class NotificationAutoaccepted(NotificationBaseList):
        default_order = ('-created_on',)

        def derive_queryset(self, *args, **kwargs):
            org = self.request.user.get_org()
            return super(NotificationViews.NotificationAutoaccepted, self).derive_queryset(*args, **kwargs).filter(reviewed = False,
                             org_orig=org,
                             auto_migrated=True).order_by("created_on")


    class NotificationRejected(NotificationBaseList):
        default_order = ('-created_on',)

        def derive_queryset(self, *args, **kwargs):
            org = self.request.user.get_org()
            return super(NotificationViews.NotificationRejected, self).derive_queryset(*args, **kwargs).filter(reviewed = True,
                            org_orig=org,
                            accepted=False).order_by("created_on")


    class AddNoteToAdmin(ModalMixin, SmartUpdateView):
        class NoteForm(forms.ModelForm):
            class Meta:
                model = Notification
                fields = ('id','note_orig',)
                widgets = {'note_orig':
                            forms.Textarea(attrs ={'style':"height: 150px"})
                          }

        form_class = NoteForm
        success_url = '@notifications.notification_list'
        success_message = 'Nota agregada o modificada'
        model = Notification


    class AddNoteToUser(ModalMixin, SmartUpdateView):
        class NoteForm(forms.ModelForm):
            class Meta:
                model = Notification
                fields = ('id','note_dest',)
                widgets = {'note_dest':
                            forms.Textarea(attrs ={'style':"height: 150px"})
                          }
        form_class = NoteForm
        success_url = '@notifications.notification_list'
        success_message = ''
        model = Notification


    class ValidChanges(SmartListView):
        model = Notification
        default_template = "notifications/notification_valid.haml"

        def get_context_data(self, **kwargs):
            from temba.flows.models import Flow
            from temba.triggers.models import Trigger
            from temba.campaigns.models import CampaignEvent
            context = super(NotificationViews.ValidChanges, self).get_context_data(**kwargs)
            flows_to_check = context["url_params"]
            flows_to_check = flows_to_check.replace("%2C","&")
            flows_to_check = flows_to_check.split("=")
            if len(flows_to_check)<=1:
                return context
            flows_to_check = flows_to_check[1].split("&")
            flows_to_check = [flow for flow in flows_to_check if flow]
            invalid = []
            valid = []
            for flow_id in flows_to_check:
                c_event = CampaignEvent.objects.filter(flow__pk = flow_id).first()
                trigger = Trigger.objects.filter(flow__pk = flow_id).first()
                flow = Flow.objects.filter(pk=flow_id).first()
                if not flow:
                    continue
                if (not c_event or c_event.campaign.is_archived) and \
                   (not trigger or trigger.is_archived):
                    invalid.append(flow.name)
                else:
                    valid.append(flow.name)

            context["invalid"] = invalid
            context["valid"] = valid
            return context


    class CampaignChanges(SmartReadView):
        model = Notification
        default_template = 'notifications/notification_campaign.html'

        def get_context_data(self, **kwargs):
            from temba.campaigns.models import Campaign
            context = super(NotificationViews.CampaignChanges, self).get_context_data(**kwargs)
            this_notification = self.model.objects.get(
                pk=self.kwargs['pk'])
            changes = {"added":[], "deleted":[]}
            if this_notification.history_dump:
                try:
                    changes = json.loads(this_notification.history_dump)
                except ValueError as e:
                    pass
            added = [c.split("|") for c in changes["added"]]
            deleted = [c.split("|") for c in changes["deleted"]]
            context['added_actions'] = added
            context['deleted_actions'] = deleted
            context["with_changes"] = added or deleted
            return context


    class TriggerChanges(SmartReadView):
        model = Notification
        default_template = 'notifications/notification_trigger.html'

        def get_context_data(self, **kwargs):
            from temba.triggers.models import Trigger
            context = super(NotificationViews.TriggerChanges, self).get_context_data(**kwargs)
            this_notification = self.model.objects.get(
                pk=self.kwargs['pk'])
            changes = {"added":[], "deleted":[]}
            if this_notification.history_dump:
                try:
                    changes = json.loads(this_notification.history_dump)
                    print(changes)
                except ValueError as e:
                    pass
            added = [c.split("|") for c in changes["added"]]
            deleted = [c.split("|") for c in changes["deleted"]]
            context['added_actions'] = added
            context['deleted_actions'] = deleted
            return context

    class FlowChanges(SmartReadView):
        model = Notification
        default_template = 'notifications/notification_flow.html'

        def get_context_data(self, **kwargs):
            context = super(NotificationViews.FlowChanges, self).get_context_data(**kwargs)

            this_notification = self.model.objects.get(
                pk=self.kwargs['pk'])
            production_f = Flow.objects.filter(
                org =this_notification.org_dest,
                name = this_notification.item_name).last()
            added=[("NEW FLOW","")]
            deleted=None
            if production_f:
                added,deleted = self.action_changes(
                    this_notification.history.definition,
                    production_f.as_json())
            context['flow_is_archived'] = production_f.is_archived if production_f else False
            context['added_actions'] = added
            context['deleted_actions'] = deleted
            return context

        def to_string_action(self, action):
            this_type = action["type"]
            str_value = ""
            if this_type == ReplyAction.TYPE:
                msg_item = action["msg"]
                while (type(msg_item) is dict) and msg_item.keys():
                    key = list(msg_item.keys())[0]
                    msg_item = msg_item[key]
                    str_value = str(msg_item)
            elif this_type == AddLabelAction.TYPE:
                str_value = ' '.join(l["name"] for l in action["labels"])
            elif this_type == SetLanguageAction.TYPE:
                str_value = action["name"]
            elif action["type"] ==StartFlowAction.TYPE:
                str_value = action["flow"]["name"]
            elif action["type"] ==SaveToContactAction.TYPE:
                str_value = action["label"] + action["value"]
            elif action["type"] == SetChannelAction.TYPE:
                str_value = action["name"]
            elif action["type"] == EmailAction.TYPE:
                recipients = ','.join(action["emails"])
                str_value = "To -> "+recipients + \
                            "Msg->"+ action["msg"]
            elif action["type"] == WebhookAction.TYPE:
                str_value = action["webhook"]
            elif action["type"] ==AddToGroupAction.TYPE:
                str_value = ','.join([g["name"] \
                                      for g in action["groups"]])
            elif action["type"] ==DeleteFromGroupAction.TYPE:
                str_value = ','.join([g["name"] \
                                      for g in action["groups"]])
                str_value = str_value if str_value else "All"
            elif action["type"] == TriggerFlowAction.TYPE:
                str_value = action["flow"]["name"]
            if action["type"] ==SendAction.TYPE:
                if action["contacts"]:
                    str_value += "To contacts ->"+\
                             ','.join(action["contacts"])
                if action["groups"]:
                    str_value += "|ToGroups->" + \
                                 ','.join([g["name"] \
                                           for g in action["groups"]])
            return  (this_type,str_value)

        def add_action_string(self, flow):
            changes = []
            for item_set in flow["action_sets"]:
                for action in item_set["actions"]:
                    action["to_string"] = self.to_string_action(action)
                    changes.append(action["to_string"])
            return changes

        def action_changes(self, new_flow, old_flow):
            STRING_VALUE = 1
            new_actions = self.add_action_string(new_flow)
            old_actions = self.add_action_string(old_flow)
            added_actionset = []
            deleted_actionset = []
            for item_set in old_flow["action_sets"]:
                deleted_actions = []
                for action in item_set["actions"]:
                    if not action["to_string"][STRING_VALUE] in\
                       [i[STRING_VALUE] for i in new_actions]:
                        deleted_actions.append(action["to_string"])
                if deleted_actions:
                    deleted_actionset += deleted_actions
            for item_set in new_flow["action_sets"]:
                added_actions = []
                for action in item_set["actions"]:
                    if not action["to_string"][STRING_VALUE] in \
                       [i[STRING_VALUE] for i in old_actions]:
                        added_actions.append(action["to_string"])
                if added_actions:
                    added_actionset += added_actions
            added_actionset = sorted(added_actionset,
                                     key=lambda x: x[0])
            deleted_actionset = sorted(deleted_actionset,
                                       key=lambda x: x[0])
            return (added_actionset, deleted_actionset)
