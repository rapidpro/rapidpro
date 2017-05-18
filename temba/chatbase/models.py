from __future__ import unicode_literals

import json
import requests
import time

from django.conf import settings
from django.db import models
from smartmin.models import SmartModel
from temba.channels.models import Channel
from temba.contacts.models import Contact
from temba.msgs.models import Msg
from temba.orgs.models import Org, CHATBASE_TYPE, CHATBASE_API_KEY, CHATBASE_FEEDBACK, CHATBASE_NOT_HANDLED, \
    CHATBASE_VERSION


class Chatbase(SmartModel):
    PENDING = 'P'
    SUCCESS = 'S'
    FAILED = 'F'

    STATUS_CHOICES = ((PENDING, "Pending"),
                      (SUCCESS, "Success"),
                      (FAILED, "Failed"))

    org = models.ForeignKey(Org, help_text="The organization that this chatbase was triggered for")

    status = models.CharField(max_length=1, choices=STATUS_CHOICES, default='P',
                              help_text="The state this event is currently in")

    channel = models.ForeignKey(Channel, null=True, blank=True,
                                help_text="The channel that this chatbase is relating to")

    msg = models.ForeignKey(Msg, help_text="The msg that this chatbase is sent to")

    contact = models.ForeignKey(Contact, help_text="The contact that this chatbase is sent to")

    data = models.TextField(null=True, blank=True, default="")

    response = models.TextField(null=True, blank=True, default="")

    message = models.CharField(max_length=255, null=True, blank=True,
                               help_text="A message describing the end status, error messages go here")

    @classmethod
    def create(cls, org, channel, msg, contact):
        from temba.api.models import get_api_user
        api_user = get_api_user()
        chatbase_event = Chatbase.objects.create(org_id=org, channel_id=channel, msg_id=msg, contact_id=contact,
                                                 created_by=api_user, modified_by=api_user)
        return chatbase_event

    def trigger_chatbase_event(self):
        if not settings.SEND_CHATBASE:
            raise Exception("!! Skipping Chatbase request, SEND_CHATBASE set to False")

        config = self.org.config_json()

        data = dict(api_key=config.get(CHATBASE_API_KEY),
                    type=config.get(CHATBASE_TYPE),
                    user_id=self.contact.uuid,
                    platform=self.channel.name,
                    not_handled=config.get(CHATBASE_NOT_HANDLED),
                    message=self.msg.text,
                    feedback=config.get(CHATBASE_FEEDBACK),
                    time_stamp=int(time.time()),
                    version=config.get(CHATBASE_VERSION))

        response = requests.post(settings.CHATBASE_API_URL, data)

        self.data = json.dumps(data, indent=2)
        self.response = response.content

        response = json.loads(response.content)

        if response.get('status') == 200:
            self.status = self.SUCCESS
            self.message = "Message ID: %s" % response.get('message_id')
        else:
            self.status = self.FAILED
            self.message = "%s" % response.content.get('reason')

        self.save()

        return response

    @classmethod
    def create_and_fire(cls, org, channel, msg, contact):
        org_obj = None
        if type(org).__name__ == 'int':
            org_obj = Org.objects.filter(id=org).first()

        if org_obj and org_obj.is_connected_to_chatbase():
            chatbase_args = dict(org=org,
                                 channel=channel,
                                 msg=msg,
                                 contact=contact)
            chatbase = Chatbase.create(**chatbase_args)
            chatbase.trigger_chatbase_event()
