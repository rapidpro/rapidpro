from __future__ import unicode_literals

from temba.msgs.handler import MessageHandler
from .models import Trigger


class TriggerHandler(MessageHandler):
    def __init__(self):
        super(TriggerHandler, self).__init__('triggers')

    def handle(self, msg):
        (handled, flow) = Trigger.find_and_handle(msg)
        return handled, flow


class CatchAllHandler(MessageHandler):
    def __init__(self):
        super(CatchAllHandler, self).__init__('triggers')

    def handle(self, msg):
        (handled, flow) = Trigger.catch_triggers(msg, Trigger.TYPE_CATCH_ALL, msg.channel)
        return handled, flow
