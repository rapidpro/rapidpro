from __future__ import unicode_literals

from temba.msgs.handler import MessageHandler
from .models import Trigger
from temba.contacts.models import ContactField

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
        contact = msg.contact
        contact.add_field_to_contact(
              label=ContactField.UNCAUGHT_LABEL,
              field=ContactField.UNCAUGHT_FIELD,
              value=msg.text,
              org=msg.org)

        return Trigger.catch_triggers(msg, Trigger.TYPE_CATCH_ALL, msg.channel)
