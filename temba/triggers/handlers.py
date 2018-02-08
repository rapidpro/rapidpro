from __future__ import unicode_literals

from temba.msgs.handler import MessageHandler
from .models import Trigger
from temba.contacts.models import ContactField
from temba.flows.models import get_flow_user

class TriggerHandler(MessageHandler):
    def __init__(self):
        super(TriggerHandler, self).__init__('triggers')

    def handle(self, msg):
        return Trigger.find_and_handle(msg)


class CatchAllHandler(MessageHandler):

    def __init__(self):
        super(CatchAllHandler, self).__init__('triggers')

    def handle(self, msg):
        ############ Save last uncaught response from contact ###############
        UNCAUGHT_FIELD = "uncaught_field"
        UNCAUGHT_LABEL = "uncaught-field" 
        contact = msg.contact
        user = get_flow_user(msg.org)
        contact_field = ContactField.objects.filter(
            org=msg.org, key=UNCAUGHT_FIELD).first()
        if not contact_field:
            ContactField.get_or_create(org, user, UNCAUGHT_FIELD,
                                       UNCAUGHT_LABEL)
        contact.set_field(user, UNCAUGHT_FIELD, msg.text)

        return Trigger.catch_triggers(msg, Trigger.TYPE_CATCH_ALL, msg.channel)
