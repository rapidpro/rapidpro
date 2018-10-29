# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.shortcuts import render

# Create your views here.

class NotificationViews(OrgObjPermsMixin):


    class NotificationBaseList(SmartListView):
        model = Notification
        title = _("Notifications")
        default_order = ('-created_on',)
        refresh = 10000
        fields = ('flow', '')
        default_template = 'flows/notification_list.html'
        search_fields = ('flow__name__icontains',)
        actions = ('archive', 'label')

        def derive_queryset(self, *args, **kwargs):
            org = self.request.user.get_org()
            return super(NotificationViews.NotificationBaseList, self).derive_queryset(*args, **kwargs)

        def get_context_data(self, **kwargs):
                context = super(NotificationViews.NotificationBaseList, self).get_context_data(**kwargs)
                context['folders'] = self.get_folders()
                context['object_list'] = self.derive_queryset()
                context['is_validating'] = False
                context['CAMPAIGN_TYPE'] = Notification.CAMPAIGN_TYPE
                context['FLOW_TYPE'] = Notification.FLOW_TYPE
                context['TRIGGER_TYPE'] = Notification.TRIGGER_TYPE
                return context

        def get_folders(self):
            org = self.request.user.get_org()
            return [
                dict(label="In process to validate", url=reverse('flows.notification_list'),
                     count=Notification.objects.filter(is_active = True, org_orig=org).count()),
                dict(label="Accepted", url=reverse('flows.notification_accepted'),
                     count=Notification.objects.filter(is_active = False,
                                                       read = False,
                                                       org_orig=org,
                                                       accepted=True).count()),
                dict(label="Rejected", url=reverse('flows.notification_rejected'),
                     count=Notification.objects.filter(is_active = False,
                                                       read = False,
                                                       org_orig=org,
                                                       accepted=False).count())
            ]

    class NotificationList(NotificationBaseList):
        title = _("Notifications")
        def derive_queryset(self, *args, **kwargs):
            org = self.request.user.get_org()
            return super(NotificationViews.NotificationList, self).derive_queryset(*args, **kwargs).filter(is_active = True,
                                                                                                           org_orig=org)
        def get_context_data(self, **kwargs):
            context = super(NotificationViews.NotificationList, self).get_context_data(**kwargs)
            context['is_validating'] = True
            return context

    class NotificationAccepted(NotificationBaseList):
        default_order = ('-created_on',)

        def derive_queryset(self, *args, **kwargs):
            org = self.request.user.get_org()
            return super(NotificationViews.NotificationAccepted, self).derive_queryset(*args, **kwargs).filter(is_active = False,
                                                                                                               read = False,
                                                                                                               org_orig=org,
                                                                                                               accepted=True)


    class NotificationRejected(NotificationBaseList):
        default_order = ('-created_on',)

        def derive_queryset(self, *args, **kwargs):
            org = self.request.user.get_org()
            return super(NotificationViews.NotificationRejected, self).derive_queryset(*args, **kwargs).filter(is_active = False,
                                                                                                               read = False,
                                                                                                               org_orig=org,
                                                                                                               accepted=False)
    class AddNoteToAdmin(ModalMixin, SmartUpdateView):
        class NoteForm(forms.ModelForm):
            class Meta:
                model = Notification
                fields = ('id','note_orig',)
                widgets = {'note_orig':
                           forms.Textarea(
                               attrs ={'style':"height: 150px"})
                }

        form_class = NoteForm
        success_url = '@flows.notification_list'
        success_message = ''
        model = Notification

    class AddNoteToUser(ModalMixin, SmartUpdateView):
        class NoteForm(forms.ModelForm):
            class Meta:
                model = Notification
                fields = ('id','note_dest',)
                widgets = {'note_dest':
                           forms.Textarea(
                               attrs ={'style':"height: 150px"})
                }

        form_class = NoteForm
        success_url = '@flows.notification_list'
        success_message = ''
        model = Notification


    class CampaignChanges(SmartReadView):
        model = Notification
        default_template = 'flows/notification_campaign.html'

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
            added = self.parse_events(changes["added"])
            deleted = self.parse_events(changes["deleted"])
            context['added_actions'] = added
            context['deleted_actions'] = deleted
            return context

        def parse_events(self, list_work):
            result = []
            for c in list_work:
                items = c.split("|")
                r = ""
                o = ""
                a = ""
                for item in items:
                    tmp=":".join(item.split(":")[1:])
                    if "relative_to" in item:
                        r = tmp
                    elif "offset"  in item:
                        o = tmp
                    elif "action" in item:
                        a = tmp
                result.append((r,o,a))
            return result

    class TriggerChanges(SmartReadView):
        model = Notification
        default_template = 'flows/notification_trigger.html'

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
                    print ("Error")
                    pass
            print (changes)
            added = self.parse_events(changes["added"])
            deleted = self.parse_events(changes["deleted"])
            context['added_actions'] = added
            context['deleted_actions'] = deleted
            return context

        def parse_events(self, list_work):
            result = []
            for c in list_work:
                items = c.split("|")
                k = ""
                t = ""
                f = ""
                g = ""
                for item in items:
                    tmp=":".join(item.split(":")[1:])
                    if "Keyword" in item:
                        k = tmp
                    elif "Type"  in item:
                        t = tmp
                    elif "Flow" in item:
                        f = tmp
                    elif "Group" in item:
                        g = tmp
                result.append((k,t,f,g))
            return result


    class FlowChanges(SmartReadView):
        model = Notification
        default_template = 'flows/notification_flow.html'

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
