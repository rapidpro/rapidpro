from __future__ import unicode_literals

from temba.msgs.handler import MessageHandler
from .models import Trigger, CATCH_ALL_TRIGGER


class TriggerHandler(MessageHandler):
    def __init__(self):
        super(TriggerHandler, self).__init__('triggers')

    def handle(self, msg):
        return Trigger.find_and_handle(msg)


class CatchAllHandler(MessageHandler):
    def __init__(self):
        super(CatchAllHandler, self).__init__('triggers')

    def handle(self, msg):
        return Trigger.catch_triggers(msg, CATCH_ALL_TRIGGER)
